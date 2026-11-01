
import numpy as np

from codex_voice_agent import cli
from codex_voice_agent.audio import BANDS, peak, spectrum_bands
from codex_voice_agent.config import Config, State


def test_spectrum_puts_a_tone_in_the_right_band():
    t = np.arange(1024) / 48000
    edges = np.geomspace(90, 7500, BANDS + 1)
    expected = 16
    centre = float(np.sqrt(edges[expected] * edges[expected + 1]))  # mid-band, away from edges
    bands = spectrum_bands(np.sin(2 * np.pi * centre * t) * 150)  # quiet enough not to clip at 1.0
    assert len(bands) == BANDS
    assert int(np.argmax(bands)) == expected
    assert all(0 <= b <= 1 for b in bands)


def test_spectrum_of_silence_is_flat_zero():
    assert spectrum_bands(np.zeros(1024)) == [0.0] * BANDS
    assert spectrum_bands(np.array([])) == [0.0] * BANDS


def test_peak():
    assert peak(np.array([0, -16384, 100], dtype=np.int16)) == 0.5


def test_config_file(tmp_path):
    f = tmp_path / "config.toml"
    work = tmp_path / "work"
    work.mkdir()
    f.write_text(f'workdir = "{work}"\npermissions = "full"\nidle_voice_minutes = 3\n'
                 'echo_cancel = false\nvoice = "marin"\n')
    cfg = Config.load(f)
    assert cfg.workdir == work and cfg.permissions == "full" and cfg.problems == []
    assert cfg.idle_voice_minutes == 3 and cfg.echo_cancel is False and cfg.voice == "marin"


def test_bad_permissions_value_is_ignored(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text('permissions = "yolo"\n')
    assert Config.load(f).permissions == "auto"


def test_auto_profile_has_writable_roots():
    p = Config(extra_writable_roots=["/tmp"]).thread_permissions()
    assert p["config"]["sandbox_workspace_write"]["writable_roots"] == ["/tmp"]


def test_state_roundtrip(tmp_path):
    f = tmp_path / "s" / "state.json"
    State(model="m", effort="high", reply_card=False).save(f)
    s = State.load(f)
    assert (s.model, s.effort, s.reply_card) == ("m", "high", False)
    assert State.load(tmp_path / "missing.json").reply_card is True


def test_cli_mute_without_daemon_shows_osd(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr("codex_voice_agent.daemon.osd", lambda muted, message=None: calls.append(message))
    assert cli.main(["mute"]) == 2
    assert calls == ["Voice assistant not running"]


def test_mute_command_matcher():
    from codex_voice_agent.daemon import is_mute_command
    commands = ["Mute yourself.", "mute yourself please", "Okay, mute.", "Mute", "Stop listening",
                "Can you mute yourself?", "Go to sleep.", "please mute the assistant", "mute copilot", "mute codex",
                "mute the voice agent"]
    not_commands = ["Hey, are you able to mute yourself if I ask you to",
                    "Mute the Sonos in the kitchen", "unmute", "How do I mute you?",
                    "I can't stop listening to this album", "tell the kids to go to sleep",
                    "mute yourself and then the TV",
                    "What happens if I say mute yourself", "Turn the volume down",
                    "I muted the TV", ""]
    assert [c for c in commands if not is_mute_command(c)] == []
    assert [c for c in not_commands if is_mute_command(c)] == []


PW_DUMP = [
    {"type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
     "metadata": [{"key": "default.audio.sink", "value": {"name": "alsa_output.x"}},
                  {"key": "default.audio.source", "value": {"name": "alsa_input.builtin"}}]},
    {"type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Source",
     "node.name": "alsa_input.builtin", "node.description": "Built-in Audio"}}},
    {"type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Source",
     "node.name": "alsa_input.usb-Apple_Studio_Display", "node.description": "Studio Display Microphone"}}},
    {"type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Source",
     "node.name": "codex_voice_ec_source", "node.description": "Codex Voice Agent mic (echo cancelled)"}}},
    {"type": "PipeWire:Interface:Node", "info": {"props": {"media.class": "Audio/Sink",
     "node.name": "alsa_output.x", "node.description": "Speakers"}}},
]


def test_parse_inputs_lists_real_microphones_and_the_default():
    from codex_voice_agent.audio import parse_inputs
    out = parse_inputs(PW_DUMP)
    assert out["default"] == "alsa_input.builtin"
    assert [d["id"] for d in out["inputs"]] == ["alsa_input.builtin", "alsa_input.usb-Apple_Studio_Display"]
    assert out["inputs"][1]["name"] == "Studio Display Microphone"


def test_echo_canceller_stays_out_of_the_playback_path():
    from codex_voice_agent.audio import ec_module
    cmd = ec_module()
    assert "monitor.mode = true" in cmd and "playback.props" not in cmd
    assert "target.object" not in cmd
    assert 'target.object = "alsa_input.usb"' in ec_module("alsa_input.usb")



def test_broken_config_never_raises_and_says_why(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text('workdir = "~/nope-does-not-exist"\nidle_voice_minutes = "x"\necho_cancel = "yes"\n')
    cfg = Config.load(f)
    assert cfg.idle_voice_minutes == 15 and cfg.echo_cancel is True  # defaults kept
    assert len(cfg.problems) == 3
    f.write_text("this is = = not toml")
    cfg = Config.load(f)
    assert cfg.problems and "could not be read" in cfg.problems[0]


def test_odd_state_file_falls_back_to_defaults(tmp_path):
    f = tmp_path / "state.json"
    for content in ("null", "[]", '"x"', '{"model": 5, "reply_card": "no"}'):
        f.write_text(content)
        st = State.load(f)
        assert st.model is None and st.reply_card is True
