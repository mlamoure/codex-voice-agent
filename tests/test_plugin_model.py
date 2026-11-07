"""Tests for Model.js (the plugin's pure logic), run under node, and for the plugin as a whole."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODEL_JS = ROOT / "Model.js"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node not installed")


def js(expr: str):
    source = MODEL_JS.read_text().replace(".pragma library", "", 1)
    script = source + f"\nprocess.stdout.write(JSON.stringify({expr}))\n"
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


ST = {"threadId": "t", "muted": False, "voice": "on", "working": False, "speaking": False,
      "model": "a", "effort": "high",
      "models": [{"id": "a", "name": "A", "efforts": ["low", "high"]},
                 {"id": "b", "name": "B", "efforts": ["medium"]}]}


def test_mic_state_answers_is_it_listening():
    assert js("micState(false, {})") == "off"
    assert js(f"micState(true, {json.dumps(ST)})") == "listening"
    assert js(f"micState(true, {json.dumps({**ST, 'muted': True})})") == "muted"


def test_tooltip_layers_working_and_speaking_on_mute_state():
    t = js(f"tooltip(true, {json.dumps({**ST, 'muted': True, 'speaking': True, 'working': True})})")
    assert t.startswith("Codex Voice Agent: muted") and "working" in t and "speaking" in t
    assert js("tooltip(false, {})") == "Codex Voice Agent: not running"


def test_effort_options_follow_the_selected_model():
    assert [o["value"] for o in js(f"effortOptions({json.dumps(ST)})")] == ["low", "high"]
    assert [o["label"] for o in js(f"effortOptions({json.dumps({**ST, 'model': 'b'})})")] == ["Medium"]
    assert js(f"modelOptions({json.dumps(ST)})") == [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]


def test_card_text_keeps_the_end_of_long_replies():
    long = " ".join(f"word{i}" for i in range(200))
    out = js(f"cardText({json.dumps(long)}, 60)")
    assert out.startswith("…") and out.endswith("word199") and len(out) <= 61


def test_card_hold_time_grows_with_length_and_is_capped():
    assert js("cardHoldMs('hi', true)") < js("cardHoldMs('hi', false)")
    assert js("cardHoldMs('x '.repeat(1000), true)") == 15000


def test_clamp_bands():
    assert js("clampBands([2, -1, 0.5, 'x'], 5)") == [1, 0, 0.5, 0, 0]


def test_entry_styles():
    assert js("entryStyle('you').role") == "user"
    assert js("entryStyle('codex').markdown") is True
    assert js("entryStyle('command').mono") is True
    assert js("reviewLabel({status: 'denied', text: 'nope', detail: {risk: 'high'}})") == "denied (risk high): nope"


def test_input_options_put_the_system_default_first():
    info = {"default": "mic-a", "inputs": [{"id": "mic-a", "name": "Built-in"}, {"id": "mic-b", "name": "USB"}]}
    opts = js(f"inputOptions({json.dumps(info)})")
    assert opts[0] == {"value": "", "label": "System default (Built-in)"}
    assert [o["value"] for o in opts[1:]] == ["mic-a", "mic-b"]
    assert js("inputOptions(null)") == [{"value": "", "label": "System default"}]


def test_unmuted_without_a_voice_link_is_not_listening():
    for voice in ("error", "off"):
        st = {**ST, "muted": False, "voice": voice}
        assert js(f"micState(true, {json.dumps(st)})") == "muted"
        assert "disconnected" in js(f"statusLine(true, {json.dumps(st)})")
    assert js(f"micState(true, {json.dumps({**ST, 'voice': 'connecting'})})") == "listening"


def test_only_codex_entries_rendered_by_the_backend_are_rich_text():
    assert js('entryBody({kind: "codex", text: "*hi*", html: "<p><em>hi</em></p>"})') == \
        {"text": "<p><em>hi</em></p>", "rich": True}
    # no backend rendering (or not a Codex entry): the raw text, as plain text
    assert js('entryBody({kind: "codex", text: "<img src=x>"})') == {"text": "<img src=x>", "rich": False}
    assert js('entryBody({kind: "you_typed", text: "a", html: "<img src=x>"})') == {"text": "a", "rich": False}
    assert js('entryBody({kind: "command", text: "ls", html: "<b>x</b>"})')["rich"] is False


def test_only_web_links_open():
    assert js("isSafeLink('https://example.com')") is True
    assert js("isSafeLink('file:///etc/passwd')") is False
    assert js("isSafeLink('javascript:alert(1)')") is False


def test_shell_quote_neutralises_metacharacters():
    import subprocess
    nasty = "/tmp/a b/$(touch /tmp/pwned)/it's `x`"
    quoted = js(f"shellQuote({json.dumps(nasty)})")
    out = subprocess.run(["bash", "-c", f"printf %s {quoted}"], capture_output=True, text=True).stdout
    assert out == nasty


def test_update_text_only_when_a_newer_release_is_known():
    assert js("updateText({version: '2026.11.6'})") == ""
    assert js("updateText({version: '2026.11.6', update: {version: '2026.12.1', url: 'https://x'}})") == \
        "Codex Voice Agent 2026.12.1 is available (you have 2026.11.6)."


def test_dictation_pause_explains_resume_and_manual_mute():
    st = {**ST, 'muted': True, 'dictationPauses': ['Superwhisper'], 'userMuted': False}
    assert 'paused for Superwhisper' in js(f'tooltip(true, {json.dumps(st)})')
    assert 'resumes afterward' in js(f'statusLine(true, {json.dumps(st)})')
    st['userMuted'] = True
    assert 'manually muted' in js(f'statusLine(true, {json.dumps(st)})')


def test_dictation_settings_status():
    assert js('dictationStatus({})') == 'Backend unavailable'
    assert js('dictationStatus({dictationAutoPause:false})') == 'Automatic detection off'
    assert js('dictationStatus({dictationAutoPause:true,dictationMonitor:"not installed"})') == 'No supported app detected'
    assert 'ready' in js('dictationStatus({dictationAutoPause:true,dictationMonitor:"idle"})')
    assert js('dictationStatus({dictationAutoPause:true,dictationMonitor:"broken"})') == 'Monitoring unavailable'
