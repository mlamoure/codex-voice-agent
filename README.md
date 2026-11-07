# Codex Voice Agent

Talk to [Codex](https://github.com/openai/codex) from the Omarchy bar. Codex Voice Agent keeps
a Codex session running in the background with a microphone you switch on and off. Speak a
request, and Codex does the work (reading files, running commands, answering questions) and
tells you the answer out loud. A small face in the bar always shows whether it is listening.

![Codex Voice Agent: the face in the bar, the reply card and the panel](preview.png)

> **Unofficial.** This project is not made by, affiliated with, or endorsed by OpenAI or by
> Omarchy/37signals. "Codex" refers to OpenAI's Codex CLI, which you install and sign in to
> yourself.

> **Experimental.** Voice uses the Codex app-server's realtime API, which Codex marks as
> experimental. A Codex update can change it; if voice stops working after a Codex update,
> check for a newer release of this project.

## Features

- **Always-on session, starts muted.** It starts with your desktop session, muted, with no
  voice connection open. The first unmute connects in about a second.
- **Listening state at a glance.** The bar icon is a face made of code braces:

  | Icon | Meaning |
  |---|---|
  | `{• •}` in the accent colour, eyes open (the odd blink) | Listening |
  | Dimmed, eyes closed | Muted: it cannot hear you |
  | Grey, eyes closed | The backend is not running |
  | Eyes glance from side to side | Codex is working |
  | Eyes move with the voice | The agent is speaking |

  ![The bar icon listening, muted, working and speaking](docs/screenshots/icon-states.png)

- **Three ways to mute:** an optional global key, a right-click on the bar icon, or saying
  "mute yourself" / "stop listening" / "go to sleep". Each change shows an on-screen message.
- **Reply card.** While the agent answers, a small card in the top-right corner shows its
  words and a live visualizer of its voice. It stays hidden over fullscreen windows.

  ![The reply card with the voice visualizer](docs/screenshots/reply-card.png)
- **Panel** (left-click the icon): status, mute, **Model** and **Thinking** level, a **Settings** view for microphone, automatic dictation pause and reply-card preferences,
  the session history (what you said, what Codex ran, its answers, safety-review decisions),
  a box to type a request, **New session**, and **Open in terminal** (`codex resume` this
  session). The cursor starts in the request box, so you can open the panel and type; Escape
  closes it.

  ![The panel: status, mute, model, thinking level and microphone](docs/screenshots/panel.png)
- **Echo cancellation** on the microphone, so the agent does not hear itself through your
  speakers (or a video playing in the background).
- **Update notices.** Once a day it checks GitHub for a newer release. When there is one, the
  panel says so (with links to the release notes and the update steps) and you get one desktop
  notification per new version. Nothing is downloaded or installed for you.

## Requirements

- **Omarchy 4** (the Quickshell-based `omarchy-shell` bar).
- **Codex CLI**, installed and signed in (`codex login`). Last tested with Codex **0.166.0**, signed
  in with a ChatGPT account. An API key is not needed.
- **uv** (fetches Python 3.13 for the backend), **jq**, **rsync**, and the **PipeWire** tools
  `pw-record`, `pw-play`, `pw-cli` and `pw-dump`. All are in the Arch repositories:
  `omarchy-pkg-add uv jq rsync pipewire` (Omarchy ships PipeWire already).
- A microphone and speakers or headphones.
- Tested on aarch64 Omarchy. x86_64 should work the same (the Python dependencies, `aiortc`,
  `numpy` and the pure-Python `markdown-it-py`, ship prebuilt wheels for both) but has not been
  tested yet.

## Install

### From the Omarchy plugin marketplace

```bash
omarchy plugin add https://github.com/mlamoure/codex-voice-agent.git --enable
~/.config/omarchy/plugins/mlamoure.codex-voice-agent/install.sh
```

`omarchy plugin add` only clones and enables the bar plugin; it never runs install steps. The
second command installs the backend service it needs (run from the plugins folder, the
installer sets up only the backend). Add `--key "SUPER + SHIFT + M"` to bind a mute key.

### From a clone

```bash
git clone https://github.com/mlamoure/codex-voice-agent.git
cd codex-voice-agent
./install.sh
```

The installer sets up the backend, copies the bar plugin into
`~/.config/omarchy/plugins/codex-voice-agent`, adds the widget to the right side of your bar
(before the clock), and starts the `codex-voice-agent` user service. It does **not** change
your keybindings unless you ask for a mute key:

```bash
./install.sh --key "SUPER + SHIFT + M"     # any Hyprland key spec, e.g. "XF86AudioMicMute" or "F5"
```

The binding goes into `~/.config/hypr/bindings.lua` inside a clearly marked block, which the
installer keeps on later runs and removes with `--key none` or `--uninstall`.

`./install.sh --backend-only` installs just the backend service, whatever folder it runs from.

### Updating

The panel shows when a newer release is out. To update:

```bash
# marketplace install
omarchy plugin update mlamoure.codex-voice-agent
~/.config/omarchy/plugins/mlamoure.codex-voice-agent/install.sh

# clone install
cd codex-voice-agent && git pull && ./install.sh
```

`omarchy plugin update` only refreshes the bar plugin; the `install.sh` run updates the backend
service and restarts it.

### Uninstall

```bash
./install.sh --uninstall
```

This stops and removes the service, the widget, the key binding, the CLI link and the backend.
For a marketplace install, run it from `~/.config/omarchy/plugins/mlamoure.codex-voice-agent/`
and then `omarchy plugin remove mlamoure.codex-voice-agent`.
Your settings (`~/.config/codex-voice-agent`) and saved choices
(`~/.local/state/codex-voice-agent`) are kept; delete them by hand if you want.

## Using it

1. Unmute: your mute key, a right-click on the bar icon, or **Unmute** in the panel.
2. Talk. Ask anything you would ask Codex: "what branch is this repo on?", "find the TODOs in
   the backend and summarize them", "is my disk getting full?". Short questions are answered
   directly; anything that needs work is handed to Codex, which runs it and reports back.
3. Mute again when you are done. While muted the agent hears nothing, but a task already
   running carries on and it can still speak the result.

If the voice connection drops (network trouble, the microphone disappearing), the agent mutes
itself and shows "Voice assistant disconnected", so the face never claims to listen when
nothing is. Unmute to reconnect.

**Typing instead of talking:** use the box at the bottom of the panel, or
`codex-voice-agent send "your request"`. Typed requests go straight to Codex.

### Pause automatically for dictation

Superwhisper for Linux is detected automatically. While it reports a non-idle state
(recording or processing), the agent sends silence instead of your microphone audio.
The icon closes its eyes and the panel says **Paused for Superwhisper**. Once Superwhisper
reports idle, listening resumes only if you have not manually muted it. During a pause,
the panel offers **Stay muted** or **Resume after dictation**; the mute key and right-click
change the same choice. A temporary pause never cancels a Codex task. The agent can still
speak a reply, just as when manually muted.

One backend watcher runs `superwhisper status --bar --follow`; it does not change shortcuts,
read dictation history, or depend on a bar widget or monitor layout. Use **Settings → Automatically pause for dictation** to change automatic monitoring
immediately, without restarting Codex. The choice survives restarts and overrides
`dictation_auto_pause` in config.toml; the file supplies the default until you make a UI choice.
Turning it off during a dictation pause leaves the agent manually muted. Explicit acquire/release
hooks still work when automatic detection is off.
The panel/state output reports the monitor's status. Missing Superwhisper leaves normal
manual operation available. A broken status stream latches manual mute; reconnecting alone
will not turn the microphone back on. Re-enable listening explicitly after recovery.

**Timing:** this is reactive status monitoring, not exclusive ownership of the microphone.
Some audio may already have reached Codex before the recording notification arrives. It
cannot guarantee zero overlap. Queued microphone frames are discarded when muting/resuming.
For strict sequencing, a transcription tool needs a pre-record hook that waits for the
pause acknowledgement before starting capture. Already transmitted audio cannot be recalled.

| Integration | Support |
|---|---|
| Superwhisper Linux CLI exposing `status --bar --follow` JSON with an `alt` state | Automatic; only explicit `idle` resumes, unfamiliar states keep the agent paused |
| Other tools with pre-record/finish hooks | Use the acknowledged pause API below |
| Other tools without hooks or status integration | Manual mute; no automatic detection is claimed |
| macOS Superwhisper controlling a Linux VM | Not detected by the Linux adapter |

The automatic adapter reads the CLI's status only; cloud and offline dictation use the same
mechanism. Backend tests cover simulated failures and state transitions. The live integration
is verified on aarch64 Omarchy; x86_64 follows the same CLI contract but is not yet live-tested.

### Integrating another transcription tool

Before recording, run `codex-voice-agent dictation acquire "Tool name"`. Check its successful
exit status and JSON `ok: true`, then save its `token`. Only then start capture. In the
finish/cancel/error hook, run `codex-voice-agent dictation release TOKEN` with that exact token.
Each acquisition has its own token: overlapping recordings cannot release each other's pause.
These commands also work when automatic Superwhisper detection is disabled.

The same protocol is available over the user's private Unix control socket:

```json
{"id":1,"cmd":"dictation_acquire","source":"Example transcriber"}
{"id":2,"cmd":"dictation_release","token":"TOKEN_FROM_ACQUIRE_REPLY"}
```

The acquire response includes `ok`, `token`, and `muted: true`. Release returns the effective
`muted` state, which may remain true due to manual mute or another tool. No speech or transcript
is needed. Sources are display labels, limited to 64 ASCII letters/numbers/spaces/`._-` and
must start with a letter or number. At most 128 pauses may be outstanding.

Hooks must fail closed: if acquire fails, do not start recording. Tokens do not expire or get
released when the hook command exits, since recording usually continues in another process.
Always retain the token for cleanup. If a recorder crashes, release its saved token only after
confirming capture has stopped. Lost tokens require restarting the voice-agent service, which
starts muted. A service restart invalidates old tokens; it never automatically restores listening.
This API orders *future* microphone transmission; it cannot retract audio already in flight.

**Settings:** open **⚙ Settings** in the panel. Model and Thinking stay visible.
Microphone, automatic dictation pause, reply-card visibility, version and update details
live here. **Back** returns to the conversation without discarding your draft or scroll position.
Escape closes an open dropdown first, then returns from Settings, then closes the panel.

**Choosing a microphone:** **Settings → Microphone** starts with *System default (…)*,
which follows whatever input your system uses. Pick another device to override it; the choice
is remembered and applied immediately.

**Model and thinking level** apply from the next request, including spoken ones, and are
remembered.

**Sessions:** every start of the service begins a new Codex session in your working directory.
Old sessions remain available with `codex resume`.

## Safety and privacy

- **What Codex may do.** Sessions use Codex's workspace sandbox (`workspace-write`): normal
  work inside the working directory runs without asking. Anything outside it (other folders,
  system commands) goes to Codex's **auto-review**, an AI reviewer that judges the risk and
  approves or denies it. Denials show in the panel with **Approve anyway**. Your own
  `~/.codex/config.toml` is not changed. To give the agent full access instead, set
  `permissions = "full"` (see Configuration); think twice, since it acts on spoken words.
- **Where your audio goes.** While unmuted, your microphone audio streams to OpenAI's realtime
  service through Codex, and the agent's replies stream back. Audio is never saved to disk;
  the text of requests that reach Codex is kept in its session history under `~/.codex`, as
  for any Codex session. While muted only silence is sent. The microphone itself stays open locally for 15 minutes
  after you mute (so unmuting is instant) and is then closed until the next unmute.
- **Nothing in Codex's answers is loaded automatically.** What Codex reads can steer what it
  writes, so the reply card shows answers as plain text, and the panel shows Codex's Markdown
  only after the backend has rendered it with every image replaced by its description and raw
  HTML shown as text. Links open only when you click them, and only `http(s)` links.
- **Update check.** Once a day the service sends one anonymous HTTPS request to the GitHub API
  (`api.github.com`) for the latest release. Turn it off with `check_updates = false`.
- **The bar plugin runs unsandboxed** inside `omarchy-shell`, like every Omarchy plugin. Read
  the code before installing it.

## Configuration

Optional. Create `~/.config/codex-voice-agent/config.toml`, then
`systemctl --user restart codex-voice-agent`:

```toml
workdir = "~/projects"               # where Codex works (default: your home directory)
permissions = "auto"                 # "auto" (sandbox + auto-review) or "full" (no sandbox, never asks)
extra_writable_roots = ["/tmp"]      # extra folders writable without review in "auto"
idle_voice_minutes = 15              # close the voice connection after this long muted (0 = never)
echo_cancel = true                   # cancel the speakers' sound out of the microphone
voice = "marin"                      # a realtime voice name (default: Codex's)
osd = true                           # on-screen messages when muting and unmuting
voice_mute_command = true            # "mute yourself" / "stop listening" mutes the agent
codex_command = ["codex"]            # how to run Codex, if it is not on the service's PATH
check_updates = true                 # check GitHub once a day for a newer release
dictation_auto_pause = true          # default; a saved Settings choice overrides it
```

## Command line

```
codex-voice-agent mute [toggle|on|off]    what the mute key runs
codex-voice-agent state                   current state as JSON
codex-voice-agent history                 this session's history as JSON
codex-voice-agent send "text"             type a request to Codex
codex-voice-agent new-session             start a fresh Codex session
codex-voice-agent watch                   print live events (debugging)
codex-voice-agent dictation acquire NAME   pause and return a unique token
codex-voice-agent dictation release TOKEN  release only that pause
codex-voice-agent daemon [-v]             the service itself
```

## Troubleshooting

| Symptom | What to check |
|---|---|
| Grey icon / "not running" | `systemctl --user status codex-voice-agent`, then `journalctl --user -u codex-voice-agent -e` |
| "Codex unavailable" in the panel | The service runs with its own PATH: `~/.local/share/mise/shims`, `~/.local/bin`, `/usr/share/omarchy/bin`, `/usr/local/bin`, `/usr/bin`. If `codex` lives elsewhere (`command -v codex`), set `codex_command = ["/full/path/to/codex"]`. Is it signed in? `codex login status` |
| "Voice could not start" / it mutes itself right away | The panel history has the reason. Usually the microphone (pick another in the panel) or a Codex version with a changed realtime API |
| A warning about config.toml in the panel | A setting has a typo or the wrong type; that setting falls back to its default until fixed |
| It does not hear you | Is it unmuted (accent face)? Is the right microphone selected? Test the device with `pw-record --target <device> test.wav` |
| It hears itself / talks over itself | Make sure `echo_cancel` is on; use headphones in a very echoey room |
| Widget missing after install | `omarchy plugin enable <plugin-id> --section right`; QML errors are in `journalctl --user -t omarchy-shell` |

Run the backend in the foreground with debug logs: `systemctl --user stop codex-voice-agent`,
then `codex-voice-agent daemon -v`.

## How it works

```
omarchy-shell plugin (QML)             backend: codex-voice-agent (Python)             codex app-server (child)
  Service.qml ─ Unix socket ──────────►  session, mute, history, control socket ─ JSONL ─►  thread, turns, auto-review
  Widget.qml, ReplyCard.qml              voice link: WebRTC peer (aiortc) ◄── SDP ────►  realtime v3 session
                                         mic: pw-record (+ echo canceller)                  └─ hands spoken requests
                                         speaker: pw-play through a jitter buffer              to Codex turns
```

- The backend starts its own `codex app-server` and talks JSON-RPC to it over stdio.
- Voice uses the app-server's documented WebRTC transport with the backend's own peer
  connection and realtime protocol v3, which works with a ChatGPT sign-in.
- PipeWire's echo canceller runs in monitor mode: it uses what your speakers play as its
  reference and is never placed in the playback path.
- The plugin keeps no state of its own; it renders what the backend reports over
  `$XDG_RUNTIME_DIR/codex-voice-agent.sock`.

## Development

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m pytest              # a fake codex drives the daemon; node is needed for the plugin-logic tests
.venv/bin/python -m pyflakes src tests
```

The repository root is the plugin (`manifest.json` and the QML files); the Python backend lives
in `src/`. The test suite stages the plugin files the way `install.sh` does and runs
`omarchy plugin validate` on them (validating the checkout itself fails once it holds a
`.venv`, since plugin folders may not contain symlinks).

## Versions

Releases use date versions, `YEAR.MONTH.DAY` (for example `2026.10.31`), which sort and
compare like semantic versions.

## License

[MIT](LICENSE)
