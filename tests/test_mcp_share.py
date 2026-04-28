"""`--dangerously-share` output + snippet generation (pyngrok mocked)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from wlens.mcp.share import (
    _claude_code_cli_command,
    _claude_code_mcp_json,
    _claude_desktop_full_config,
    _codex_mcp_toml,
    _gemini_cli_command,
    _gemini_mcp_json,
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


def test_gemini_mcp_json_uses_httpUrl():
    """Gemini CLI / Antigravity differs from Claude Code by one field: `httpUrl`
    instead of `url` + `type: "http"`. Same `mcpServers` envelope otherwise."""
    parsed = json.loads(_gemini_mcp_json(url=URL, token=TOKEN))
    entry = parsed["mcpServers"]["wlens"]
    assert entry["httpUrl"] == URL
    assert entry["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    # Gemini CLI doesn't use `type` or `url` for HTTP MCP servers.
    assert "type" not in entry
    assert "url" not in entry
    assert "command" not in entry


def test_gemini_cli_command_shape():
    cmd = _gemini_cli_command(url=URL, token=TOKEN)
    assert "gemini mcp add" in cmd
    assert "--transport http" in cmd
    assert URL in cmd
    assert f'"Authorization: Bearer {TOKEN}"' in cmd


def test_codex_mcp_toml_shape():
    """Codex CLI uses TOML with `[mcp_servers.<name>]`, `url`, and an inline
    `http_headers` map (per OpenAI's Codex config reference)."""
    text = _codex_mcp_toml(url=URL, token=TOKEN)
    assert "[mcp_servers.wlens]" in text
    assert f'url = "{URL}"' in text
    assert f'Authorization = "Bearer {TOKEN}"' in text
    assert "http_headers" in text
    # No JSON artefacts (curly braces around the table, etc.) leaking in.
    assert not text.startswith("{")


def test_host_from_url_strips_scheme_and_path():
    assert _host_from_url("https://abc.ngrok.io") == ["abc.ngrok.io", "abc.ngrok.io:*"]
    assert _host_from_url("https://abc.ngrok-free.app/mcp") == [
        "abc.ngrok-free.app",
        "abc.ngrok-free.app:*",
    ]


# ─── Drop-in file writing ───────────────────────────────────────────────────


def test_write_share_files_creates_json_configs(tmp_path: Path, monkeypatch):
    """JSON / TOML drop-ins are unconditional. The .mcpb is opportunistic."""
    # Stub out vendoring so this test doesn't hit the network / pip.
    import wlens.mcp.share as share_mod

    monkeypatch.setattr(share_mod, "_vendor_python_deps", lambda target_dir: target_dir.mkdir(exist_ok=True))

    share_dir = tmp_path / "wlens-share"
    written = write_share_files(share_dir, mcp_url=URL, token=TOKEN, wlens_binary=WLENS_BIN)

    assert written["claude-code"] == share_dir / ".mcp.json"
    assert written["claude-desktop"] == share_dir / "claude_desktop_config.json"
    assert written["gemini"] == share_dir / "gemini_settings.json"
    assert written["codex"] == share_dir / "codex_config.toml"
    assert written["claude-code"].exists()
    assert written["claude-desktop"].exists()
    assert written["gemini"].exists()
    assert written["codex"].exists()


def test_written_gemini_file_uses_httpUrl(tmp_path: Path, monkeypatch):
    import wlens.mcp.share as share_mod

    monkeypatch.setattr(share_mod, "_vendor_python_deps", lambda target_dir: target_dir.mkdir(exist_ok=True))

    share_dir = tmp_path / "wlens-share"
    written = write_share_files(share_dir, mcp_url=URL, token=TOKEN, wlens_binary=WLENS_BIN)

    parsed = json.loads(written["gemini"].read_text())
    entry = parsed["mcpServers"]["wlens"]
    assert entry["httpUrl"] == URL
    assert entry["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_written_codex_file_is_valid_toml(tmp_path: Path, monkeypatch):
    """Round-trip the Codex TOML through tomllib so we know it parses."""
    import tomllib

    import wlens.mcp.share as share_mod

    monkeypatch.setattr(share_mod, "_vendor_python_deps", lambda target_dir: target_dir.mkdir(exist_ok=True))

    share_dir = tmp_path / "wlens-share"
    written = write_share_files(share_dir, mcp_url=URL, token=TOKEN, wlens_binary=WLENS_BIN)

    parsed = tomllib.loads(written["codex"].read_text())
    server = parsed["mcp_servers"]["wlens"]
    assert server["url"] == URL
    assert server["http_headers"]["Authorization"] == f"Bearer {TOKEN}"


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
    import tomllib

    import wlens.mcp.share as share_mod

    monkeypatch.setattr(share_mod, "_vendor_python_deps", lambda target_dir: target_dir.mkdir(exist_ok=True))

    share_dir = tmp_path / "wlens-share"
    write_share_files(share_dir, mcp_url=URL, token="first-token", wlens_binary=WLENS_BIN)
    write_share_files(share_dir, mcp_url=URL, token="second-token", wlens_binary=WLENS_BIN)

    parsed = json.loads((share_dir / ".mcp.json").read_text())
    assert parsed["mcpServers"]["wlens"]["headers"]["Authorization"] == "Bearer second-token"
    desktop = json.loads((share_dir / "claude_desktop_config.json").read_text())
    assert desktop["mcpServers"]["wlens"]["env"]["WLENS_AUTH_TOKEN"] == "second-token"
    gemini = json.loads((share_dir / "gemini_settings.json").read_text())
    assert gemini["mcpServers"]["wlens"]["headers"]["Authorization"] == "Bearer second-token"
    codex = tomllib.loads((share_dir / "codex_config.toml").read_text())
    assert codex["mcp_servers"]["wlens"]["http_headers"]["Authorization"] == "Bearer second-token"


def test_cli_mcp_clients_uses_env_token(tmp_path: Path, monkeypatch):
    """`wlens mcp-clients --url ...` defaults the token to WLENS_AUTH_TOKEN."""
    import wlens.mcp.share as share_mod

    monkeypatch.setattr(share_mod, "_vendor_python_deps", lambda target_dir: target_dir.mkdir(exist_ok=True))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WLENS_AUTH_TOKEN", "env-token-xyz")

    from wlens.cli import main
    rc = main(["mcp-clients", "--url", "https://wlens.team.com/mcp"])
    assert rc == 0

    out_dir = tmp_path / "wlens" / "share"
    parsed = json.loads((out_dir / ".mcp.json").read_text())
    assert parsed["mcpServers"]["wlens"]["headers"]["Authorization"] == "Bearer env-token-xyz"
    assert parsed["mcpServers"]["wlens"]["url"] == "https://wlens.team.com/mcp"


def test_cli_mcp_clients_explicit_token_overrides_env(tmp_path: Path, monkeypatch):
    import wlens.mcp.share as share_mod

    monkeypatch.setattr(share_mod, "_vendor_python_deps", lambda target_dir: target_dir.mkdir(exist_ok=True))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WLENS_AUTH_TOKEN", "env-token")

    from wlens.cli import main
    rc = main(["mcp-clients", "--url", "https://wlens.team.com/mcp", "--token", "explicit-token"])
    assert rc == 0

    parsed = json.loads((tmp_path / "wlens" / "share" / ".mcp.json").read_text())
    assert parsed["mcpServers"]["wlens"]["headers"]["Authorization"] == "Bearer explicit-token"


def test_cli_mcp_clients_fails_without_token(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WLENS_AUTH_TOKEN", raising=False)

    from wlens.cli import main
    rc = main(["mcp-clients", "--url", "https://wlens.team.com/mcp"])
    assert rc == 1
    assert not (tmp_path / "wlens" / "share").exists()


def test_cli_mcp_clients_custom_out_dir(tmp_path: Path, monkeypatch):
    import wlens.mcp.share as share_mod

    monkeypatch.setattr(share_mod, "_vendor_python_deps", lambda target_dir: target_dir.mkdir(exist_ok=True))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WLENS_AUTH_TOKEN", "tok")

    from wlens.cli import main
    custom = tmp_path / "elsewhere"
    rc = main(["mcp-clients", "--url", "https://wlens.team.com/mcp", "--out", str(custom)])
    assert rc == 0
    assert (custom / ".mcp.json").exists()
    assert not (tmp_path / "wlens" / "share").exists()


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
