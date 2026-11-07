"""Settings: an optional user config file plus a small state file the panel writes.

~/.config/codex-voice-agent/config.toml   (optional, hand-edited; see README)
~/.local/state/codex-voice-agent/state.json (model / thinking level / reply-card choice)
"""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()


def _xdg(var: str, default: Path) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else default


CONFIG_FILE = _xdg("XDG_CONFIG_HOME", HOME / ".config") / "codex-voice-agent" / "config.toml"
STATE_FILE = _xdg("XDG_STATE_HOME", HOME / ".local" / "state") / "codex-voice-agent" / "state.json"


def runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")


def socket_path() -> Path:
    return runtime_dir() / "codex-voice-agent.sock"


def default_workdir() -> Path:
    """Where Codex works when config.toml names no `workdir`: the home directory."""
    return HOME


@dataclass
class Config:
    workdir: Path = field(default_factory=default_workdir)
    # "auto": workspace-write sandbox + on-request approvals reviewed by Codex auto_review.
    # "full": danger-full-access, never ask (what the terminal sessions use).
    permissions: str = "auto"
    extra_writable_roots: list[str] = field(default_factory=lambda: ["/tmp"])
    idle_voice_minutes: float = 15.0
    echo_cancel: bool = True
    voice: str | None = None
    codex_command: list[str] = field(default_factory=lambda: ["codex"])
    osd: bool = True
    # mute when the user says "mute yourself" / "stop listening" / "go to sleep"
    voice_mute_command: bool = True
    # check GitHub once a day for a newer release and say so in the panel
    dictation_auto_pause: bool = True
    check_updates: bool = True
    problems: list[str] = field(default_factory=list)  # config errors, shown in the panel

    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> "Config":
        """Read config.toml. Never raises: a problem is noted in `problems` (shown in the
        panel) and that setting keeps its default, so a typo cannot crash-loop the service."""
        cfg = cls()
        cfg.problems = []
        if not path.exists():
            return cfg
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            cfg.problems.append(f"{path} could not be read ({exc}); using defaults")
            return cfg

        def take(key, kind, convert=lambda v: v):
            if key not in data:
                return None
            value = data[key]
            if not isinstance(value, kind) or isinstance(value, bool) and kind is not bool:
                cfg.problems.append(f"config: '{key}' has the wrong type; using the default")
                return None
            return convert(value)

        workdir = take("workdir", str, lambda v: Path(os.path.expanduser(v)))
        if workdir is not None:
            if workdir.is_dir():
                cfg.workdir = workdir
            else:
                cfg.problems.append(f"config: workdir {workdir} does not exist; using {cfg.workdir}")
        permissions = take("permissions", str)
        if permissions is not None:
            if permissions in ("auto", "full"):
                cfg.permissions = permissions
            else:
                cfg.problems.append("config: permissions must be \"auto\" or \"full\"; using \"auto\"")
        roots = take("extra_writable_roots", list)
        if roots is not None:
            cfg.extra_writable_roots = [os.path.expanduser(str(p)) for p in roots]
        idle = take("idle_voice_minutes", (int, float))
        if idle is not None:
            cfg.idle_voice_minutes = max(0.0, float(idle))
        for key in ("echo_cancel", "osd", "voice_mute_command", "check_updates", "dictation_auto_pause"):
            value = take(key, bool)
            if value is not None:
                setattr(cfg, key, value)
        voice = take("voice", str)
        if voice:
            cfg.voice = voice
        command = take("codex_command", list)
        if command:
            cfg.codex_command = [str(x) for x in command]
        return cfg
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        if "workdir" in data:
            cfg.workdir = Path(os.path.expanduser(str(data["workdir"])))
        if data.get("permissions") in ("auto", "full"):
            cfg.permissions = data["permissions"]
        if isinstance(data.get("extra_writable_roots"), list):
            cfg.extra_writable_roots = [os.path.expanduser(str(p)) for p in data["extra_writable_roots"]]
        if "idle_voice_minutes" in data:
            cfg.idle_voice_minutes = float(data["idle_voice_minutes"])
        if "echo_cancel" in data:
            cfg.echo_cancel = bool(data["echo_cancel"])
        if data.get("voice"):
            cfg.voice = str(data["voice"])
        if isinstance(data.get("codex_command"), list) and data["codex_command"]:
            cfg.codex_command = [str(x) for x in data["codex_command"]]
        if "osd" in data:
            cfg.osd = bool(data["osd"])
        if "voice_mute_command" in data:
            cfg.voice_mute_command = bool(data["voice_mute_command"])
        return cfg

    def thread_permissions(self) -> dict:
        """thread/start parameters for the chosen permission profile."""
        if self.permissions == "full":
            return {"sandbox": "danger-full-access", "approvalPolicy": "never"}
        return {
            "sandbox": "workspace-write",
            "approvalPolicy": "on-request",
            "approvalsReviewer": "auto_review",
            "config": {
                "sandbox_workspace_write": {
                    "writable_roots": self.extra_writable_roots,
                    "network_access": True,
                }
            },
        }


@dataclass
class State:
    """Choices made in the panel; survive restarts (the session itself does not)."""
    model: str | None = None
    effort: str | None = None
    dictation_auto_pause: bool | None = None  # panel override; None inherits config.toml
    reply_card: bool = True
    input: str | None = None  # microphone (PipeWire node name); None = system default
    notified_update: str | None = None  # newest release already announced with a notification

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> "State":
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()

        def text(key):
            value = data.get(key)
            return value if isinstance(value, str) and value else None
        pause = data.get("dictation_auto_pause")
        return cls(dictation_auto_pause=pause if isinstance(pause, bool) else None,
                   model=text("model"), effort=text("effort"),
                   reply_card=data.get("reply_card") is not False, input=text("input"),
                   notified_update=text("notified_update"))

    def save(self, path: Path = STATE_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"model": self.model, "effort": self.effort,
                                   "reply_card": self.reply_card, "input": self.input,
                                   "dictation_auto_pause": self.dictation_auto_pause,
                                   "notified_update": self.notified_update}, indent=2))
        tmp.replace(path)
