#!/usr/bin/env bash
# Install (or update) Codex Voice Agent for the current user on Omarchy 4.
#
#   ./install.sh                         install or update everything (no mute key unless one exists)
#   ./install.sh --key "SUPER + SHIFT + M"   also bind a global mute key (any Hyprland key spec)
#   ./install.sh --key none              remove the mute key binding
#   ./install.sh --backend-only          only the backend service (the plugin came from `omarchy plugin add`;
#                                        this is also what happens when run from inside the plugins folder)
#   ./install.sh --uninstall             remove everything except your config and state
#
# What it touches (nothing else):
#   ~/.local/share/codex-voice-agent/venv            the backend
#   ~/.local/bin/codex-voice-agent                   symlink to the CLI
#   ~/.config/systemd/user/codex-voice-agent.service enabled for the graphical session
#   ~/.config/omarchy/plugins/codex-voice-agent      a copy of the plugin files (not with --backend-only)
#   ~/.config/omarchy/shell.json                     via `omarchy plugin enable`, once (not with --backend-only)
#   ~/.config/hypr/bindings.lua                      a marked block, only when you ask for a key
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/codex-voice-agent"
PLUGIN_DIR="$CONFIG_HOME/omarchy/plugins/codex-voice-agent"
UNIT_DIR="$CONFIG_HOME/systemd/user"
BINDINGS="$CONFIG_HOME/hypr/bindings.lua"
SHELL_JSON="$CONFIG_HOME/omarchy/shell.json"
PLUGIN_ID="$(jq -r .id "$REPO/manifest.json")"
# Earlier IDs of this plugin; one found in the bar is replaced by this one in the same slot.
OLD_IDS=(mike.copilot-agent mike.codex-voice-agent)
BEGIN_MARK="-- codex-voice-agent: begin (managed by codex-voice-agent/install.sh)"
END_MARK="-- codex-voice-agent: end"

KEY="keep"
UNINSTALL=0
BACKEND_ONLY=0
while (($#)); do
  case "$1" in
    --key) KEY="${2:?--key needs a key spec or none}"; shift 2 ;;
    --backend-only) BACKEND_ONLY=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 1 ;;
  esac
done

# Run from a clone that `omarchy plugin add` put in the plugins folder: that clone *is* the
# installed plugin, so only the backend is needed (copying would install it twice).
if [[ "$REPO/" == "$CONFIG_HOME/omarchy/plugins/"* ]]; then BACKEND_ONLY=1; fi

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

current_key() {  # the key in our marked block, if any
  [[ -f "$BINDINGS" ]] || return 0
  sed -n "\|$BEGIN_MARK|,\|$END_MARK|p" "$BINDINGS" | sed -n 's/^o\.bind("\([^"]*\)".*/\1/p' | head -1
}
remove_binding() {
  [[ -f "$BINDINGS" ]] && grep -qF -- "$BEGIN_MARK" "$BINDINGS" && sed -i "\|$BEGIN_MARK|,\|$END_MARK|d" "$BINDINGS"
  return 0
}
in_bar() { [[ -f "$SHELL_JSON" ]] && jq -e --arg id "$1" '[.bar.layout[]?[]?.id] | index($id)' "$SHELL_JSON" >/dev/null 2>&1; }

# ---------------------------------------------------------------- uninstall
if ((UNINSTALL)); then
  say "Stopping and removing the service"
  systemctl --user disable --now codex-voice-agent.service 2>/dev/null || true
  rm -f "$UNIT_DIR/codex-voice-agent.service"
  systemctl --user daemon-reload 2>/dev/null || true   # no user bus (e.g. over SSH): keep going
  if in_bar "$PLUGIN_ID"; then
    say "Removing the widget from the bar"
    omarchy plugin disable "$PLUGIN_ID" >/dev/null 2>&1 || warn "could not disable $PLUGIN_ID; run: omarchy plugin disable $PLUGIN_ID"
  fi
  [[ "$PLUGIN_DIR" -ef "$REPO" ]] || rm -rf "$PLUGIN_DIR"   # never delete the checkout we run from
  remove_binding
  rm -f "$HOME/.local/bin/codex-voice-agent"
  rm -rf "$PREFIX"
  command -v hyprctl >/dev/null && hyprctl reload >/dev/null 2>&1 || true
  say "Done. Kept $CONFIG_HOME/codex-voice-agent and ~/.local/state/codex-voice-agent."
  exit 0
fi

# ---------------------------------------------------------------- prerequisites
needed=(uv jq pw-record pw-play pw-cli pw-dump codex)
((BACKEND_ONLY)) || needed+=(omarchy omarchy-shell rsync)
for tool in "${needed[@]}"; do
  command -v "$tool" >/dev/null || die "$tool not found on PATH (see README: Requirements)"
done
# A key spec goes into a Lua string in bindings.lua: allow only what key names use.
if [[ "$KEY" != "keep" && "$KEY" != "none" && ! "$KEY" =~ ^[A-Za-z0-9_+:\ ]+$ ]]; then
  die "--key may only contain letters, digits, spaces, _ + and : (got: $KEY)"
fi

# ---------------------------------------------------------------- upgrade from copilot-agent
# Releases before 0.2 were called copilot-agent. Remove that install so both never run at
# once and carry its config and state over; its bar widget is replaced in place below.
migrate_dir() {  # old new
  if [[ -d "$1" && ! -e "$2" ]]; then mkdir -p "$(dirname "$2")"; mv "$1" "$2"; say "Moved $1 -> $2"; fi
}
if [[ -f "$UNIT_DIR/copilot-agent.service" ]]; then
  say "Removing the old copilot-agent service"
  systemctl --user disable --now copilot-agent.service >/dev/null 2>&1 || true
  rm -f "$UNIT_DIR/copilot-agent.service"
  systemctl --user daemon-reload
fi
migrate_dir "$CONFIG_HOME/copilot-agent" "$CONFIG_HOME/codex-voice-agent"
migrate_dir "${XDG_STATE_HOME:-$HOME/.local/state}/copilot-agent" "${XDG_STATE_HOME:-$HOME/.local/state}/codex-voice-agent"
rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/copilot-agent"
rm -f "$HOME/.local/bin/copilot-agent"
if [[ -f "$BINDINGS" ]] && grep -qF -- "-- copilot-agent: begin" "$BINDINGS"; then
  old_key="$(sed -n '\|-- copilot-agent: begin|,\|-- copilot-agent: end|p' "$BINDINGS" | sed -n 's/^o\.bind("\([^"]*\)".*/\1/p' | head -1)"
  sed -i '\|-- copilot-agent: begin|,\|-- copilot-agent: end|d' "$BINDINGS"
  [[ "$KEY" == "keep" && -n "$old_key" ]] && KEY="$old_key"   # keep the key the user had
  say "Removed the old copilot-agent key binding"
fi

# ---------------------------------------------------------------- backend
say "Installing the backend into $PREFIX/venv"
mkdir -p "$PREFIX"
[[ -x "$PREFIX/venv/bin/python" ]] || uv venv -q --python 3.13 "$PREFIX/venv"
# Stale build output in the checkout can end up in the wheel (old module names); start clean.
rm -rf "$REPO/build" "$REPO"/src/*.egg-info
uv pip install -q --python "$PREFIX/venv/bin/python" --reinstall-package codex-voice-agent "$REPO"
mkdir -p "$HOME/.local/bin"
ln -sf "$PREFIX/venv/bin/codex-voice-agent" "$HOME/.local/bin/codex-voice-agent"

# ---------------------------------------------------------------- plugin
slot_of() {  # "<section> <index>" of a widget in the bar, if it is there
  [[ -f "$SHELL_JSON" ]] || return 0
  jq -r --arg id "$1" '.bar.layout // {} | to_entries[] | select(.value | type == "array")
    | .key as $s | (.value | map(.id) | index($id)) as $i | select($i != null) | "\($s) \($i)"' \
    "$SHELL_JSON" 2>/dev/null | head -1
}

if ((BACKEND_ONLY)); then
  say "Skipping the bar plugin (--backend-only)"
else
  # 1. An earlier ID in the bar: remember its slot and take it out while the shell still
  #    knows it (its files are about to be replaced).
  old_slot=""
  for old in "${OLD_IDS[@]}"; do
    [[ "$old" == "$PLUGIN_ID" ]] && continue
    if in_bar "$old"; then
      [[ -n "$old_slot" ]] || old_slot="$(slot_of "$old")"
      omarchy plugin disable "$old" >/dev/null 2>&1 && say "Removed the old widget ($old)" \
        || warn "could not remove the old widget; run: omarchy plugin disable $old"
    fi
  done
  rm -rf "$CONFIG_HOME/omarchy/plugins/copilot-agent"

  # 2. Stage only the plugin's own files from the repository root and validate that copy
  #    (a development checkout also holds a venv full of symlinks, which the validator
  #    rightly refuses). A copy, not a link: the shell must not depend on the checkout.
  say "Installing the bar plugin into $PLUGIN_DIR"
  stage="$(mktemp -d)"
  trap 'rm -rf "$stage"' EXIT
  rsync -a --include='manifest.json' --include='*.qml' --include='*.js' --include='LICENSE' \
    --include='README.md' --include='preview.*' --exclude='*' "$REPO/" "$stage/"
  omarchy plugin validate "$stage" || die "plugin failed validation"
  mkdir -p "$PLUGIN_DIR"
  changed="$(rsync -a --delete --itemize-changes "$stage/" "$PLUGIN_DIR/")"
  omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true

  # 3. Put it in the bar: the old widget's slot, else before the clock.
  if in_bar "$PLUGIN_ID"; then
    say "Widget already in the bar"
    if [[ -n "$changed" ]]; then
      # A rescan keeps QML the shell has already compiled; a restart reloads it.
      say "Plugin files changed: restarting omarchy-shell"
      omarchy-restart-shell >/dev/null 2>&1 || warn "shell not restarted (locked?); run omarchy-restart-shell"
    fi
  else
    placement=(--section right --before omarchy.clock)
    [[ -n "$old_slot" ]] && placement=(--section "${old_slot% *}" --index "${old_slot#* }")
    enabled=0
    for _ in 1 2 3 4 5; do  # the rescan is asynchronous
      if omarchy plugin enable "$PLUGIN_ID" "${placement[@]}" >/dev/null 2>&1 \
         || omarchy plugin enable "$PLUGIN_ID" --section right >/dev/null 2>&1; then
        enabled=1; break
      fi
      sleep 1
    done
    if ((enabled)); then
      where=""
      for _ in $(seq 1 20); do  # the shell writes shell.json a moment after enable returns
        where="$(slot_of "$PLUGIN_ID")"
        [[ -n "$where" ]] && break
        sleep 0.25
      done
      if [[ -n "$old_slot" && "$where" != "$old_slot" ]]; then
        warn "the widget is at '$where', not the old widget's slot '$old_slot'; move it with: omarchy bar move $PLUGIN_ID"
      else
        say "Added the widget to the bar ($where)"
      fi
      # A changed manifest id in an already-loaded folder needs a fresh shell to show.
      [[ -n "$old_slot" ]] && { omarchy-restart-shell >/dev/null 2>&1 || true; }
    else
      warn "could not add the widget; run: omarchy plugin enable $PLUGIN_ID --section right"
    fi
  fi
fi

# ---------------------------------------------------------------- service
say "Installing and (re)starting the user service"
mkdir -p "$UNIT_DIR"
install -m 0644 "$REPO/systemd/codex-voice-agent.service" "$UNIT_DIR/codex-voice-agent.service"
systemctl --user daemon-reload
systemctl --user enable codex-voice-agent.service >/dev/null 2>&1
systemctl --user restart codex-voice-agent.service

# ---------------------------------------------------------------- mute key (opt-in)
[[ "$KEY" == "keep" ]] && KEY="$(current_key)" && KEY="${KEY:-none}"
remove_binding
if [[ "$KEY" != "none" ]]; then
  [[ -f "$BINDINGS" ]] || die "$BINDINGS not found; is this Omarchy 4?"
  say "Binding $KEY to toggle mute"
  cat >> "$BINDINGS" <<EOF
$BEGIN_MARK
o.bind("$KEY", "Voice assistant mute", "$HOME/.local/bin/codex-voice-agent mute toggle", { locked = true })
$END_MARK
EOF
else
  say "No mute key bound (add one with --key \"SUPER + SHIFT + M\"; the bar icon toggles mute too)"
fi
command -v hyprctl >/dev/null && hyprctl reload >/dev/null 2>&1 || true

say "Installed. It starts muted; ${KEY/none/right-click the bar icon} to start listening."
