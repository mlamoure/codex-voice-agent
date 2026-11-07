"""The codex-voice-agent backend: one Codex session, a voice link, and a control socket.

Control protocol (newline-delimited JSON on $XDG_RUNTIME_DIR/codex-voice-agent.sock):
  request  {"id": <any>, "cmd": "<name>", ...args}
  reply    {"id": <same>, "ok": true, ...} | {"id": <same>, "ok": false, "error": "..."}
  event    {"event": "<name>", ...}   (only after {"cmd": "subscribe"})
Commands: state, subscribe, history, mute {value: "toggle"|true|false}, set_model {model},
list_inputs, set_input {device: node name | null},
set_effort {effort}, set_reply_card {value}, new_session, send {text}, approve_denied {reviewId}.
Events: state, history_reset, item, reply, spectrum.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from collections import deque
from pathlib import Path

import numpy as np

from . import __version__, update
from .dictation import watch_superwhisper
from .appserver import AppServer, AppServerError
from .render import markdown_html
from .audio import peak, spectrum_bands
from .config import Config, State, socket_path

log = logging.getLogger(__name__)

HISTORY_LIMIT = 500
MAX_REQUEST = 1 << 20       # longest accepted control-socket line (bytes)
SPEAKING_HOLD = 0.35        # seconds of quiet before "speaking" ends
SPECTRUM_INTERVAL = 1 / 30  # visualizer frame rate
LOUD = 0.012                # peak level that counts as the agent speaking
UPDATE_FIRST_CHECK = 60      # seconds after start (the network may still be coming up)
UPDATE_INTERVAL = 24 * 3600  # then once a day
DELEGATION_RE = re.compile(r"<realtime_delegation>.*?<input>(.*?)</input>", re.S)

# Spoken mute commands. The voice model cannot mute the app itself (it would only
# claim to), so the daemon watches the user's own transcribed words. Only phrases
# aimed at the agent count: "mute the Sonos" must not mute it.
# The whole utterance must be the command (fillers aside): "I can't stop listening to this
# album" or "tell the kids to go to sleep" must not mute the agent.
_FILLER_START = re.compile(r"^(?:(?:ok|okay|alright|hey|please|now|codex|agent|assistant)\s+)+")
_FILLER_END = re.compile(r"(?:\s+(?:please|now|for now|thanks|thank you|for a bit|for a while))+$")
_MUTE_COMMAND = re.compile(
    r"(?:(?:can|could|would|will) you\s+(?:please\s+)?)?"
    r"(?:mute(?:\s+(?:yourself|your\s*self|the\s+(?:voice\s+)?(?:assistant|agent)|codex|copilot|the\s+mic))?"
    r"|stop\s+listening|go\s+to\s+sleep|go\s+quiet|sleep)")
# Questions *about* muting ("are you able to mute yourself if I ask") are not commands.
_ABOUT_MUTING = re.compile(r"\b(?:are you able|is it possible|if i (?:ask|say|tell)|how (?:do|can|would) i|"
                           r"what happens|would you be able|do you know how)\b")


def is_mute_command(text: str) -> bool:
    t = re.sub(r"[^\w\s']", " ", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    if not t or _ABOUT_MUTING.search(t):
        return False
    t = _FILLER_END.sub("", _FILLER_START.sub("", t)).strip()
    return bool(_MUTE_COMMAND.fullmatch(t))


# Told to the voice model at session start so it describes its controls truthfully.
VOICE_BRIEFING = (
    "Facts about this voice session (codex-voice-agent, running in the user's desktop bar): "
    "the user mutes you with their mute key, the bar icon, or by saying 'mute yourself', 'stop listening' "
    "or 'go to sleep' - the app mutes the microphone by itself as soon as it hears one of those, so "
    "acknowledge in a few words ('Muting now.') and do not delegate it. While muted you hear nothing, "
    "so you cannot be unmuted by voice: the user presses their mute key or clicks the bar icon. Do not claim to "
    "control the microphone, the app or the computer in any other way."
)


_osd_children: list[subprocess.Popen] = []


def osd(muted: bool | None, message: str | None = None) -> None:
    """Omarchy's on-screen display (the same one volume and brightness use).

    Fire and forget: the child is spawned before this returns, so it also works from the
    short-lived CLI. Finished children are reaped on the next call, so the daemon never
    accumulates zombies, and a failure to start is logged, never raised.
    """
    _osd_children[:] = [p for p in _osd_children if p.poll() is None]
    if not shutil.which("omarchy-osd"):
        return
    icon = "microphone-muted" if muted else "microphone"
    text = message or ("Voice assistant muted" if muted else "Voice assistant unmuted")
    try:
        _osd_children.append(subprocess.Popen(
            ["omarchy-osd", "-i", icon, "-m", text],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True))
    except OSError as exc:
        log.debug("omarchy-osd failed: %s", exc)


def codex_version(command: list[str]) -> str | None:
    try:
        out = subprocess.run(command + ["--version"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(\d+\.\d+\.\d+)", out)
    return match.group(1) if match else None


class Daemon:
    def __init__(self, config: Config, state: State, *, socket: Path | None = None,
                 voice_factory=None, osd_func=osd):
        self.config = config
        self.state = state
        self.socket = socket or socket_path()
        self.voice_factory = voice_factory  # tests inject a fake; None = real VoiceLink
        self.osd = osd_func if config.osd else (lambda *a, **k: None)

        self.app: AppServer | None = None
        self.thread_id: str | None = None
        self.models: list[dict] = []
        self.codex_version: str | None = None
        self.warnings: list[str] = []
        self.available_update: dict | None = None  # {"version", "url"} of a newer release, if any

        self.user_muted = True
        self.dictation_pauses: dict[str, str] = {}
        self.dictation_monitor = "disabled"
        self._dictation_seen = False
        self.voice = None
        self.voice_status = "off"      # off | connecting | on | error
        self.working = False
        self.speaking = False
        self.history: deque[dict] = deque(maxlen=HISTORY_LIMIT)
        self.reviews: dict[str, dict] = {}
        self._typed_ids: set[str] = set()
        self._reply_text = ""
        self._reply_open = False
        self._last_loud = 0.0
        self._window = np.zeros(1024)
        self._muted_since = time.monotonic()
        self._subscribers: set[asyncio.StreamWriter] = set()
        self._server: asyncio.base_events.Server | None = None
        self._stopping = asyncio.Event()
        self._voice_lock = asyncio.Lock()
        self._background: set[asyncio.Task] = set()
        self._carry_notice: str | None = None  # shown at the top of the next session

    # ------------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        self.warnings.extend(getattr(self.config, "problems", []))
        self.codex_version = await asyncio.to_thread(codex_version, self.config.codex_command)
        if self.config.dictation_auto_pause and shutil.which("superwhisper"):
            self.dictation_pauses["adapter:superwhisper"] = "Superwhisper"
        await self._start_server()
        if self.voice_factory is None:
            # aiortc/PyAV take ~2 s to import; do it now so the first unmute is quick.
            self._spawn(asyncio.to_thread(importlib.import_module, "codex_voice_agent.voice"))
        tasks = [asyncio.create_task(self._supervise_codex()),
                 asyncio.create_task(self._tick())]
        if self.config.dictation_auto_pause:
            tasks.append(asyncio.create_task(watch_superwhisper(self._superwhisper_status)))
        if self.config.check_updates:
            tasks.append(asyncio.create_task(self._check_updates()))
        await self._stopping.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._shutdown()

    def stop(self) -> None:
        self._stopping.set()

    def _spawn(self, coro) -> asyncio.Task:
        """Run a coroutine in the background, keeping a reference and logging failures."""
        task = asyncio.create_task(coro)
        self._background.add(task)

        def done(t: asyncio.Task) -> None:
            self._background.discard(t)
            if not t.cancelled() and t.exception():
                log.error("background task failed", exc_info=t.exception())
        task.add_done_callback(done)
        return task

    async def _shutdown(self) -> None:
        await self._close_voice()
        if self.app:
            await self.app.stop()
        if self._server:
            self._server.close()
        try:
            self.socket.unlink()
        except FileNotFoundError:
            pass

    async def _supervise_codex(self) -> None:
        """Keep a codex app-server running; a crash costs the session, not the daemon."""
        backoff = 2
        while not self._stopping.is_set():
            try:
                await self._start_codex()
                backoff = 2
                await self.app.closed.wait()
                if self._stopping.is_set():
                    return
                log.warning("codex app-server exited; restarting")
                self._carry_notice = "Codex stopped unexpectedly; started a new session."
            except Exception as exc:
                log.error("codex start failed: %s", exc)
                self.warnings = [w for w in self.warnings if not w.startswith("Codex unavailable")]
                self.warnings.append(f"Codex unavailable: {exc}")
                await self._broadcast_state()
            await self._close_voice()
            if self.app:
                await self.app.stop()  # never leave a half-started app-server running
            self.thread_id = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _start_codex(self) -> None:
        self.app = AppServer(self.config.codex_command, self._on_notification,
                             client_name="codex-voice-agent", client_version=__version__)
        await self.app.start()
        self.warnings = [w for w in self.warnings if not w.startswith("Codex unavailable")]
        listing = await self.app.call("model/list", {})
        self.models = [{"id": m.get("id") or m.get("model"),
                        "name": m.get("displayName") or m.get("id") or m.get("model"),
                        "default": bool(m.get("isDefault")),
                        "efforts": [e.get("reasoningEffort", e) if isinstance(e, dict) else e
                                    for e in m.get("supportedReasoningEfforts", [])],
                        "defaultEffort": m.get("defaultReasoningEffort")}
                       for m in listing.get("data", listing.get("models", []))]
        await self._new_thread()

    async def _new_thread(self) -> None:
        params = {"cwd": str(self.config.workdir), "serviceName": "codex-voice-agent",
                  **self.config.thread_permissions()}
        if self.state.model and any(m["id"] == self.state.model for m in self.models):
            params["model"] = self.state.model
        result = await self.app.call("thread/start", params)
        thread = result["thread"]
        self.thread_id = thread["id"]
        if not self.state.model:
            self.state.model = result.get("model") or thread.get("model")
        if not self.state.effort:
            self.state.effort = thread.get("reasoningEffort")
        elif self.state.effort != thread.get("reasoningEffort"):
            await self.app.call("thread/settings/update", {"threadId": self.thread_id, "effort": self.state.effort})
        self.history.clear()
        self.reviews.clear()
        self.working = False
        self._reset_reply()
        await self._broadcast({"event": "history_reset"})
        if self._carry_notice:
            self._notice(self._carry_notice)
            self._carry_notice = None
        self._notice(f"New session in {self.config.workdir}")
        log.info("thread %s started (model %s, effort %s)", self.thread_id, self.state.model, self.state.effort)
        if not self.muted:
            self._spawn(self._ensure_voice())
        await self._broadcast_state()

    async def _check_updates(self) -> None:
        """Once a day: is there a newer release? Shown in the panel; one desktop notification
        per new version (remembered across restarts)."""
        await asyncio.sleep(UPDATE_FIRST_CHECK)
        while True:
            latest = await asyncio.to_thread(update.latest_release)
            if latest and update.is_newer(latest["version"]):
                if latest != self.available_update:
                    self.available_update = latest
                    log.info("update available: %s", latest["version"])
                    await self._broadcast_state()
                if self.state.notified_update != latest["version"]:
                    self.state.notified_update = latest["version"]
                    self.state.save()
                    self.notify_update(latest)
            await asyncio.sleep(UPDATE_INTERVAL)

    def notify_update(self, latest: dict) -> None:
        _osd_children[:] = [p for p in _osd_children if p.poll() is None]
        if not shutil.which("notify-send"):
            return
        try:
            _osd_children.append(subprocess.Popen(
                ["notify-send", "-a", "Codex Voice Agent", "-i", "software-update-available",
                 f"Codex Voice Agent {latest['version']} is available",
                 f"You have {__version__}. See the panel for how to update."],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True))
        except OSError as exc:
            log.debug("notify-send failed: %s", exc)

    # ------------------------------------------------------------------ snapshot
    def snapshot(self) -> dict:
        return {
            "event": "state",
            "version": __version__,
            "codexVersion": self.codex_version,
            "codexRunning": bool(self.app and self.app.running),
            "threadId": self.thread_id,
            "workdir": str(self.config.workdir),
            "muted": self.muted,
            "userMuted": self.user_muted,
            "dictationPauses": sorted(set(self.dictation_pauses.values())),
            "dictationMonitor": self.dictation_monitor,
            "listening": not self.muted and self.voice_status == "on",
            "voice": self.voice_status,
            "working": self.working,
            "speaking": self.speaking,
            "model": self.state.model,
            "effort": self.state.effort,
            "models": self.models,
            "replyCard": self.state.reply_card,
            "input": self.state.input,
            "permissions": self.config.permissions,
            "warnings": self.warnings,
            "update": self.available_update,
        }

    # ------------------------------------------------------------------ mute + voice
    @property
    def muted(self) -> bool:
        return self.user_muted or bool(self.dictation_pauses)

    @muted.setter
    def muted(self, value: bool) -> None:
        self.user_muted = value

    def _discard_mic_frames(self) -> None:
        frames = getattr(self.voice, "mic_frames", None)
        if frames is not None:
            while not frames.empty():
                frames.get_nowait()

    async def _mute_state_changed(self, was_muted: bool) -> None:
        if was_muted != self.muted:
            self._discard_mic_frames()
            self._muted_since = time.monotonic()
            message = "Paused for dictation" if self.dictation_pauses else None
            self.osd(self.muted, message)
        await self._broadcast_state()
        if not self.muted:
            self._spawn(self._ensure_voice())

    async def set_muted(self, value) -> bool:
        was_muted = self.muted
        self.user_muted = (not self.user_muted) if value == "toggle" else bool(value)
        await self._mute_state_changed(was_muted)
        return self.muted

    async def _superwhisper_status(self, active: bool | None, error: str | None) -> None:
        was_muted = self.muted
        key = "adapter:superwhisper"
        self.dictation_monitor = error or ("active" if active else "idle")
        if active is not None:
            self._dictation_seen = True
            if active:
                self.dictation_pauses[key] = "Superwhisper"
            else:
                self.dictation_pauses.pop(key, None)
        elif error == "connecting":
            # Before the first snapshot, never assume that recording is idle.
            self.dictation_pauses[key] = "Superwhisper"
        elif key in self.dictation_pauses or self._dictation_seen:
            # Failed monitoring must not silently resume. Latch manual mute;
            # reconnection can clear the pause, but needs an explicit unmute.
            self.user_muted = True
            if key in self.dictation_pauses:
                self.dictation_pauses[key] = "Superwhisper (status unavailable)"
        await self._mute_state_changed(was_muted)

    async def acquire_dictation(self, source: str) -> str:
        if not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}", source):
            raise ValueError("source must be 1–64 letters, numbers, spaces, dots, underscores or hyphens")
        if len(self.dictation_pauses) >= 128:
            raise ValueError("too many dictation pauses")
        token = str(uuid.uuid4())
        was_muted = self.muted
        self.dictation_pauses[token] = source
        await self._mute_state_changed(was_muted)
        return token

    async def release_dictation(self, token: str) -> None:
        if not isinstance(token, str) or token.startswith("adapter:") or token not in self.dictation_pauses:
            raise ValueError("unknown dictation token")
        was_muted = self.muted
        del self.dictation_pauses[token]
        await self._mute_state_changed(was_muted)

    async def _ensure_voice(self) -> None:
        async with self._voice_lock:
            if self.voice and self.voice.connected:
                return
            if not self.app or not self.app.running or not self.thread_id:
                return
            if self.voice:  # a link that died on its own: release its devices and session first
                stale, self.voice = self.voice, None
                await self._quiet_close(stale)
            self.voice_status = "connecting"
            await self._broadcast_state()
            factory = self.voice_factory
            if factory is None:
                from .voice import VoiceLink
                factory = VoiceLink
            voice = factory(self.app, self.thread_id, is_muted=lambda: self.muted,
                            on_event=self._on_voice_event, on_audio=self._on_voice_audio,
                            echo_cancel=self.config.echo_cancel, voice=self.config.voice,
                            briefing=VOICE_BRIEFING if self.config.voice_mute_command else None,
                            input_device=self.state.input, on_lost=self._on_voice_lost)
            self.voice = voice
            try:
                await voice.connect()
                self.voice_status = "on"
            except Exception as exc:
                log.error("voice connect failed: %s", exc)
                self.voice = None
                self.voice_status = "error"
                self._notice(f"Voice could not start: {exc}")
                self._mute_after_failure("Voice assistant could not connect")
            await self._broadcast_state()

    def _mute_after_failure(self, message: str) -> None:
        """Never leave the user unmuted with nothing listening: mute, and say why."""
        if not self.user_muted:
            self.user_muted = True
            self._muted_since = time.monotonic()
            self.osd(True, message)

    def _on_voice_lost(self, reason: str) -> None:
        """Called by the voice link when its peer fails or its microphone process dies."""
        self._spawn(self._voice_lost(reason))

    async def _voice_lost(self, reason: str) -> None:
        async with self._voice_lock:
            if not self.voice or self.voice.connected:
                return
            lost, self.voice = self.voice, None
            await self._quiet_close(lost)
            self.voice_status = "error"
            self.speaking = False
            self._reset_reply()
        log.warning("voice connection lost: %s", reason)
        self._notice(f"Voice connection lost ({reason}). Unmute to reconnect.")
        self._mute_after_failure("Voice assistant disconnected")
        await self._broadcast_state()

    async def _quiet_close(self, voice) -> None:
        try:
            await voice.close(stop_remote=bool(self.app and self.app.running))
        except Exception as exc:
            log.debug("voice close: %s", exc)

    def _reset_reply(self) -> None:
        self._reply_open, self._reply_text = False, ""

    async def _close_voice(self) -> None:
        async with self._voice_lock:
            if self.voice:
                voice, self.voice = self.voice, None
                await self._quiet_close(voice)
            self.voice_status = "off"
            self.speaking = False
            self._reset_reply()

    def _on_voice_event(self, event: dict) -> None:
        """Raw realtime events from the data channel; only delegations are needed here."""
        if event.get("type") == "delegation.created":
            content = event.get("item", {}).get("content") or [{}]
            self._add("handoff", content[0].get("text", ""))

    def _on_voice_audio(self, pcm: np.ndarray) -> None:
        if peak(pcm) > LOUD:
            self._last_loud = time.monotonic()
        self._window = np.concatenate([self._window[pcm.size:], pcm.astype(np.float64)])[-1024:]

    async def _tick(self) -> None:
        """Speaking detection, the visualizer feed, and the idle voice timeout."""
        while True:
            await asyncio.sleep(SPECTRUM_INTERVAL)
            try:
                await self._tick_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("tick failed")
                await asyncio.sleep(1)

    async def _tick_once(self) -> None:
        now = time.monotonic()
        speaking = self.voice is not None and now - self._last_loud < SPEAKING_HOLD
        if speaking != self.speaking:
            self.speaking = speaking
            await self._broadcast_state()
            if not speaking and self._reply_open:
                await self._broadcast({"event": "reply", "phase": "audio_done", "text": self._reply_text})
        if speaking:
            await self._broadcast({"event": "spectrum", "bands": spectrum_bands(self._window)})
        idle = self.config.idle_voice_minutes * 60
        if self.muted and self.voice and idle > 0 and now - self._muted_since > idle and not self.working:
            log.info("muted for %.0f min; closing voice until next unmute", self.config.idle_voice_minutes)
            await self._close_voice()
            await self._broadcast_state()

    # ------------------------------------------------------------------ codex events
    async def _on_notification(self, method: str, params: dict) -> None:
        if params.get("threadId") not in (None, self.thread_id):
            return
        if self.voice and method.startswith("thread/realtime/"):
            self.voice.handle_notification(method, params)

        if method == "thread/realtime/transcript/delta" and params.get("role") == "assistant":
            if not self._reply_open:
                self._reply_open, self._reply_text = True, ""
                await self._broadcast({"event": "reply", "phase": "start", "voice": True, "text": ""})
            self._reply_text += params.get("delta", "")
            await self._broadcast({"event": "reply", "phase": "delta", "voice": True, "text": self._reply_text.strip()})
        elif method == "thread/realtime/transcript/done":
            text = params.get("text", "").strip()
            if params.get("role") == "assistant":
                self._add("agent", text)
                self._reply_open = False
                await self._broadcast({"event": "reply", "phase": "done", "voice": True, "text": text})
            elif text:
                self._add("you", text)
                if self.config.voice_mute_command and not self.muted and is_mute_command(text):
                    await self.set_muted(True)
                    self._notice("Muted by voice command")
        elif method == "thread/realtime/closed":
            # Scheduled, not awaited: closing calls Codex, and this handler runs on the
            # notification dispatcher. Only a live link is affected; a stale "closed" for an
            # earlier session must not tear down one that is still connecting.
            if self.voice and self.voice.connected:
                self.voice.connected = False
                self._on_voice_lost(params.get("reason") or "closed by Codex")
        elif method == "thread/realtime/error":
            self._notice(f"Voice error: {params.get('message')}")
        elif method == "turn/started":
            self.working = True
            await self._broadcast_state()
        elif method == "turn/completed":
            self.working = False
            err = (params.get("turn") or {}).get("error")
            if err:
                self._notice(f"Codex turn failed: {err.get('message', err) if isinstance(err, dict) else err}")
            await self._broadcast_state()
        elif method == "item/completed":
            await self._on_item(params.get("item") or {})
        elif method == "item/autoApprovalReview/completed":
            review = params.get("review") or {}
            rid = params.get("reviewId") or str(uuid.uuid4())
            self.reviews[rid] = params
            action = params.get("action") or {}
            self._add("review", review.get("rationale") or "", status=review.get("status"),
                      detail={"reviewId": rid, "risk": review.get("riskLevel"),
                              "command": action.get("command")})

    async def _on_item(self, item: dict) -> None:
        kind = item.get("type")
        if kind == "userMessage":
            if item.get("clientId") in self._typed_ids:
                return
            text = " ".join(c.get("text", "") for c in item.get("content", []) if c.get("type") == "text")
            if DELEGATION_RE.search(text) or text.lstrip().startswith("<realtime_delegation>"):
                return  # the spoken words are already in the history as "you"
            if text.strip():
                self._add("you_typed", text.strip())
        elif kind == "agentMessage":
            text = (item.get("text") or "").strip()
            if not text:
                return
            final = item.get("phase") != "commentary"
            self._add("codex" if final else "codex_note", text, html=markdown_html(text))
            if final and not (self.voice and self.voice.connected):
                # No voice to speak it: still show the reply card for typed requests.
                await self._broadcast({"event": "reply", "phase": "done", "voice": False, "text": text})
        elif kind == "commandExecution":
            self._add("command", item.get("command", ""), status=item.get("status"),
                      detail={"exitCode": item.get("exitCode")})
        elif kind == "fileChange":
            paths = [c.get("path") for c in item.get("changes", []) if isinstance(c, dict)]
            self._add("files", ", ".join(p for p in paths if p) or "file changes", status=item.get("status"))

    # ------------------------------------------------------------------ actions
    def _require_codex(self) -> AppServer:
        if not self.app or not self.app.running or not self.thread_id:
            raise AppServerError("Codex is not running")
        return self.app

    async def send_text(self, text: str) -> None:
        app = self._require_codex()
        client_id = str(uuid.uuid4())
        self._typed_ids.add(client_id)
        params = {"threadId": self.thread_id, "clientUserMessageId": client_id,
                  "input": [{"type": "text", "text": text}]}
        if self.state.model:
            params["model"] = self.state.model
        if self.state.effort:
            params["effort"] = self.state.effort
        await app.call("turn/start", params)
        self._add("you_typed", text)  # only once Codex has accepted it

    async def set_model(self, model: str) -> None:
        info = next((m for m in self.models if m["id"] == model), None)
        if not info:
            raise ValueError(f"unknown model {model}")
        app = self._require_codex()
        update = {"threadId": self.thread_id, "model": model}
        new_effort = self.state.effort
        if self.state.effort and info["efforts"] and self.state.effort not in info["efforts"]:
            new_effort = info.get("defaultEffort") or info["efforts"][0]
            update["effort"] = new_effort
        await app.call("thread/settings/update", update)
        self.state.effort = new_effort
        self.state.model = model
        self.state.save()
        await self._broadcast_state()

    async def set_effort(self, effort: str) -> None:
        info = next((m for m in self.models if m["id"] == self.state.model), None)
        if info and info["efforts"] and effort not in info["efforts"]:
            raise ValueError(f"{self.state.model} does not support thinking level {effort}")
        await self._require_codex().call("thread/settings/update", {"threadId": self.thread_id, "effort": effort})
        self.state.effort = effort
        self.state.save()
        await self._broadcast_state()

    async def list_inputs(self) -> dict:
        from .audio import list_inputs
        return await list_inputs()

    async def set_input(self, device: str | None) -> None:
        """Choose the microphone; None follows the system default."""
        device = device or None
        if device:
            known = {d["id"] for d in (await self.list_inputs())["inputs"]}
            if device not in known:
                raise ValueError(f"no microphone named {device}")
        failure = None
        async with self._voice_lock:  # never race a connect, close or idle timeout
            if self.voice and getattr(self.voice, "connected", False):
                try:
                    await self.voice.set_input(device)
                except Exception as exc:
                    # The link fell back to the previous mic, or closed itself; keep the old choice.
                    log.error("microphone switch failed: %s", exc)
                    failure = exc
                    if not getattr(self.voice, "connected", False):
                        lost, self.voice = self.voice, None
                        await self._quiet_close(lost)
                        self.voice_status = "error"
            if failure is None:
                self.state.input = device
                self.state.save()
        if failure is not None:
            if self.voice is None:
                self._mute_after_failure("Voice assistant disconnected")
            await self._broadcast_state()
            raise ValueError(f"could not switch to that microphone: {failure}") from failure
        await self._broadcast_state()

    async def new_session(self) -> None:
        await self._close_voice()
        if self.app and self.app.running:
            await self._new_thread()

    async def approve_denied(self, review_id: str) -> None:
        event = self.reviews.get(review_id)
        if not event:
            raise ValueError("unknown review")
        await self._require_codex().call("thread/approveGuardianDeniedAction", {"threadId": self.thread_id, "event": event})
        self._notice("Approved the denied action; Codex will retry it.")

    # ------------------------------------------------------------------ history
    def _add(self, kind: str, text: str, status: str | None = None, detail: dict | None = None,
             html: str | None = None) -> None:
        entry = {"id": uuid.uuid4().hex[:12], "kind": kind, "text": text, "ts": time.time()}
        if html is not None:
            entry["html"] = html  # Codex Markdown, rendered safe by render.markdown_html
        if status:
            entry["status"] = status
        if detail:
            entry["detail"] = detail
        self.history.append(entry)
        asyncio.create_task(self._broadcast({"event": "item", "item": entry}))

    def _notice(self, text: str) -> None:
        self._add("notice", text)

    # ------------------------------------------------------------------ control socket
    async def _start_server(self) -> None:
        self.socket.parent.mkdir(parents=True, exist_ok=True)
        if self.socket.exists():
            try:  # a live daemon still owns it?
                _, w = await asyncio.wait_for(asyncio.open_unix_connection(str(self.socket)), 1)
                w.close()
                raise SystemExit(f"another codex-voice-agent is running ({self.socket})")
            except (ConnectionError, OSError, asyncio.TimeoutError):
                self.socket.unlink()
        old_umask = os.umask(0o177)
        try:
            self._server = await asyncio.start_unix_server(self._client, path=str(self.socket), limit=MAX_REQUEST)
        finally:
            os.umask(old_umask)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    line = await reader.readline()
                except ValueError:  # a line longer than MAX_REQUEST: skip to its end, keep the connection
                    while True:
                        chunk = await reader.read(65536)
                        if not chunk or b"\n" in chunk:
                            break
                    await self._write(writer, {"ok": False, "error": "request too large"})
                    continue
                if not line:
                    return
                try:
                    req = json.loads(line)
                except ValueError:
                    await self._write(writer, {"ok": False, "error": "invalid JSON"})
                    continue
                if not isinstance(req, dict):
                    await self._write(writer, {"ok": False, "error": "a request must be a JSON object"})
                    continue
                reply = await self._dispatch(req, writer)
                reply["id"] = req.get("id")
                await self._write(writer, reply)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._subscribers.discard(writer)
            writer.close()

    async def _dispatch(self, req: dict, writer: asyncio.StreamWriter) -> dict:
        cmd = req.get("cmd")
        try:
            if cmd == "state":
                return {"ok": True, **self.snapshot()}
            if cmd == "subscribe":
                self._subscribers.add(writer)
                return {"ok": True, **self.snapshot()}
            if cmd == "history":
                return {"ok": True, "items": list(self.history)}
            if cmd == "dictation_acquire":
                token = await self.acquire_dictation(req.get("source"))
                return {"ok": True, "token": token, "muted": self.muted}
            if cmd == "dictation_release":
                await self.release_dictation(req.get("token"))
                return {"ok": True, "muted": self.muted}
            if cmd == "mute":
                return {"ok": True, "muted": await self.set_muted(req.get("value", "toggle"))}
            if cmd == "set_model":
                await self.set_model(str(req["model"]))
                return {"ok": True}
            if cmd == "set_effort":
                await self.set_effort(str(req["effort"]))
                return {"ok": True}
            if cmd == "set_reply_card":
                self.state.reply_card = bool(req.get("value"))
                self.state.save()
                await self._broadcast_state()
                return {"ok": True}
            if cmd == "list_inputs":
                return {"ok": True, **(await self.list_inputs()), "selected": self.state.input}
            if cmd == "set_input":
                await self.set_input(req.get("device"))
                return {"ok": True, "selected": self.state.input}
            if cmd == "new_session":
                await self.new_session()
                return {"ok": True, "threadId": self.thread_id}
            if cmd == "send":
                text = str(req.get("text", "")).strip()
                if not text:
                    return {"ok": False, "error": "empty message"}
                await self.send_text(text)
                return {"ok": True}
            if cmd == "approve_denied":
                await self.approve_denied(str(req["reviewId"]))
                return {"ok": True}
            return {"ok": False, "error": f"unknown command {cmd!r}"}
        except (KeyError, ValueError, AppServerError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # anything else still gets an answer, and a log entry
            log.exception("command %s failed", cmd)
            return {"ok": False, "error": f"{cmd} failed: {exc}"}

    @staticmethod
    async def _write(writer: asyncio.StreamWriter, obj: dict) -> None:
        writer.write((json.dumps(obj) + "\n").encode())
        await writer.drain()

    async def _broadcast(self, obj: dict) -> None:
        if not self._subscribers:
            return
        data = (json.dumps(obj) + "\n").encode()
        for writer in list(self._subscribers):
            try:
                writer.write(data)
                if writer.transport.get_write_buffer_size() > 1 << 20:
                    raise ConnectionError("subscriber not reading")
            except (ConnectionError, RuntimeError):
                self._subscribers.discard(writer)
                writer.close()

    async def _broadcast_state(self) -> None:
        await self._broadcast(self.snapshot())
