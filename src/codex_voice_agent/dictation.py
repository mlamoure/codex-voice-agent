"""Optional dictation adapters. Only status is read; never audio or transcripts."""
from __future__ import annotations

import asyncio
import json
import shutil


def superwhisper_active(line: bytes) -> bool:
    """The public bar stream has an `alt` state. Only explicit idle releases us.

    Unknown states conservatively pause too: a new capture/processing phase must
    not accidentally enable the microphone. Malformed output is a failed monitor.
    """
    data = json.loads(line)
    if not isinstance(data, dict) or not isinstance(data.get("alt"), str) or not data["alt"]:
        raise ValueError("Superwhisper status has no state")
    return data["alt"] != "idle"


async def watch_superwhisper(report, command: str | None = None) -> None:
    """One supervised CLI stream, independent of shortcuts and bar instances.

    report(active, error): None means unknown, not an idle notification. No
    inactivity timeout: a healthy idle stream can be quiet indefinitely.
    """
    delay = 1
    while True:
        executable = command or shutil.which("superwhisper")
        if not executable:
            await report(None, "not installed")
            await asyncio.sleep(30)
            continue
        proc = None
        try:
            await report(None, "connecting")
            proc = await asyncio.create_subprocess_exec(
                executable, "status", "--bar", "--follow",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=65536)
            first = True
            while True:
                read = proc.stdout.readline()
                line = await asyncio.wait_for(read, 10) if first else await read
                if not line:
                    raise ConnectionError("status stream closed")
                active = superwhisper_active(line)
                first = False
                delay = 1
                await report(active, None)
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            await report(None, str(exc) or "status timed out")
        finally:
            if proc and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 2)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)
