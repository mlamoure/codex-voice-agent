"""codex-voice-agent command line.

  codex-voice-agent daemon                 run the backend (the systemd user unit does this)
  codex-voice-agent mute [toggle|on|off]   what the mute key binding runs
  codex-voice-agent state | history        print JSON
  codex-voice-agent send TEXT...           type a request to Codex
  codex-voice-agent new-session
  codex-voice-agent watch                  print live events (debugging)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import socket
import sys

from .config import Config, State, socket_path


def request(cmd: dict, timeout: float = 30) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(socket_path()))
        sock.sendall((json.dumps({"id": 1, **cmd}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    if not buf.strip():
        raise ConnectionResetError("the backend closed the connection without answering")
    return json.loads(buf)


def _run_daemon(verbose: bool) -> int:
    from .daemon import Daemon

    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("aioice").setLevel(logging.WARNING)
    logging.getLogger("aiortc").setLevel(logging.WARNING)
    daemon = Daemon(Config.load(), State.load())

    async def main():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, daemon.stop)
        await daemon.run()

    asyncio.run(main())
    return 0


def _watch() -> int:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(str(socket_path()))
        sock.sendall(b'{"id": 1, "cmd": "subscribe"}\n')
        for line in sock.makefile("r"):
            msg = json.loads(line)
            if msg.get("event") != "spectrum":
                print(json.dumps(msg), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codex-voice-agent", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("daemon")
    d.add_argument("-v", "--verbose", action="store_true")
    m = sub.add_parser("mute")
    m.add_argument("value", nargs="?", default="toggle", choices=["toggle", "on", "off"])
    dp = sub.add_parser("dictation", help="acknowledged dictation pause hooks")
    ds = dp.add_subparsers(dest="action", required=True)
    ds.add_parser("acquire").add_argument("source")
    ds.add_parser("release").add_argument("token")
    sub.add_parser("state")
    sub.add_parser("history")
    s = sub.add_parser("send")
    s.add_argument("text", nargs="+")
    sub.add_parser("new-session")
    sub.add_parser("watch")
    args = parser.parse_args(argv)

    if args.command == "daemon":
        return _run_daemon(args.verbose)
    try:
        if args.command == "watch":
            return _watch()
        if args.command == "mute":
            value = {"toggle": "toggle", "on": True, "off": False}[args.value]
            reply = request({"cmd": "mute", "value": value})
        elif args.command == "dictation":
            payload = {"source": args.source} if args.action == "acquire" else {"token": args.token}
            reply = request({"cmd": "dictation_" + args.action, **payload})
        elif args.command == "state":
            reply = request({"cmd": "state"})
        elif args.command == "history":
            reply = request({"cmd": "history"})
        elif args.command == "send":
            reply = request({"cmd": "send", "text": " ".join(args.text)})
        else:
            reply = request({"cmd": "new_session"}, timeout=90)
    except (FileNotFoundError, ConnectionRefusedError, ConnectionResetError) as exc:
        if isinstance(exc, ConnectionResetError):
            print(f"codex-voice-agent: {exc}", file=sys.stderr)
            return 3
        if args.command == "mute":
            from .daemon import osd
            osd(True, "Voice assistant not running")
        print("codex-voice-agent is not running", file=sys.stderr)
        return 2
    except socket.timeout:
        print("codex-voice-agent did not answer", file=sys.stderr)
        return 3
    print(json.dumps(reply, indent=2))
    return 0 if reply.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
