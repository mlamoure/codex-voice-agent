import json
import asyncio

import numpy as np

from conftest import FakeVoice, make_daemon, requests, start


def run(coro):
    return asyncio.run(coro)


def test_starts_muted_with_auto_review_thread(env):
    tmp, log = env

    async def body():
        osd = []
        d = make_daemon(tmp, osd)
        task, c = await start(d, tmp)
        st = await c.cmd("subscribe")
        assert st["ok"] and st["muted"] is True and st["listening"] is False
        assert st["voice"] == "off"  # no voice connection until the first unmute
        assert [m["id"] for m in st["models"]] == ["model-a", "model-b"]
        assert st["models"][0]["efforts"] == ["low", "medium", "high"]
        start_req = requests(log, "thread/start")[0]["params"]
        assert start_req["sandbox"] == "workspace-write"
        assert start_req["approvalPolicy"] == "on-request"
        assert start_req["approvalsReviewer"] == "auto_review"
        assert start_req["cwd"] == str(tmp)
        assert osd == []  # starting muted shows no OSD
        d.stop(); await task
    run(body())


def test_full_permissions_profile(env):
    tmp, log = env

    async def body():
        d = make_daemon(tmp, [], permissions="full")
        task, c = await start(d, tmp)
        p = requests(log, "thread/start")[0]["params"]
        assert p["sandbox"] == "danger-full-access" and p["approvalPolicy"] == "never"
        assert "approvalsReviewer" not in p
        d.stop(); await task
    run(body())


def test_mute_toggle_connects_voice_and_shows_osd(env):
    tmp, _ = env

    async def body():
        osd = []
        d = make_daemon(tmp, osd)
        task, c = await start(d, tmp)
        await c.cmd("subscribe")
        r = await c.cmd("mute", value="toggle")
        assert r["muted"] is False
        await c.wait_event(lambda e: e.get("event") == "state" and e.get("listening"))
        assert FakeVoice.instances and FakeVoice.instances[-1].connected
        assert FakeVoice.instances[-1].is_muted() is False
        r = await c.cmd("mute", value="toggle")
        assert r["muted"] is True
        # muting keeps the connection (fast unmute) but the mic track sends silence
        assert FakeVoice.instances[-1].connected and FakeVoice.instances[-1].is_muted() is True
        assert osd == [(False, None), (True, None)]
        r = await c.cmd("mute", value=True)  # already muted: no second OSD
        assert osd == [(False, None), (True, None)]
        d.stop(); await task
    run(body())


def test_idle_mute_closes_voice(env):
    tmp, _ = env

    async def body():
        d = make_daemon(tmp, [], idle_voice_minutes=0.5 / 60)  # 0.5 s
        task, c = await start(d, tmp)
        await c.cmd("mute", value=False)
        for _ in range(50):
            if d.voice_status == "on":
                break
            await asyncio.sleep(0.05)
        await c.cmd("mute", value=True)
        await asyncio.sleep(1.0)
        assert d.voice is None and d.voice_status == "off"
        assert FakeVoice.instances[-1].closed
        d.stop(); await task
    run(body())


def test_typed_request_history_and_reply_card(env):
    tmp, log = env

    async def body():
        d = make_daemon(tmp, [])
        task, c = await start(d, tmp)
        await c.cmd("subscribe")
        assert (await c.cmd("send", text="hello"))["ok"]
        reply = await c.wait_event(lambda e: e.get("event") == "reply" and e.get("phase") == "done")
        assert reply["text"] == "done: hello" and reply["voice"] is False
        await c.wait_event(lambda e: e.get("event") == "state" and e.get("working") is False
                           and any(x.get("event") == "state" and x.get("working") for x in c.events))
        kinds = [i["kind"] for i in (await c.cmd("history"))["items"]]
        # the typed message appears once (our copy), not again from Codex's userMessage item
        assert kinds.count("you_typed") == 1
        assert "command" in kinds and "codex" in kinds
        # Codex entries carry the backend's image-free rendering; others never do
        items = (await c.cmd("history"))["items"]
        codex = next(i for i in items if i["kind"] == "codex")
        assert codex["html"] == "<p>done: hello</p>"
        assert all("html" not in i for i in items if i["kind"] not in ("codex", "codex_note"))
        turn = requests(log, "turn/start")[0]["params"]
        assert turn["model"] == "model-a" and turn["effort"] == "low"
        d.stop(); await task
    run(body())


def test_delegation_user_message_is_hidden(env):
    tmp, _ = env

    async def body():
        d = make_daemon(tmp, [])
        task, c = await start(d, tmp)
        await c.cmd("send", text="DELEGATION")
        await asyncio.sleep(0.3)
        texts = [i["text"] for i in (await c.cmd("history"))["items"]]
        assert not any("realtime_delegation" in t for t in texts)
        d.stop(); await task
    run(body())


def test_model_and_effort_validation(env):
    tmp, log = env

    async def body():
        d = make_daemon(tmp, [])
        task, c = await start(d, tmp)
        assert not (await c.cmd("set_model", model="nope"))["ok"]
        assert not (await c.cmd("set_effort", effort="ultra"))["ok"]
        assert (await c.cmd("set_effort", effort="high"))["ok"]
        # model-b has no "low"; switching keeps "high" (supported) ...
        assert (await c.cmd("set_model", model="model-b"))["ok"]
        st = await c.cmd("state")
        assert st["model"] == "model-b" and st["effort"] == "high"
        updates = [r["params"] for r in requests(log, "thread/settings/update")]
        assert {"threadId": "thread-1", "effort": "high"} in updates
        assert {"threadId": "thread-1", "model": "model-b"} in updates
        d.stop(); await task
    run(body())


def test_model_switch_resets_unsupported_effort(env):
    tmp, _ = env

    async def body():
        d = make_daemon(tmp, [])
        task, c = await start(d, tmp)
        assert d.state.effort == "low"
        assert (await c.cmd("set_model", model="model-b"))["ok"]
        assert d.state.effort == "medium"  # model-b's default
        d.stop(); await task
    run(body())


def test_new_session_resets_history(env):
    tmp, _ = env

    async def body():
        d = make_daemon(tmp, [])
        task, c = await start(d, tmp)
        await c.cmd("send", text="first")
        await asyncio.sleep(0.3)
        r = await c.cmd("new_session")
        assert r["ok"] and r["threadId"] == "thread-2"
        texts = [i["text"] for i in (await c.cmd("history"))["items"]]
        assert not any("first" in t for t in texts)
        d.stop(); await task
    run(body())


def test_denied_review_can_be_approved(env):
    tmp, log = env

    async def body():
        d = make_daemon(tmp, [])
        task, c = await start(d, tmp)
        await c.cmd("send", text="REVIEW")
        await asyncio.sleep(0.3)
        items = (await c.cmd("history"))["items"]
        review = next(i for i in items if i["kind"] == "review")
        assert review["status"] == "denied" and review["detail"]["risk"] == "high"
        assert (await c.cmd("approve_denied", reviewId=review["detail"]["reviewId"]))["ok"]
        sent = requests(log, "thread/approveGuardianDeniedAction")[0]["params"]
        assert sent["event"]["reviewId"] == "rev-1"
        d.stop(); await task
    run(body())


def test_codex_crash_restarts_with_new_session(env):
    tmp, _ = env

    async def body():
        d = make_daemon(tmp, [])
        task, c = await start(d, tmp)
        first_app = d.app
        await c.cmd("send", text="CRASH")
        for _ in range(100):
            if d.app is not first_app and d.app.running and d.thread_id:
                break
            await asyncio.sleep(0.1)
        assert d.app is not first_app and d.app.running and d.thread_id
        # the reason survives into the new session's history
        texts = [i["text"] for i in d.history]
        assert texts[0].startswith("Codex stopped unexpectedly") and texts[1].startswith("New session")
        d.stop(); await task
    run(body())


def test_speaking_drives_spectrum_events(env):
    tmp, _ = env

    async def body():
        d = make_daemon(tmp, [])
        task, c = await start(d, tmp)
        await c.cmd("subscribe")
        await c.cmd("mute", value=False)
        await asyncio.sleep(0.2)
        voice = FakeVoice.instances[-1]
        t = np.arange(960) / 48000
        tone = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
        for _ in range(10):
            voice.on_audio(tone)
            await asyncio.sleep(0.02)
        spec = await c.wait_event(lambda e: e.get("event") == "spectrum")
        assert len(spec["bands"]) == 24 and max(spec["bands"]) > 0.3
        await c.wait_event(lambda e: e.get("event") == "state" and e.get("speaking"))
        await c.wait_event(lambda e: e.get("event") == "state" and e.get("speaking") is False
                           and e is not None, timeout=3)
        d.stop(); await task
    run(body())


def test_untested_codex_version_warns(env, monkeypatch):
    tmp, _ = env
    monkeypatch.setenv("FAKE_CODEX_VERSION", "codex-cli 9.9.9")

    async def body():
        d = make_daemon(tmp, [])
        task, c = await start(d, tmp)
        st = await c.cmd("state")
        assert any("9.9.9" in w for w in st["warnings"])
        d.stop(); await task
    run(body())


def test_second_daemon_refuses_same_socket(env):
    tmp, _ = env

    async def body():
        d = make_daemon(tmp, [])
        task, _c = await start(d, tmp)
        d2 = make_daemon(tmp, [])
        try:
            await d2._start_server()
            raise AssertionError("second daemon started")
        except SystemExit:
            pass
        d.stop(); await task
    run(body())


def test_spoken_mute_command_mutes_and_briefs_the_voice(env):
    tmp, _ = env

    async def body():
        osd = []
        d = make_daemon(tmp, osd)
        task, c = await start(d, tmp)
        await c.cmd("mute", value=False)
        for _ in range(50):
            if d.voice_status == "on":
                break
            await asyncio.sleep(0.05)
        assert "mute yourself" in FakeVoice.instances[-1].briefing
        tid = d.thread_id
        # a question about muting does nothing
        await d._on_notification("thread/realtime/transcript/done",
                                 {"threadId": tid, "role": "user", "text": "Are you able to mute yourself if I ask you to?"})
        assert d.muted is False
        # the agent saying it does nothing either
        await d._on_notification("thread/realtime/transcript/done",
                                 {"threadId": tid, "role": "assistant", "text": "Mute yourself."})
        assert d.muted is False
        await d._on_notification("thread/realtime/transcript/done",
                                 {"threadId": tid, "role": "user", "text": "Okay, mute yourself."})
        assert d.muted is True
        assert osd[-1] == (True, None)
        assert any(i["text"] == "Muted by voice command" for i in d.history)
        d.stop(); await task
    run(body())


def test_microphone_choice_is_validated_saved_and_applied_live(env, monkeypatch):
    tmp, _ = env

    async def fake_inputs():
        return {"default": "mic-a", "inputs": [{"id": "mic-a", "name": "Built-in"}, {"id": "mic-b", "name": "USB"}]}
    monkeypatch.setattr("codex_voice_agent.audio.list_inputs", fake_inputs)

    async def body():
        d = make_daemon(tmp, [])
        task, c = await start(d, tmp)
        r = await c.cmd("list_inputs")
        assert r["default"] == "mic-a" and len(r["inputs"]) == 2 and r["selected"] is None
        assert not (await c.cmd("set_input", device="nope"))["ok"]
        await c.cmd("mute", value=False)
        for _ in range(50):
            if d.voice_status == "on":
                break
            await asyncio.sleep(0.05)
        assert (await c.cmd("set_input", device="mic-b"))["ok"]
        assert d.state.input == "mic-b" and FakeVoice.instances[-1].switched_to == ["mic-b"]
        assert (await c.cmd("state"))["input"] == "mic-b"
        assert (await c.cmd("set_input", device=None))["selected"] is None  # back to system default
        assert FakeVoice.instances[-1].switched_to == ["mic-b", None]
        d.stop(); await task
    run(body())


def test_failed_microphone_switch_is_not_saved(env, monkeypatch):
    tmp, _ = env

    async def fake_inputs():
        return {"default": "mic-a", "inputs": [{"id": "mic-a", "name": "A"}, {"id": "mic-fails", "name": "Busy"}]}
    monkeypatch.setattr("codex_voice_agent.audio.list_inputs", fake_inputs)

    async def body():
        d = make_daemon(tmp, [])
        task, c = await start(d, tmp)
        await c.cmd("mute", value=False)
        for _ in range(50):
            if d.voice_status == "on":
                break
            await asyncio.sleep(0.05)
        r = await c.cmd("set_input", device="mic-fails")
        assert not r["ok"] and "could not switch" in r["error"]
        assert d.state.input is None and (await c.cmd("state"))["input"] is None
        d.stop(); await task
    run(body())


def test_failed_start_does_not_leak_codex_processes(env, monkeypatch):
    tmp, _ = env
    monkeypatch.setenv("FAKE_FAIL_THREAD_START", "1")

    async def body():
        d = make_daemon(tmp, [])
        task = asyncio.create_task(d.run())
        apps = []
        for _ in range(80):  # first attempt, then one retry after the 2 s backoff
            if d.app and d.app not in apps:
                apps.append(d.app)
            if len(apps) >= 2:
                break
            await asyncio.sleep(0.05)
        assert len(apps) >= 2
        await asyncio.sleep(0.3)
        assert apps[0].proc.returncode is not None  # the failed one was stopped
        assert any("not signed in" in w for w in d.warnings)
        d.stop(); await task
    run(body())


def test_requests_while_codex_is_down_get_an_answer(env):
    tmp, _ = env

    async def body():
        d = make_daemon(tmp, [])
        task, c = await start(d, tmp)
        await c.cmd("send", text="CRASH")
        await asyncio.wait_for(d.app.closed.wait(), 5)
        r = await c.cmd("send", text="hello?")
        assert r["ok"] is False and "not running" in r["error"]
        assert not any(i["text"] == "hello?" for i in d.history)  # not shown as sent
        assert (await c.cmd("state"))["ok"]  # the connection survived
        d.stop(); await task
    run(body())


def test_malformed_and_oversized_requests_are_answered(env):
    tmp, _ = env

    async def body():
        d = make_daemon(tmp, [])
        task, c = await start(d, tmp)
        for line in (b"[]\n", b"42\n", b"not json\n"):
            c.writer.write(line)
            await c.writer.drain()
            reply = json.loads(await asyncio.wait_for(c.reader.readline(), 5))
            assert reply["ok"] is False
        assert (await c.cmd("state"))["ok"]  # still usable afterwards
        r2, w2 = await asyncio.open_unix_connection(str(tmp / "ctl.sock"))
        w2.write(b'{"cmd": "send", "text": "' + b"x" * (2 << 20) + b'"}\n')
        await w2.drain()
        reply = json.loads(await asyncio.wait_for(r2.readline(), 5))
        assert reply["ok"] is False and reply["error"] == "request too large"
        w2.write(b'{"id": 7, "cmd": "state"}\n')  # the same connection keeps working
        await w2.drain()
        reply = json.loads(await asyncio.wait_for(r2.readline(), 5))
        assert reply["ok"] and reply["id"] == 7
        w2.close()
        d.stop(); await task
    run(body())


def test_lost_voice_mutes_and_says_why(env):
    tmp, _ = env

    async def body():
        osd = []
        d = make_daemon(tmp, osd)
        task, c = await start(d, tmp)
        await c.cmd("mute", value=False)
        for _ in range(50):
            if d.voice_status == "on":
                break
            await asyncio.sleep(0.05)
        link = FakeVoice.instances[-1]
        link.drop("connection failed")
        for _ in range(50):
            if d.muted:
                break
            await asyncio.sleep(0.05)
        assert d.muted and d.voice is None and d.voice_status == "error" and link.closed
        assert osd[-1] == (True, "Voice assistant disconnected")
        assert any("Voice connection lost" in i["text"] for i in d.history)
        # unmuting reconnects with a fresh link
        await c.cmd("mute", value=False)
        for _ in range(50):
            if d.voice_status == "on":
                break
            await asyncio.sleep(0.05)
        assert FakeVoice.instances[-1] is not link and FakeVoice.instances[-1].connected
        d.stop(); await task
    run(body())


def test_failed_voice_connect_mutes(env, monkeypatch):
    tmp, _ = env

    async def fail(self):
        raise OSError("no microphone")
    monkeypatch.setattr(FakeVoice, "connect", fail)

    async def body():
        osd = []
        d = make_daemon(tmp, osd)
        task, c = await start(d, tmp)
        await c.cmd("mute", value=False)
        for _ in range(50):
            if d.voice_status == "error":
                break
            await asyncio.sleep(0.05)
        assert d.muted and d.voice_status == "error"
        assert osd[-1] == (True, "Voice assistant could not connect")
        d.stop(); await task
    run(body())
