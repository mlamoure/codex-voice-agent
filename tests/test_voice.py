"""VoiceLink with fake audio devices, a fake WebRTC peer and a fake app-server.

These run the real connect / close / set_input code (the daemon tests use a stand-in
VoiceLink, which is how a NameError in connect() once shipped unnoticed).
"""
import asyncio

import pytest

from codex_voice_agent import voice as voice_mod
from codex_voice_agent.audio import EC_SOURCE


class Log:
    events: list = []


class FakeEC:
    def __init__(self):
        self.loaded = False

    async def start(self, capture_target=None):
        self.loaded = True
        Log.events.append(("ec.start", capture_target))
        return True

    def stop(self):
        if self.loaded:
            Log.events.append(("ec.stop",))
        self.loaded = False


class FakeMic:
    def __init__(self, target=None, frames=None, on_exit=None):
        self.target = target
        self.frames = frames
        self.on_exit = on_exit
        Log.mic = self

    async def start(self):
        if self.target == "broken" or (self.target == EC_SOURCE and Log.broken_capture):
            raise OSError("device busy")
        Log.events.append(("mic.start", self.target))

    async def stop(self):
        Log.events.append(("mic.stop", self.target))


class FakeSpeaker:
    def start(self):
        Log.events.append(("speaker.start",))

    def write(self, pcm):
        return True

    def stop(self):
        Log.events.append(("speaker.stop",))


class FakeOffer:
    sdp = "offer-sdp"


class FakeChannel:
    def on(self, event):
        return lambda fn: fn


class FakePC:
    def __init__(self):
        self.connectionState = "new"
        self.localDescription = None

    def addTrack(self, track):
        pass

    def createDataChannel(self, name):
        return FakeChannel()

    def on(self, event):
        return lambda fn: fn

    async def createOffer(self):
        return FakeOffer()

    async def setLocalDescription(self, desc):
        self.localDescription = desc

    async def setRemoteDescription(self, desc):
        Log.events.append(("pc.remote", desc))

    async def close(self):
        Log.events.append(("pc.close",))


class FakeApp:
    def __init__(self, answer="answer-sdp", error=None):
        self.calls = []
        self.answer, self.error = answer, error
        self.link = None

    async def call(self, method, params=None, timeout=60):
        self.calls.append((method, params))
        if method == "thread/realtime/start":
            loop = asyncio.get_running_loop()
            if self.error:
                loop.call_soon(self.link.handle_notification, "thread/realtime/error", {"message": self.error})
            else:
                loop.call_soon(self.link.handle_notification, "thread/realtime/sdp", {"sdp": self.answer})
        return {}


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    Log.events = []
    Log.broken_capture = False
    monkeypatch.setattr(voice_mod, "EchoCanceller", FakeEC)
    monkeypatch.setattr(voice_mod, "Microphone", FakeMic)
    monkeypatch.setattr(voice_mod, "Speaker", FakeSpeaker)
    monkeypatch.setattr(voice_mod, "RTCPeerConnection", FakePC)
    monkeypatch.setattr(voice_mod, "RTCSessionDescription", lambda sdp, type: sdp)


def make_link(app, **kw):
    link = voice_mod.VoiceLink(app, "thread-1", is_muted=lambda: True, on_event=lambda e: None,
                               on_audio=lambda pcm: None, **kw)
    app.link = link
    return link


def run(coro):
    return asyncio.run(coro)


def methods(app):
    return [m for m, _ in app.calls]


def test_connect_opens_audio_and_the_session():
    async def body():
        app = FakeApp()
        link = make_link(app, briefing="facts")
        await link.connect()
        assert link.connected and link.aec_active
        assert ("mic.start", EC_SOURCE) in Log.events  # mic goes through the echo canceller
        assert ("pc.remote", "answer-sdp") in Log.events
        start = dict(app.calls)["thread/realtime/start"]
        assert start["version"] == "v3" and start["transport"]["sdp"] == "offer-sdp"
        assert start["initialItems"] == [{"role": "developer", "text": "facts"}]
        await link.close()
        assert ("ec.stop",) in Log.events and ("speaker.stop",) in Log.events
    run(body())


def test_device_failure_before_the_session_unloads_everything():
    async def body():
        app = FakeApp()
        Log.broken_capture = True
        link = make_link(app)
        with pytest.raises(OSError):
            await link.connect()
        assert not link.connected
        assert ("ec.stop",) in Log.events  # the echo canceller is not left loaded
        assert "thread/realtime/start" not in methods(app)
        assert "thread/realtime/stop" not in methods(app)  # nothing remote to stop
    run(body())


def test_session_failure_stops_the_remote_side_too():
    async def body():
        app = FakeApp(error="refused")
        link = make_link(app)
        with pytest.raises(RuntimeError, match="refused"):
            await link.connect()
        assert not link.connected
        assert "thread/realtime/stop" in methods(app)
        assert ("ec.stop",) in Log.events and ("speaker.stop",) in Log.events and ("pc.close",) in Log.events
    run(body())


def test_bad_microphone_falls_back_to_the_previous_one():
    async def body():
        app = FakeApp()
        link = make_link(app, echo_cancel=False, input_device="good-mic")
        await link.connect()
        with pytest.raises(OSError):
            await link.set_input("broken")
        assert link.connected and link.input_device == "good-mic"
        assert Log.events[-1] == ("mic.start", "good-mic")
    run(body())


def test_switch_that_cannot_recover_closes_the_link():
    async def body():
        app = FakeApp()
        link = make_link(app, echo_cancel=True)
        await link.connect()
        Log.broken_capture = True  # neither the new nor the old mic can open now
        with pytest.raises(OSError):
            await link.set_input("other-mic")
        assert not link.connected  # the daemon will reconnect on the next unmute
        assert "thread/realtime/stop" in methods(app)
    run(body())


def test_a_dying_microphone_reports_the_link_lost():
    async def body():
        app = FakeApp()
        lost = []
        link = make_link(app, echo_cancel=False, on_lost=lost.append)
        await link.connect()
        Log.mic.on_exit()  # pw-record ended on its own
        assert not link.connected and lost == ["the microphone stopped"]
        Log.mic.on_exit()  # reported once only
        assert lost == ["the microphone stopped"]
    run(body())
