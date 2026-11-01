"""Realtime voice over Codex's documented WebRTC transport.

Why WebRTC with our own peer: the websocket transport needs API-key auth (ChatGPT sign-in
is refused), and the TUI's bundled codex-voice-host speaks a private protocol. With
WebRTC the client owns the RTCPeerConnection: one audio track (mic) plus the
`oai-events` data channel; the SDP answer arrives as `thread/realtime/sdp`. Realtime
protocol v3 is required. Spoken requests are delegated to real Codex turns by the
app-server itself (`delegation.created` -> `handoff_request` -> `turn/started`).
"""
from __future__ import annotations

import asyncio
import fractions
import json
import logging
from typing import Callable

import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from av import AudioFrame

from .audio import EC_SOURCE, FRAME, RATE, EchoCanceller, Microphone, Speaker

log = logging.getLogger(__name__)


class MicTrack(MediaStreamTrack):
    """Sends the mic, or silence while muted — the agent stops hearing you, as with F9."""
    kind = "audio"

    def __init__(self, frames: asyncio.Queue, is_muted: Callable[[], bool]):
        super().__init__()
        self.frames = frames  # shared with whichever Microphone is current
        self.is_muted = is_muted
        self.pts = 0

    async def recv(self) -> AudioFrame:
        pcm = await self.frames.get()
        if self.is_muted():
            pcm = np.zeros(FRAME, dtype=np.int16)
        frame = AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = RATE
        frame.pts = self.pts
        frame.time_base = fractions.Fraction(1, RATE)
        self.pts += FRAME
        return frame


class VoiceLink:
    def __init__(self, app, thread_id: str, *, is_muted: Callable[[], bool],
                 on_event: Callable[[dict], None], on_audio: Callable[[np.ndarray], None],
                 echo_cancel: bool = True, voice: str | None = None, briefing: str | None = None,
                 input_device: str | None = None, on_lost: Callable[[str], None] | None = None):
        self.app = app
        self.thread_id = thread_id
        self.is_muted = is_muted
        self.on_event = on_event
        self.on_audio = on_audio
        self.echo_cancel = echo_cancel
        self.voice = voice
        self.briefing = briefing
        self.input_device = input_device  # PipeWire node name; None = system default
        self.on_lost = on_lost  # told when an established session dies on its own mic
        self.mic_frames: asyncio.Queue = asyncio.Queue(maxsize=25)
        self.aec_active = False
        self._remote_started = False
        self.ec = EchoCanceller()
        self.mic: Microphone | None = None
        self.speaker: Speaker | None = None
        self.pc: RTCPeerConnection | None = None
        self.connected = False
        self._answer: asyncio.Future | None = None
        self._tasks: list[asyncio.Task] = []

    # Called by the daemon for app-server notifications about this thread.
    def handle_notification(self, method: str, params: dict) -> None:
        if method == "thread/realtime/sdp" and self._answer and not self._answer.done():
            self._answer.set_result(params["sdp"])
        elif method == "thread/realtime/error" and self._answer and not self._answer.done():
            self._answer.set_exception(RuntimeError(params.get("message", "realtime error")))

    async def _start_audio(self) -> None:
        """Mic (through the echo canceller when enabled) and speaker (the default output)."""
        loop = asyncio.get_running_loop()
        t_setup = loop.time()
        aec = self.aec_active = bool(self.echo_cancel and await self.ec.start(self.input_device))
        t_ec = loop.time()
        self.mic = Microphone(EC_SOURCE if aec else self.input_device, self.mic_frames,
                              on_exit=lambda: self._lost("the microphone stopped"))
        await self.mic.start()
        if self.speaker is None:
            self.speaker = Speaker()
            self.speaker.start()
        log.debug("voice setup: echo canceller %.2fs, devices %.2fs (mic %s)",
                  t_ec - t_setup, loop.time() - t_ec, self.input_device or "system default")

    def _lost(self, reason: str) -> None:
        if self.connected:
            self.connected = False
            if self.on_lost:
                self.on_lost(reason)

    async def _stop_mic(self) -> None:
        if self.mic:
            await self.mic.stop()
            self.mic = None
        await asyncio.to_thread(self.ec.stop)  # waits up to 3 s for pw-cli; not on the loop

    async def set_input(self, device: str | None) -> None:
        """Switch microphones without dropping the voice session.

        If the new device cannot be opened, the previous one is restored and the error
        re-raised. If even that fails, the link closes itself (connected = False) so the
        next unmute reconnects instead of sitting deaf with a dead microphone.
        """
        previous = self.input_device
        self.input_device = device
        if not self.connected:
            return
        await self._stop_mic()
        try:
            await self._start_audio()
        except Exception:
            log.warning("could not open microphone %s; going back to %s", device, previous or "system default")
            self.input_device = previous
            await self._stop_mic()
            try:
                await self._start_audio()
            except Exception:
                log.error("could not reopen the previous microphone either; closing the voice link")
                await self.close()
            raise
        log.info("microphone switched to %s", device or "system default")

    async def connect(self) -> None:
        """Open the audio devices and the WebRTC session. On any failure everything that
        was started (echo canceller, mic, speaker, peer, remote session) is torn down
        before the error propagates, so nothing is left loaded."""
        self._remote_started = False
        try:
            await self._connect()
        except BaseException:
            await self.close(stop_remote=self._remote_started)
            raise

    async def _connect(self) -> bool:
        await self._start_audio()
        self.pc = RTCPeerConnection()
        self.pc.addTrack(MicTrack(self.mic_frames, self.is_muted))
        channel = self.pc.createDataChannel("oai-events")

        @channel.on("message")
        def _on_message(message):
            try:
                self.on_event(json.loads(message))
            except ValueError:
                pass

        @self.pc.on("track")
        def _on_track(track):
            if track.kind == "audio":
                self._tasks.append(asyncio.create_task(self._play(track)))

        @self.pc.on("connectionstatechange")
        async def _on_state():
            log.info("voice peer: %s", self.pc.connectionState if self.pc else "closed")
            if self.pc and self.pc.connectionState in ("failed", "closed"):
                self._lost(f"connection {self.pc.connectionState}")

        await self.pc.setLocalDescription(await self.pc.createOffer())
        self._answer = asyncio.get_running_loop().create_future()
        params = {"threadId": self.thread_id, "outputModality": "audio", "version": "v3",
                  "transport": {"type": "webrtc", "sdp": self.pc.localDescription.sdp}}
        if self.voice:
            params["voice"] = self.voice
        if self.briefing:
            # v3 only: facts the voice model should know (e.g. how muting really works)
            params["initialItems"] = [{"role": "developer", "text": self.briefing}]
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        self._remote_started = True  # from here on, a failure must also stop the remote session
        await self.app.call("thread/realtime/start", params)
        t1 = loop.time()
        sdp = await asyncio.wait_for(self._answer, 20)
        t2 = loop.time()
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))
        self.connected = True
        log.info("voice connected (echo cancel %s): start %.2fs, answer %.2fs, peer %.2fs",
                 "on" if self.aec_active else "off", t1 - t0, t2 - t1, loop.time() - t2)
        return True

    async def _play(self, track) -> None:
        while True:
            try:
                frame = await track.recv()
            except Exception:
                return
            arr = frame.to_ndarray()
            channels = len(frame.layout.channels)
            pcm = (arr.reshape(-1, channels).mean(axis=1) if channels > 1 else arr.reshape(-1)).astype(np.int16)
            if self.speaker:
                self.speaker.write(pcm)
            self.on_audio(pcm)

    async def close(self, stop_remote: bool = True) -> None:
        self.connected = False
        if stop_remote:
            try:
                await self.app.call("thread/realtime/stop", {"threadId": self.thread_id}, timeout=5)
            except Exception as exc:
                log.debug("realtime/stop: %s", exc)
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        if self.pc:
            await self.pc.close()
            self.pc = None
        await self._stop_mic()
        if self.speaker:
            speaker, self.speaker = self.speaker, None
            await asyncio.to_thread(speaker.stop)
