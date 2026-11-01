"""JSON-RPC client for a `codex app-server` child process (newline-delimited JSON on stdio).

We run our own child rather than the shared daemon: the daemon's control socket speaks
WebSocket, while stdio is plain JSONL. Threads are persisted in ~/.codex either way, so
`codex resume <id>` still opens them.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

Notification = Callable[[str, dict], Awaitable[None] | None]

# Server-initiated requests we never want to block on. The backend runs unattended:
# anything that would need a person is declined, and Codex carries on or reports it.
DECLINE = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "execCommandApproval": {"decision": "denied"},
    "applyPatchApproval": {"decision": "denied"},
    "mcpServer/elicitation/request": {"action": "decline"},
    "item/tool/requestUserInput": {"answers": {}},
}


class AppServerError(RuntimeError):
    pass


class AppServer:
    def __init__(self, command: list[str], on_notification: Notification,
                 client_name: str = "codex-voice-agent", client_version: str = "0"):
        self.command = command
        self.on_notification = on_notification
        self.client_name = client_name
        self.client_version = client_version
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._tasks: list[asyncio.Task] = []
        self._inbox: asyncio.Queue = asyncio.Queue()
        self.closed = asyncio.Event()

    async def start(self) -> dict:
        self.closed.clear()
        self.proc = await asyncio.create_subprocess_exec(
            *self.command, "app-server",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024)
        self._tasks = [asyncio.create_task(self._read_stdout()),
                       asyncio.create_task(self._read_stderr()),
                       asyncio.create_task(self._dispatch_notifications())]
        result = await self.call("initialize", {
            "clientInfo": {"name": self.client_name, "version": self.client_version},
            "capabilities": {"experimentalApi": True},
        })
        await self.notify("initialized")
        return result

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None and not self.closed.is_set()

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        async for line in self.proc.stderr:
            text = line.decode(errors="replace").rstrip()
            if text:
                log.debug("app-server: %s", text)

    async def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            async for line in self.proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    log.warning("app-server sent invalid JSON: %r", line[:200])
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                elif "id" in msg and "method" in msg:
                    task = asyncio.create_task(self._answer_server_request(msg))
                    self._tasks.append(task)
                    task.add_done_callback(lambda t: t in self._tasks and self._tasks.remove(t))
                elif "method" in msg:
                    # Handed to a separate, in-order dispatcher: a handler that itself calls
                    # Codex must never block the reader that would deliver the reply.
                    self._inbox.put_nowait((msg["method"], msg.get("params") or {}))
        finally:
            self.closed.set()
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(AppServerError("codex app-server exited"))
            self._pending.clear()

    async def _dispatch_notifications(self) -> None:
        while True:
            method, params = await self._inbox.get()
            try:
                res = self.on_notification(method, params)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # a bad handler must not stop the dispatcher
                log.exception("notification handler failed for %s", method)

    async def _answer_server_request(self, msg: dict) -> None:
        method = msg["method"]
        result = DECLINE.get(method)
        log.info("app-server request %s -> %s", method, "declined" if result else "unsupported")
        try:
            if result is None:
                await self._send({"id": msg["id"], "error": {"code": -32601, "message": f"{method} not supported"}})
            else:
                await self._send({"id": msg["id"], "result": result})
        except AppServerError:
            pass  # Codex went away; nothing to answer

    async def _send(self, obj: dict) -> None:
        if self.closed.is_set() or not self.proc or not self.proc.stdin or self.proc.stdin.is_closing():
            raise AppServerError("Codex is not running")
        try:
            self.proc.stdin.write((json.dumps(obj) + "\n").encode())
            await self.proc.stdin.drain()
        except (ConnectionError, OSError) as exc:
            raise AppServerError(f"Codex is not running ({exc})") from exc

    async def call(self, method: str, params: dict | None = None, timeout: float = 60) -> Any:
        if self.closed.is_set():
            raise AppServerError("Codex is not running")
        rid = self._next_id
        self._next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._send({"id": rid, "method": method, "params": params or {}})
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise AppServerError(f"Codex did not answer {method} within {timeout:.0f} s") from None
        finally:
            self._pending.pop(rid, None)
        if "error" in msg:
            err = msg["error"]
            raise AppServerError(f"{method}: {err.get('message', err)}")
        return msg["result"]

    async def notify(self, method: str, params: dict | None = None) -> None:
        await self._send({"method": method, "params": params or {}})

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            if self.proc.stdin and not self.proc.stdin.is_closing():
                self.proc.stdin.close()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), 3)
                except asyncio.TimeoutError:
                    self.proc.kill()
        for task in self._tasks:
            task.cancel()
        self.closed.set()
