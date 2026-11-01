"""The JSON-RPC client: notification dispatch must never block reading replies."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

from codex_voice_agent.appserver import AppServer, AppServerError

FAKE = [sys.executable, str(Path(__file__).with_name("fake_codex.py"))]


def run(coro):
    return asyncio.run(coro)


def test_a_handler_may_call_codex_while_handling_a_notification():
    async def body():
        results = []
        app = None

        async def on_note(method, params):
            if method == "turn/started":
                # needs a reply that only the stdout reader can deliver
                t = time.monotonic()
                await app.call("thread/settings/update", {"threadId": "thread-1"}, timeout=5)
                results.append(time.monotonic() - t)

        app = AppServer(FAKE, on_note)
        await app.start()
        await app.call("thread/start", {})
        await app.call("turn/start", {"threadId": "thread-1", "input": [{"type": "text", "text": "hi"}]})
        for _ in range(50):
            if results:
                break
            await asyncio.sleep(0.05)
        await app.stop()
        assert results and results[0] < 1.0  # answered promptly, not after a timeout
    run(body())


def test_calls_fail_fast_once_codex_is_gone():
    async def body():
        app = AppServer(FAKE, lambda m, p: None)
        await app.start()
        await app.call("thread/start", {})
        with pytest.raises(AppServerError):
            await app.call("turn/start", {"threadId": "thread-1", "input": [{"type": "text", "text": "CRASH"}]},
                           timeout=5)
        await asyncio.wait_for(app.closed.wait(), 5)
        t = time.monotonic()
        with pytest.raises(AppServerError, match="not running"):
            await app.call("model/list", {})
        assert time.monotonic() - t < 0.5
        await app.stop()
    run(body())


def test_a_slow_answer_becomes_a_clear_error():
    async def body():
        app = AppServer(FAKE, lambda m, p: None)
        await app.start()
        app.proc.stdin.write(b"")  # noqa: keep the process; ask something it never answers:
        with pytest.raises(AppServerError, match="did not answer"):
            await app.call("thread/start", {}, timeout=0.000001)
        await app.stop()
    run(body())
