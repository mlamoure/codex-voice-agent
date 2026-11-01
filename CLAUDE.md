# CLAUDE.md — codex-voice-agent

Assistant workflow for this repo. User docs are in README.md.

## Layout
- `src/codex_voice_agent/` — backend: `daemon.py` (session, mute/voice lifecycle, history, control
  socket), `voice.py` (WebRTC realtime link), `audio.py` (PipeWire, echo cancel, spectrum),
  `appserver.py` (codex app-server JSON-RPC), `config.py`, `cli.py`.
- Repository root = the omarchy-shell plugin `mlamoure.codex-voice-agent` (`service` +
  `bar-widget`): `manifest.json`, `*.qml`, `Model.js` (marketplace layout: one plugin, manifest at
  the root). `Service.qml` is the single instance that owns the socket and the reply card;
  `Widget.qml` exists once per monitor and only renders. Pure logic goes in `Model.js` (tested
  with node). The ID is permanent once published: it lives in `manifest.json` and `Model.PLUGIN_ID`
  (a test keeps them equal); `install.sh` OLD_IDS swaps earlier IDs in place.
- `tests/` — pytest; `fake_codex.py` stands in for `codex` (logs every request).

## Workflow
- Branch, test (`.venv/bin/python -m pytest`), lint (`.venv/bin/python -m pyflakes src tests` — the
  tests use a fake voice link, so a typo in `voice.py` only shows up there or live; the suite also
  runs `omarchy plugin validate` on the staged plugin files), PR. `main` is protected.
- Deploy to the running machine with `./install.sh` (copies the plugin, reinstalls the venv,
  restarts the service). Never edit the installed copies under `~/.config/omarchy/plugins` or
  `~/.local/share/codex-voice-agent`.
- Plugin errors: `journalctl --user -t omarchy-shell`. Backend: `journalctl --user -u codex-voice-agent`.
- Changed QML only shows after `omarchy-restart-shell` (a plugin rescan keeps the compiled
  components); `install.sh` restarts the shell when the plugin files changed.
- Check the bar visually: `grim -o <output> out.png` (the bar's output is in `hyprctl monitors`),
  then crop around the widget.

## Facts that cost a spike to learn (Codex 0.166.0)
- Run a plain `codex app-server` child: stdio is JSONL. The shared daemon socket speaks WebSocket.
- Voice: the websocket transport needs API-key auth (a ChatGPT sign-in has none), and
  `codex-voice-host`'s IPC is private, so we use the documented **WebRTC** transport with our
  own aiortc peer (audio track + `oai-events` data channel) and **`version: "v3"`** (v2 is
  refused for these calls; v1 lacks delegation). The answer SDP arrives as `thread/realtime/sdp`.
- Speech becomes a Codex turn by itself (`delegation.created` → `turn/started`).
  `thread/realtime/appendText` is context only (the model may make things up), so typed
  requests use `turn/start`.
- The voice model will happily claim abilities it lacks (e.g. "yes, I can mute myself"). The
  daemon handles spoken mute commands from the user transcript itself, and tells the model the
  real controls through `initialItems` (role `developer`, v3 only) at realtime start.
- `pw-record` needs `--raw` on stdout (otherwise a Sun AU header). The echo canceller lives
  as long as the `pw-cli` process that loaded it.
- Importing aiortc/PyAV takes ~2 s; the daemon pre-imports it so the first unmute is quick.
- Unix socket paths must be < 108 chars (use `$XDG_RUNTIME_DIR` in tests/harnesses).
- In some macOS-hosted VMs a Mac keyboard's record/dictation key and fn+F5 both arrive as plain
  `KEY_F5`, so binding F5 is the only way to use that key.

## Bumping the tested Codex version
Re-check voice end to end (unmute, speak a request that needs a command, hear the answer)
and `item/autoApprovalReview/*` before changing `TESTED_CODEX_VERSION`.
