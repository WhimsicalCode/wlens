"""`--dangerously-share` output + snippet generation (pyngrok mocked)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from wlens.mcp.share import (
    _claude_code_cli_command,
    _claude_code_mcp_json,
    _claude_desktop_full_config,
    _host_from_url,
    _mcpb_main_py,
    _mcpb_manifest,
    write_share_files,
)

URL = "https://abc123.ngrok-free.app/mcp"
TOKEN = "T-O-K-E-N"
WLENS_BIN = "/Users/me/.local/bin/wlens"


def test_claude_desktop_snippet_uses_wlens_python_proxy():
    parsed = json.loads(_claude_desktop_full_config(url=URL, token=TOKEN, wlens_binary=WLENS_BIN))
    entry = parsed["mcpServers"]["wlens"]
    # No Node, no npx, no mcp-remote — wlens itself is the stdio bridge.
    assert entry["command"] == WLENS_BIN
    assert entry["args"] == ["mcp-proxy", URL]
    # Token rides in env (not argv) so it doesn't show up in `ps`.
    assert entry["env"] == {"WLENS_AUTH_TOKEN": TOKEN}
    # Explicitly: no npx / node / mcp-remote anywhere.
    serialised = json.dumps(entry)
    assert "npx" not in serialised
    assert "mcp-remote" not in serialised


def test_claude_code_mcp_json_uses_http_type():
    parsed = json.loads(_claude_code_mcp_json(url=URL, token=TOKEN))
    entry = parsed["mcpServers"]["wlens"]
    # Claude Code speaks HTTP MCP natively — no proxy needed.
    assert entry["type"] == "http"
    assert entry["url"] == URL
    assert entry["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert "command" not in entry
    assert "args" not in entry


def test_claude_code_cli_command_shape():
    cmd = _claude_code_cli_command(url=URL, token=TOKEN)
    assert "claude mcp add" in cmd
    assert "--transport http" in cmd
    assert "--scope project" in cmd
    assert URL in cmd
    assert f'"Authorization: Bearer {TOKEN}"' in cmd


def test_host_from_url_strips_scheme_and_path():
    assert _host_from_url("https://abc.ngrok.io") == ["abc.ngrok.io", "abc.ngrok.io:*"]
    assert _host_from_url("https://abc.ngrok-free.app/mcp") == [
        "abc.ngrok-free.app",
        "abc.ngrok-free.app:*",
    ]


# ─── Drop-in file writing ───────────────────────────────────────────────────


def test_write_share_files_creates_json_configs(tmp_path: Path, monkeypatch):
    """JSON drop-ins are unconditional. The .mcpb is opportunistic."""
    # Stub out vendoring so this test doesn't hit the network / pip.
    import wlens.mcp.share as share_mod

    monkeypatch.setattr(share_mod, "_vendor_python_deps", lambda target_dir: target_dir.mkdir(exist_ok=True))

    share_dir = tmp_path / "wlens-share"
    written = write_share_files(share_dir, mcp_url=URL, token=TOKEN, wlens_binary=WLENS_BIN)

    assert written["claude-code"] == share_dir / ".mcp.json"
    assert written["claude-desktop"] == share_dir / "claude_desktop_config.json"
    assert written["claude-code"].exists()
    assert written["claude-desktop"].exists()


def test_written_claude_code_file_is_standalone_mcp_json(tmp_path: Path, monkeypatch):
    import wlens.mcp.share as share_mod

    monkeypatch.setattr(share_mod, "_vendor_python_deps", lambda target_dir: target_dir.mkdir(exist_ok=True))

    share_dir = tmp_path / "wlens-share"
    written = write_share_files(share_dir, mcp_url=URL, token=TOKEN, wlens_binary=WLENS_BIN)

    parsed = json.loads(written["claude-code"].read_text())
    entry = parsed["mcpServers"]["wlens"]
    assert entry["type"] == "http"
    assert entry["url"] == URL
    assert entry["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_written_claude_desktop_file_uses_python_proxy(tmp_path: Path, monkeypatch):
    import wlens.mcp.share as share_mod

    monkeypatch.setattr(share_mod, "_vendor_python_deps", lambda target_dir: target_dir.mkdir(exist_ok=True))

    share_dir = tmp_path / "wlens-share"
    written = write_share_files(share_dir, mcp_url=URL, token=TOKEN, wlens_binary=WLENS_BIN)

    parsed = json.loads(written["claude-desktop"].read_text())
    entry = parsed["mcpServers"]["wlens"]
    assert entry["command"] == WLENS_BIN
    assert entry["args"] == ["mcp-proxy", URL]
    assert entry["env"] == {"WLENS_AUTH_TOKEN": TOKEN}


def test_write_share_files_is_idempotent(tmp_path: Path, monkeypatch):
    import wlens.mcp.share as share_mod

    monkeypatch.setattr(share_mod, "_vendor_python_deps", lambda target_dir: target_dir.mkdir(exist_ok=True))

    share_dir = tmp_path / "wlens-share"
    write_share_files(share_dir, mcp_url=URL, token="first-token", wlens_binary=WLENS_BIN)
    write_share_files(share_dir, mcp_url=URL, token="second-token", wlens_binary=WLENS_BIN)

    parsed = json.loads((share_dir / ".mcp.json").read_text())
    assert parsed["mcpServers"]["wlens"]["headers"]["Authorization"] == "Bearer second-token"
    desktop = json.loads((share_dir / "claude_desktop_config.json").read_text())
    assert desktop["mcpServers"]["wlens"]["env"]["WLENS_AUTH_TOKEN"] == "second-token"


def test_share_falls_back_to_wlens_string_when_binary_missing(tmp_path: Path, monkeypatch):
    """If shutil.which can't find wlens, we write the literal string "wlens"."""
    import wlens.mcp.share as share_mod

    monkeypatch.setattr(share_mod.shutil, "which", lambda _cmd: None)
    # Also stub out the vendoring so the fallback test doesn't actually shell out
    # to pip (we only care about the claude-desktop JSON here).
    monkeypatch.setattr(share_mod, "_vendor_python_deps", lambda target_dir: target_dir.mkdir(exist_ok=True))

    share_dir = tmp_path / "wlens-share"
    written = write_share_files(share_dir, mcp_url=URL, token=TOKEN)
    parsed = json.loads(written["claude-desktop"].read_text())
    assert parsed["mcpServers"]["wlens"]["command"] == "wlens"


# ─── .mcpb bundle (pure-Python) ─────────────────────────────────────────────


def test_mcpb_manifest_uses_python_runtime():
    manifest = _mcpb_manifest(mcp_url=URL, token=TOKEN)
    assert manifest["manifest_version"] == "0.3"
    assert manifest["name"] == "wlens"
    server = manifest["server"]
    # Python-native bundle: no Node anywhere in the manifest.
    assert server["type"] == "python"
    assert server["entry_point"] == "server/main.py"
    assert server["mcp_config"]["command"] == "python"
    assert server["mcp_config"]["args"] == ["${__dirname}/server/main.py", URL]
    # Token rides in env so it doesn't end up in `ps`.
    assert server["mcp_config"]["env"] == {"WLENS_AUTH_TOKEN": TOKEN}
    assert manifest["compatibility"]["runtimes"]["python"].startswith(">=3")


def test_mcpb_main_py_is_runnable_stdlib_plus_vendored():
    src = _mcpb_main_py()
    # Must import the vendored deps via sys.path shim so it can run inside
    # Claude Desktop's bundled Python without wlens being installed.
    assert 'sys.path.insert(0, str(_HERE / "lib"))' in src
    assert "from mcp.client.streamable_http import streamablehttp_client" in src
    assert "from mcp.server.stdio import stdio_server" in src
    # Doesn't import from wlens — the .mcpb is standalone.
    assert "from wlens" not in src
    assert "import wlens" not in src
    # Token from env (matches manifest), URL from argv.
    assert 'os.environ.get("WLENS_AUTH_TOKEN"' in src
    compile(src, "<main.py>", "exec")  # syntax check


def test_mcpb_bundle_structure_when_vendoring_succeeds(tmp_path: Path, monkeypatch):
    """With vendoring mocked, the zip should contain the expected tree."""
    import wlens.mcp.share as share_mod

    def fake_vendor(target_dir: Path) -> None:
        # Simulate `uv pip install --target` by planting a few dep trees.
        (target_dir / "mcp" / "client").mkdir(parents=True)
        (target_dir / "mcp" / "__init__.py").write_text("# mcp\n")
        (target_dir / "mcp" / "client" / "streamable_http.py").write_text("# client\n")
        (target_dir / "httpx").mkdir()
        (target_dir / "httpx" / "__init__.py").write_text("# httpx\n")
        (target_dir / "anyio").mkdir()
        (target_dir / "anyio" / "__init__.py").write_text("# anyio\n")

    monkeypatch.setattr(share_mod, "_vendor_python_deps", fake_vendor)

    share_dir = tmp_path / "wlens-share"
    written = write_share_files(share_dir, mcp_url=URL, token=TOKEN, wlens_binary=WLENS_BIN)

    assert "mcpb" in written
    bundle = written["mcpb"]
    assert bundle.name == "wlens.mcpb"
    assert zipfile.is_zipfile(bundle)

    with zipfile.ZipFile(bundle) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "server/main.py" in names
        assert "server/lib/mcp/__init__.py" in names
        assert "server/lib/httpx/__init__.py" in names
        assert "server/lib/anyio/__init__.py" in names

        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["server"]["type"] == "python"
        assert manifest["server"]["mcp_config"]["env"]["WLENS_AUTH_TOKEN"] == TOKEN
        assert URL in manifest["server"]["mcp_config"]["args"]

        main_py = zf.read("server/main.py").decode()
        assert "streamablehttp_client" in main_py


def test_mcpb_bundle_skipped_when_bundler_missing(tmp_path: Path, monkeypatch):
    """Graceful fallback: no uv / no pip → skip .mcpb, still emit JSON files."""
    import wlens.mcp.share as share_mod

    def raise_unavailable(_target_dir: Path) -> None:
        raise share_mod.PythonBundlerUnavailableError("no uv and no pip")

    monkeypatch.setattr(share_mod, "_vendor_python_deps", raise_unavailable)

    share_dir = tmp_path / "wlens-share"
    written = write_share_files(share_dir, mcp_url=URL, token=TOKEN, wlens_binary=WLENS_BIN)

    assert "mcpb" not in written
    assert not (share_dir / "wlens.mcpb").exists()
    # The two JSON drop-ins are still there and usable.
    assert written["claude-code"].exists()
    assert written["claude-desktop"].exists()


def test_mcpb_bundle_regenerates_on_second_write(tmp_path: Path, monkeypatch):
    import wlens.mcp.share as share_mod

    def fake_vendor(target_dir: Path) -> None:
        (target_dir / "stub.py").write_text("# stub\n")

    monkeypatch.setattr(share_mod, "_vendor_python_deps", fake_vendor)

    share_dir = tmp_path / "wlens-share"
    write_share_files(share_dir, mcp_url=URL, token="first", wlens_binary=WLENS_BIN)
    write_share_files(share_dir, mcp_url=URL, token="second", wlens_binary=WLENS_BIN)

    bundle = share_dir / "wlens.mcpb"
    with zipfile.ZipFile(bundle) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["server"]["mcp_config"]["env"]["WLENS_AUTH_TOKEN"] == "second"
