"""The plugin as installed: the files install.sh stages from the repository root."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Must match the rsync include rules in install.sh (stage_plugin).
PATTERNS = ["manifest.json", "*.qml", "*.js", "LICENSE", "README.md", "preview.*"]


def staged_files() -> list[Path]:
    files = set()
    for pattern in PATTERNS:
        files.update(p for p in ROOT.glob(pattern) if p.is_file())
    return sorted(files)


def test_install_sh_stages_the_same_patterns():
    text = (ROOT / "install.sh").read_text()
    for pattern in PATTERNS:
        assert f"--include='{pattern}'" in text, pattern


def test_manifest_id_matches_the_qml_constant():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    constant = re.search(r'var PLUGIN_ID = "([^"]+)"', (ROOT / "Model.js").read_text()).group(1)
    assert manifest["id"] == constant
    assert not manifest["id"].startswith("omarchy.")


def test_every_entry_point_and_local_import_is_staged():
    names = {p.name for p in staged_files()}
    manifest = json.loads((ROOT / "manifest.json").read_text())
    for entry in manifest["entryPoints"].values():
        assert entry in names, entry
    for qml in ROOT.glob("*.qml"):
        for imported in re.findall(r'^import "([^"]+)"', qml.read_text(), re.M):
            assert imported in names, f"{qml.name} imports {imported}"
        # sibling components used by name, e.g. ReplyCard { } or AgentIcon { }
        for comp in re.findall(r"^\s*([A-Z][A-Za-z]+)\s*\{", qml.read_text(), re.M):
            if (ROOT / f"{comp}.qml").exists():
                assert f"{comp}.qml" in names


@pytest.mark.skipif(shutil.which("omarchy") is None, reason="omarchy not installed")
def test_omarchy_validates_the_staged_plugin(tmp_path):
    for f in staged_files():
        shutil.copy2(f, tmp_path / f.name)
    result = subprocess.run(["omarchy", "plugin", "validate", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def qml_text_blocks(source: str) -> list[str]:
    """Every `Text { ... }` element in a QML file, braces matched."""
    blocks = []
    for m in re.finditer(r"\bText\s*\{", source):
        depth, i = 0, m.end() - 1
        while True:
            depth += {"{": 1, "}": -1}.get(source[i], 0)
            i += 1
            if depth == 0:
                break
        blocks.append(source[m.start():i])
    return blocks


def test_text_from_codex_never_renders_html():
    # A Text without textFormat is AutoText: an <img src> in Codex output would be fetched.
    for qml in ROOT.glob("*.qml"):
        for block in qml_text_blocks(qml.read_text()):
            if re.search(r"cardTextFull|modelData", block):
                assert "textFormat:" in block, f"{qml.name}: {block.splitlines()[1].strip()}"


def test_widget_reaches_the_backend_without_the_shell_service():
    # Omarchy 4.5+ gives widgets on a replacement bar (a cloned bar, e.g. OmacVM's notch bar)
    # a shell without serviceFor(); the widget must then link to the daemon itself.
    widget = (ROOT / "Widget.qml").read_text()
    assert re.search(r"svc:\s*shellSvc\s*\|\|\s*ownBackend\.item", widget)
    assert re.search(r"active:\s*!!root\.bar\s*&&\s*!root\.shellSvc", widget)
    assert "Backend {" in widget
    service = (ROOT / "Service.qml").read_text()
    assert re.search(r"^Backend\s*\{", service, re.M), "Service.qml must be a Backend"
    backend = (ROOT / "Backend.qml").read_text()
    assert "codex-voice-agent.sock" in backend
