"""Audio plumbing: PipeWire mic/speaker, the optional echo canceller, and spectrum bands.

Mic and speaker are `pw-record --raw` / `pw-play --raw` child processes carrying 48 kHz
mono s16. (Without --raw, pw-record writes a Sun AU header to stdout.) The echo
canceller is PipeWire's own WebRTC AEC module, loaded through a pw-cli process that is
held open: the module lives exactly as long as that process.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import threading
import time
from collections import deque

import numpy as np

log = logging.getLogger(__name__)

RATE = 48000
FRAME = 960  # 20 ms
BANDS = 24

EC_SOURCE = "codex_voice_ec_source"
EC_SINK = "codex_voice_ec_sink"


def ec_module(capture_target: str | None = None) -> str:
    """pw-cli command loading the echo canceller; its capture side follows the chosen mic.

    monitor.mode: the canceller takes its reference from the speakers' monitor instead of
    putting a sink in the playback path. Routing the voice through the canceller's sink
    distorted it audibly; this way playback goes straight to the real device, and anything
    the speakers play (the agent, a video) is cancelled out of the mic.
    """
    target = f' target.object = "{capture_target}"' if capture_target else ""
    return (
        "load-module libpipewire-module-echo-cancel { library.name = aec/libspa-aec-webrtc "
        "monitor.mode = true node.latency = 1024/48000 "
        f'capture.props = {{ node.name = "codex_voice_ec_capture" node.passive = true{target} }} '
        f'source.props = {{ node.name = "{EC_SOURCE}" node.description = "Codex Voice Agent mic (echo cancelled)" }} '
        f'sink.props = {{ node.name = "{EC_SINK}" node.description = "Codex Voice Agent echo reference" }} }}'
    )


try:  # loaded once here: a forked child may only make the prctl call itself
    import ctypes as _ctypes
    import signal as _signal
    _PRCTL = _ctypes.CDLL("libc.so.6", use_errno=True).prctl
    _SIGTERM = int(_signal.SIGTERM)
except OSError:  # not glibc: children just won't follow us down
    _PRCTL = None


def die_with_parent() -> None:
    """preexec_fn: the kernel sends SIGTERM to this child when the app's process exits,
    so a crash or kill never leaves a stale pw-cli/pw-record/pw-play (and with it an
    echo canceller) behind. It runs between fork and exec in a multi-threaded parent, so
    it does nothing but one already-resolved libc call (no imports, no allocation)."""
    if _PRCTL is not None:
        _PRCTL(1, _SIGTERM)  # PR_SET_PDEATHSIG


# ---------------------------------------------------------------- input devices
def parse_inputs(dump: list) -> dict:
    """Microphones from `pw-dump` output: {"default": node name, "inputs": [{id, name}]}.
    Our own echo-cancelled source and monitor sources are left out."""
    default = None
    inputs = []
    for obj in dump:
        if obj.get("type") == "PipeWire:Interface:Metadata" and (obj.get("props") or {}).get("metadata.name") == "default":
            for entry in obj.get("metadata") or []:
                if entry.get("key") == "default.audio.source":
                    value = entry.get("value")
                    default = value.get("name") if isinstance(value, dict) else value
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") not in ("Audio/Source", "Audio/Source/Virtual"):
            continue
        name = props.get("node.name") or ""
        if not name or name.startswith(("codex_voice_ec", "copilot_ec")) or name.endswith(".monitor"):
            continue
        inputs.append({"id": name, "name": props.get("node.description") or props.get("node.nick") or name})
    inputs.sort(key=lambda d: d["name"].lower())
    return {"default": default, "inputs": inputs}


async def list_inputs() -> dict:
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec("pw-dump", stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 5)
        return parse_inputs(json.loads(out or b"[]"))
    except (OSError, ValueError, asyncio.TimeoutError) as exc:
        log.warning("could not list microphones: %s", exc)
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        return {"default": None, "inputs": []}


def spectrum_bands(window: np.ndarray, n: int = BANDS, lo: float = 90.0, hi: float = 7500.0) -> list[float]:
    """Log-spaced band levels in 0..1 for the voice visualizer (speech lives in ~90 Hz-7.5 kHz)."""
    if window.size == 0:
        return [0.0] * n
    w = window.astype(np.float64) * np.hanning(window.size)
    spec = np.abs(np.fft.rfft(w))
    freqs = np.fft.rfftfreq(window.size, 1 / RATE)
    edges = np.geomspace(lo, hi, n + 1)
    levels = []
    for a, b in zip(edges[:-1], edges[1:]):
        sel = spec[(freqs >= a) & (freqs < b)]
        mag = float(sel.mean()) if sel.size else 0.0
        db = 20 * np.log10(mag + 1e-9)
        # ~60 dB (quiet) .. ~100 dB (loud speech) mapped to 0..1
        levels.append(round(float(np.clip((db - 60) / 40, 0, 1)), 3))
    return levels


def peak(pcm: np.ndarray) -> float:
    return float(np.abs(pcm).max()) / 32768.0 if pcm.size else 0.0


class EchoCanceller:
    def __init__(self):
        self.proc: subprocess.Popen | None = None

    @property
    def active(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    async def start(self, capture_target: str | None = None) -> bool:
        if self.active:
            return True
        if not shutil.which("pw-cli"):
            log.warning("pw-cli not found; echo cancellation off")
            return False
        self.proc = subprocess.Popen(["pw-cli"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, text=True, preexec_fn=die_with_parent)
        assert self.proc.stdin
        self.proc.stdin.write(ec_module(capture_target) + "\n")
        self.proc.stdin.flush()
        for _ in range(30):  # wait up to 3 s for the nodes to appear
            await asyncio.sleep(0.1)
            if await self._nodes_present():
                return True
        log.warning("echo-cancel nodes did not appear; continuing without them")
        self.stop()
        return False

    @staticmethod
    async def _nodes_present() -> bool:
        proc = await asyncio.create_subprocess_exec(
            "pw-cli", "ls", "Node", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        text = out.decode(errors="replace")
        return EC_SOURCE in text

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


class Microphone:
    """Reads 20 ms PCM frames from pw-record into a bounded queue (old frames dropped)."""

    def __init__(self, target: str | None = None, frames: asyncio.Queue | None = None,
                 on_exit=None):
        self.target = target
        self.on_exit = on_exit  # called if pw-record ends while we still want it
        self._stopping = False
        self.proc: asyncio.subprocess.Process | None = None
        # The queue can outlive this Microphone, so switching devices keeps the
        # WebRTC track reading from the same place.
        self.frames: asyncio.Queue[np.ndarray] = frames if frames is not None else asyncio.Queue(maxsize=25)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        args = ["pw-record", "--raw", "--rate", str(RATE), "--channels", "1", "--format", "s16"]
        if self.target:
            args += ["--target", self.target]
        self.proc = await asyncio.create_subprocess_exec(
            *args, "-", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True, preexec_fn=die_with_parent)
        self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        need = FRAME * 2
        while True:
            try:
                data = await self.proc.stdout.readexactly(need)
            except (asyncio.IncompleteReadError, ConnectionError):
                if not self._stopping and self.on_exit:
                    self.on_exit()
                return
            if self.frames.full():
                self.frames.get_nowait()  # never let latency build up
            self.frames.put_nowait(np.frombuffer(data, dtype=np.int16).copy())

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), 3)
            except (ProcessLookupError, asyncio.TimeoutError):
                pass


class JitterBuffer:
    """Smooths bursty network audio into a steady 20 ms stream.

    Frames are pushed as they arrive (in bursts: gaps of 100-230 ms were measured on
    a VM with NAT networking). pop() is called once per 20 ms tick and returns the next frame, or
    None (play silence) while the buffer is (re)filling. Playback starts only once
    PREFILL is queued; if the buffer runs dry it refills before playing again, and a
    backlog beyond MAX is trimmed so latency cannot creep up.
    """

    PREFILL = 8   # frames (160 ms) queued before playback (re)starts
    MAX = 20      # frames (400 ms): trim older audio beyond this

    def __init__(self):
        self._frames: deque[np.ndarray] = deque()
        self._lock = threading.Lock()
        self.playing = False
        self.underruns = 0
        self.trimmed = 0

    def push(self, pcm: np.ndarray) -> None:
        with self._lock:
            self._frames.append(pcm)
            while len(self._frames) > self.MAX:
                self._frames.popleft()
                self.trimmed += 1

    def pop(self) -> np.ndarray | None:
        with self._lock:
            if not self.playing:
                if len(self._frames) < self.PREFILL:
                    return None
                self.playing = True
            if not self._frames:
                self.playing = False
                self.underruns += 1
                return None
            return self._frames.popleft()

    def __len__(self) -> int:
        return len(self._frames)


class Speaker:
    """pw-play fed one 20 ms frame every 20 ms from a JitterBuffer, by its own thread.

    Writing frames to pw-play as they arrive (in bursts) produced audible dropouts even
    with audio queued ahead; a steadily paced stream did not. The thread also keeps the
    blocking pipe writes off the event loop.
    """

    def __init__(self, target: str | None = None):
        self.target = target
        self.proc: subprocess.Popen | None = None
        self.buffer = JitterBuffer()
        self._thread: threading.Thread | None = None
        self._running = threading.Event()

    def start(self) -> None:
        args = ["pw-play", "--raw", "--rate", str(RATE), "--channels", "1", "--format", "s16"]
        if self.target:
            args += ["--target", self.target]
        self.proc = subprocess.Popen(args + ["-"], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     start_new_session=True, preexec_fn=die_with_parent)
        self._running.set()
        self._thread = threading.Thread(target=self._pace, name="codex-voice-speaker", daemon=True)
        self._thread.start()

    def write(self, pcm: np.ndarray) -> bool:
        """Queue one decoded frame (any length; normally 20 ms) for playback."""
        if not self._running.is_set():
            return False
        self.buffer.push(pcm.astype(np.int16))
        return True

    def _pace(self) -> None:
        silence = bytes(FRAME * 2)
        period = FRAME / RATE
        next_tick = time.monotonic()
        while self._running.is_set():
            frame = self.buffer.pop()
            data = silence if frame is None else frame.tobytes()
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (BrokenPipeError, ValueError, AttributeError, OSError):
                return
            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.2:  # fell far behind (suspend, load): resync the clock
                next_tick = time.monotonic()

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None
        if self.proc and self.proc.poll() is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
        self.proc = None
