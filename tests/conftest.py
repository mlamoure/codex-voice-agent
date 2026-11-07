import asyncio
import json
import sys
from pathlib import Path

import pytest

from codex_voice_agent.config import Config, State
from codex_voice_agent.daemon import Daemon

FAKE = Path(__file__).with_name("fake_codex.py")


class FakeVoice:
    """Replaces VoiceLink: no WebRTC, no audio devices."""
    instances: list["FakeVoice"] = []

    def __init__(self, app, thread_id, *, is_muted, on_event, on_audio, echo_cancel=True, voice=None,
                 briefing=None, input_device=None, on_lost=None):
        self.briefing = briefing
        self.on_lost = on_lost
        self.input_device = input_device
        self.switched_to = []
        self.thread_id = thread_id
        self.is_muted = is_muted
        self.on_event = on_event
        self.on_audio = on_audio
        self.connected = False
        self.closed = False
        FakeVoice.instances.append(self)

    async def connect(self):
        self.connected = True

    def handle_notification(self, method, params):
        pass

    async def set_input(self, device):
        if device == "mic-fails":
            raise OSError("device busy")  # like VoiceLink: previous mic restored, error raised
        self.input_device = device
        self.switched_to.append(device)

    def drop(self, reason="peer failed"):
        """Simulate the connection dying on its own (network, remote close, dead mic)."""
        self.connected = False
        self.on_lost(reason)

    async def close(self, stop_remote=True):
        self.connected = False
        self.closed = True


class Client:
    """Talks to the daemon's control socket like the panel does."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.events: list[dict] = []
        self._id = 0

    async def cmd(self, cmd, **args):
        self._id += 1
        self.writer.write((json.dumps({"id": self._id, "cmd": cmd, **args}) + "\n").encode())
        await self.writer.drain()
        while True:
            msg = json.loads(await asyncio.wait_for(self.reader.readline(), 10))
            if "event" in msg and "id" not in msg:
                self.events.append(msg)
                continue
            return msg

    async def wait_event(self, pred, timeout=5):
        for e in self.events:
            if pred(e):
                return e
        end = asyncio.get_running_loop().time() + timeout
        while True:
            left = end - asyncio.get_running_loop().time()
            if left <= 0:
                raise AssertionError("event not seen")
            msg = json.loads(await asyncio.wait_for(self.reader.readline(), left))
            self.events.append(msg)
            if pred(msg):
                return msg


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("codex_voice_agent.config.STATE_FILE", tmp_path / "state.json")
    log = tmp_path / "codex.log"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    FakeVoice.instances.clear()
    return tmp_path, log


def make_daemon(tmp_path, osd_calls, **cfg):
    cfg.setdefault("dictation_auto_pause", False)  # never launch a real transcriber
    cfg.setdefault("check_updates", False)  # never reach GitHub from the suite
    config = Config(workdir=tmp_path, codex_command=[sys.executable, str(FAKE)], **cfg)
    state = State()
    state.save = lambda path=None: None
    return Daemon(config, state, socket=tmp_path / "ctl.sock", voice_factory=FakeVoice,
                  osd_func=lambda muted, message=None: osd_calls.append((muted, message)))


async def start(daemon, tmp_path):
    task = asyncio.create_task(daemon.run())
    for _ in range(200):
        if daemon.thread_id and (tmp_path / "ctl.sock").exists():
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("daemon did not start")
    reader, writer = await asyncio.open_unix_connection(str(tmp_path / "ctl.sock"))
    return task, Client(reader, writer)


def requests(log: Path, method: str) -> list[dict]:
    if not log.exists():
        return []
    return [r for r in map(json.loads, log.read_text().splitlines()) if r.get("method") == method]
