"""Update notifications: ask GitHub for the latest release and compare it with ours.

Read-only and best effort: one HTTPS GET a day, no account, nothing downloaded or installed.
A failure (offline, rate limit, odd reply) is logged and ignored until the next check.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request

from . import __version__

log = logging.getLogger(__name__)

REPO_URL = "https://github.com/mlamoure/codex-voice-agent"
# Overridable for tests and for forks.
RELEASES_URL = os.environ.get("CODEX_VOICE_AGENT_RELEASES_URL",
                              "https://api.github.com/repos/mlamoure/codex-voice-agent/releases/latest")
MAX_REPLY = 1 << 20


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """'v2026.11.6' / '2026.11.1.1' -> (2026, 11, 6) / (2026, 11, 1, 1); anything else -> None."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(p) for p in match.group(1).split(".")) if match else None


def is_newer(candidate: str | None, current: str = __version__) -> bool:
    new, cur = parse_version(candidate), parse_version(current)
    return bool(new and cur and new > cur)


def latest_release(url: str | None = None, timeout: float = 15) -> dict | None:
    """{"version", "url"} of the latest published release, or None if it cannot be read."""
    request = urllib.request.Request(url or RELEASES_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"codex-voice-agent/{__version__}",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            data = json.loads(reply.read(MAX_REPLY))
    except (OSError, ValueError) as exc:
        log.info("update check failed: %s", exc)
        return None
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    version = tag[1:] if isinstance(tag, str) and tag.startswith("v") else tag
    if not parse_version(version):
        return None
    page = data.get("html_url")
    if not (isinstance(page, str) and page.startswith(REPO_URL + "/")):
        page = f"{REPO_URL}/releases"
    return {"version": version, "url": page}
