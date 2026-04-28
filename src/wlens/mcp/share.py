"""`wlens mcp --dangerously-share` — local server + ngrok tunnel.

Starts the wlens MCP server on localhost, opens a pyngrok tunnel, auto-
generates a bearer token, and writes ready-to-use drop-in files for
the major MCP clients:

  - `.mcp.json`                       native HTTP (Claude Code; also reusable
                                      for Cursor at .cursor/mcp.json, GitHub
                                      Copilot at .vscode/mcp.json, etc.)
  - `claude_desktop_config.json`      proxied through `wlens mcp-proxy`
  - `wlens.mcpb`                      standalone Python bundle (double-click)
  - `gemini_settings.json`            Gemini CLI / Antigravity (uses `httpUrl`)
  - `codex_config.toml`               Codex CLI (TOML, [mcp_servers.wlens])

Everything stays pure Python: the .mcpb vendors `mcp` + `httpx` + `anyio`
into the bundle with `uv pip install --target`, so the recipient doesn't
need wlens installed — just Claude Desktop's bundled Python runtime.

This is a demo mode. The `--dangerously-` prefix is deliberate: a public
URL fronting a warehouse is not a production posture.
"""

from __future__ import annotations

import atexit
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import Config
from . import app as app_module
from . import logs

NGROK_AUTHTOKEN_ENV = "NGROK_AUTHTOKEN"
SHARE_DIR_NAME = "wlens/share"
MCPB_MANIFEST_VERSION = "0.3"

# Third-party deps the bundled proxy.py needs at runtime. Transitive deps
# (starlette, sniffio, etc.) come along automatically via pip/uv's resolver.
MCPB_BUNDLED_PACKAGES = ("mcp>=1.10", "httpx>=0.27", "anyio>=4.0")


@dataclass
class ShareHandles:
    public_url: str
    token: str
    process: Any


def run(
    config: Config,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    allowed_hosts: list[str] | None = None,
) -> int:
    """Open a tunnel, start the server, write config files, print banner. Blocks."""
    token = secrets.token_urlsafe(32)

    try:
        public_url = _open_tunnel(port)
    except Exception as e:  # noqa: BLE001
        print(f"error: failed to open ngrok tunnel: {e}", file=sys.stderr)
        print(
            "hint: install the ngrok binary via `brew install ngrok` (or set "
            f"{NGROK_AUTHTOKEN_ENV} with your authtoken from https://dashboard.ngrok.com).",
            file=sys.stderr,
        )
        return 1

    share_dir = config.repo_root / SHARE_DIR_NAME
    written = write_share_files(
        share_dir,
        mcp_url=f"{public_url}/mcp",
        token=token,
    )

    atexit.register(_close_tunnel)
    atexit.register(_cleanup_share_dir, share_dir)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _shutdown_handler_factory(share_dir))
        except (ValueError, OSError):
            pass  # non-main thread, pytest harness, etc.

    allowed = allowed_hosts if allowed_hosts is not None else _host_from_url(public_url)
    _print_share_banner(
        public_url=public_url,
        token=token,
        share_dir=share_dir,
        written_files=written,
    )

    return app_module.run(
        config,
        host=host,
        port=port,
        dangerously_share=True,
        no_auth=False,
        token_override=token,
        allowed_hosts=allowed,
    )


# ─── Config file writing ────────────────────────────────────────────────────


def write_share_files(
    share_dir: Path,
    *,
    mcp_url: str,
    token: str,
    wlens_binary: str | None = None,
) -> dict[str, Path]:
    """Write the per-client drop-in files into `share_dir`.

    Returns a dict mapping short client names → absolute file paths.

    Five artifacts (one per format family — see docs/mcp.md for the per-client
    paste-and-place table):
    - `.mcp.json`                  Claude Code shape (HTTP, `url` + `headers`).
                                   Universal donor: drops into Cursor, GitHub
                                   Copilot in VS Code, Cline, Zed unchanged.
    - `claude_desktop_config.json` Claude Desktop (stdio via `wlens mcp-proxy`).
    - `wlens.mcpb`                 Claude Desktop standalone bundle (double-click).
    - `gemini_settings.json`       Gemini CLI / Antigravity (HTTP, `httpUrl`).
    - `codex_config.toml`          Codex CLI (TOML, `[mcp_servers.wlens]`).

    The `.mcpb` is pure-Python: we vendor `mcp` + `httpx` + `anyio` into
    `server/lib/` via uv/pip so the recipient needs nothing beyond Claude
    Desktop's bundled Python. If neither `uv` nor `pip` is available at
    build time, the bundle is skipped (the JSON/TOML files are still emitted).
    """
    share_dir.mkdir(parents=True, exist_ok=True)
    resolved_binary = wlens_binary or _resolve_wlens_binary()

    code_path = share_dir / ".mcp.json"
    code_path.write_text(_claude_code_mcp_json(url=mcp_url, token=token) + "\n")

    desktop_path = share_dir / "claude_desktop_config.json"
    desktop_path.write_text(
        _claude_desktop_full_config(url=mcp_url, token=token, wlens_binary=resolved_binary) + "\n"
    )

    gemini_path = share_dir / "gemini_settings.json"
    gemini_path.write_text(_gemini_mcp_json(url=mcp_url, token=token) + "\n")

    codex_path = share_dir / "codex_config.toml"
    codex_path.write_text(_codex_mcp_toml(url=mcp_url, token=token))

    out: dict[str, Path] = {
        "claude-code": code_path,
        "claude-desktop": desktop_path,
        "gemini": gemini_path,
        "codex": codex_path,
    }

    bundle_path = share_dir / "wlens.mcpb"
    try:
        _write_mcpb_bundle(bundle_path, mcp_url=mcp_url, token=token)
        out["mcpb"] = bundle_path
    except PythonBundlerUnavailableError as e:
        logs.event("mcpb_skipped", reason=str(e))
        if bundle_path.exists():
            bundle_path.unlink()

    return out


def _resolve_wlens_binary() -> str:
    """Return an absolute path to the wlens CLI for Claude Desktop to exec.

    Claude Desktop's spawn env may not include `~/.local/bin` or Homebrew
    paths, so we bake in the full path we found at share time. Falls back
    to the string "wlens" if nothing resolves (rare — the caller just
    installed wlens, so it's almost always on PATH).
    """
    return shutil.which("wlens") or "wlens"


# ─── .mcpb bundle (pure-Python) ────────────────────────────────────────────


class PythonBundlerUnavailableError(RuntimeError):
    """Neither `uv` nor `pip` was usable for vendoring Python deps."""


def _write_mcpb_bundle(path: Path, *, mcp_url: str, token: str) -> None:
    """Create a pure-Python `.mcpb` extension bundle at `path`.

    Contents of the zip:
      manifest.json                    (MCPB spec v0.3, server.type: python)
      server/main.py                   (stdio↔HTTP proxy with sys.path shim)
      server/lib/                      (vendored `mcp`, `httpx`, `anyio`, deps)

    The manifest's `mcp_config.command` is `python` — Claude Desktop's
    bundled Python runtime runs `server/main.py`. The URL travels as a CLI
    arg, the bearer token as an env var so it doesn't show up in `ps`.
    """
    import tempfile

    with tempfile.TemporaryDirectory(prefix="wlens-mcpb-") as td:
        server_dir = Path(td) / "server"
        server_dir.mkdir()
        lib_dir = server_dir / "lib"
        lib_dir.mkdir()

        _vendor_python_deps(lib_dir)
        (server_dir / "main.py").write_text(_mcpb_main_py(), encoding="utf-8")

        manifest = _mcpb_manifest(mcp_url=mcp_url, token=token)

        if path.exists():
            path.unlink()
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
            for file_path in sorted(server_dir.rglob("*")):
                if file_path.is_file() and not file_path.is_symlink():
                    arcname = f"server/{file_path.relative_to(server_dir).as_posix()}"
                    zf.write(file_path, arcname)


def _vendor_python_deps(target_dir: Path) -> None:
    """Populate `target_dir` with vendored copies of the proxy's deps.

    Tries `uv pip install --target` first (fast, resolves like wlens's own
    lockfile), falls back to `pip install --target`. Raises
    PythonBundlerUnavailableError if neither is usable.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    uv_bin = shutil.which("uv")
    if uv_bin:
        cmd = [uv_bin, "pip", "install", "--target", str(target_dir), "--quiet", *MCPB_BUNDLED_PACKAGES]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return
        logs.event("mcpb_uv_failed", stderr=result.stderr.strip())

    pip_cmd = [sys.executable, "-m", "pip", "install", "--target", str(target_dir), "--quiet", *MCPB_BUNDLED_PACKAGES]
    try:
        result = subprocess.run(pip_cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise PythonBundlerUnavailableError(
            "Neither `uv` nor `pip` was usable to vendor Python deps."
        ) from e
    if result.returncode != 0:
        raise PythonBundlerUnavailableError(
            f"Vendoring Python deps failed. stderr: {result.stderr.strip()!r}"
        )


def _mcpb_main_py() -> str:
    """Return the contents of `server/main.py` for the bundle.

    The script is pure stdlib + (vendored) `mcp` + `anyio` + `httpx`. It
    prepends its own `lib/` to sys.path so vendored imports resolve before
    any system-wide packages.
    """
    return '''\
#!/usr/bin/env python3
"""wlens stdio↔HTTP MCP proxy — bundled.

Auto-generated by `wlens mcp --dangerously-share`. Reads JSON-RPC on
stdin, forwards to the remote wlens HTTP MCP server with a Bearer token
from the WLENS_AUTH_TOKEN env var, streams responses to stdout.

Vendored dependencies live in ./lib/ and are prepended to sys.path so
this script runs inside Claude Desktop's bundled Python without needing
any system-wide packages.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make vendored deps importable before anything else.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / "lib"))

import anyio
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.stdio import stdio_server


async def _forward(src, dst) -> None:
    async for msg in src:
        if isinstance(msg, Exception):
            raise msg
        await dst.send(msg)


async def _run(url: str) -> int:
    token = os.environ.get("WLENS_AUTH_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else None

    async with streamablehttp_client(url, headers=headers) as (remote_read, remote_write, _sid):
        async with stdio_server() as (stdio_read, stdio_write):
            async with anyio.create_task_group() as tg:
                tg.start_soon(_forward, stdio_read, remote_write)
                tg.start_soon(_forward, remote_read, stdio_write)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("Usage: main.py <remote_url>", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_run(argv[0]))
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"wlens mcpb proxy: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
'''


def _mcpb_manifest(*, mcp_url: str, token: str) -> dict:
    return {
        "manifest_version": MCPB_MANIFEST_VERSION,
        "name": "wlens",
        "display_name": "wlens (warehouse lens)",
        "version": __version__,
        "description": "Read-only SQL + dbt schema docs over MCP.",
        "long_description": (
            "Exposes a remote wlens MCP server: execute_sql tool (read-only), "
            "dbt doc resources (wlens://models/...), and the wlens skill prompt. "
            "This bundle was generated by `wlens mcp --dangerously-share` and "
            "points at a temporary ngrok URL — regenerate when the tunnel dies."
        ),
        "author": {
            "name": "wlens",
            "url": "https://github.com/whimsical/wlens",
        },
        "server": {
            "type": "python",
            "entry_point": "server/main.py",
            "mcp_config": {
                "command": "python",
                "args": ["${__dirname}/server/main.py", mcp_url],
                "env": {"WLENS_AUTH_TOKEN": token},
            },
        },
        "compatibility": {
            "runtimes": {"python": ">=3.10"},
        },
    }


# ─── ngrok wrapper ──────────────────────────────────────────────────────────


def _open_tunnel(port: int) -> str:
    from pyngrok import conf, ngrok

    authtoken = os.environ.get(NGROK_AUTHTOKEN_ENV)
    if authtoken:
        conf.get_default().auth_token = authtoken

    # Force HTTPS-only. ngrok v3 returns HTTPS by default but being explicit
    # protects us from future API changes and makes sure `public_url` can
    # never come back as plain http://.
    tunnel = ngrok.connect(port, "http", schemes=["https"])
    url = tunnel.public_url
    if not url.startswith("https://"):
        raise RuntimeError(
            f"ngrok returned a non-https URL: {url!r}. Refusing to share a bearer "
            "token over plaintext."
        )
    logs.event("share_tunnel_opened", url=url, port=port)
    return url


def _close_tunnel() -> None:
    try:
        from pyngrok import ngrok

        ngrok.disconnect_all()
        ngrok.kill()
        logs.event("share_tunnel_closed")
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass


def _cleanup_share_dir(share_dir: Path) -> None:
    try:
        if share_dir.exists():
            shutil.rmtree(share_dir)
            logs.event("share_dir_removed", path=str(share_dir))
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass


def _shutdown_handler_factory(share_dir: Path):
    def _handler(_signo: int, _frame: Any) -> None:
        _close_tunnel()
        _cleanup_share_dir(share_dir)
        sys.exit(0)

    return _handler


def _host_from_url(url: str) -> list[str]:
    # https://abc.ngrok-free.app → ["abc.ngrok-free.app"]
    host = url.split("://", 1)[1].split("/", 1)[0]
    return [host, f"{host}:*"]


# ─── User-facing output ─────────────────────────────────────────────────────


def _print_share_banner(
    *,
    public_url: str,
    token: str,
    share_dir: Path,
    written_files: dict[str, Path],
) -> None:
    mcp_url = f"{public_url}/mcp"
    code_path = written_files["claude-code"]
    desktop_path = written_files["claude-desktop"]
    gemini_path = written_files["gemini"]
    codex_path = written_files["codex"]
    mcpb_path = written_files.get("mcpb")

    banner = [
        "",
        "⚠️  DANGER — wlens is temporarily public.",
        "",
        f"  Public URL:     {mcp_url}",
        f"  Bearer token:   {token}",
        "",
        "  This URL is reachable by anyone until you Ctrl-C this process.",
        "  Do NOT post it in public channels. Rotate the token if it leaks.",
        "",
        f"Drop-in files written to {share_dir}/:",
        "",
        "─── Claude Desktop (recommended) ─────────────────────────────────",
        "",
    ]
    if mcpb_path is not None:
        banner += [
            f"  {mcpb_path}",
            "",
            "  Double-click this .mcpb file. Claude Desktop installs it as an",
            "  extension — all Python, all self-contained (mcp + httpx + anyio",
            "  are vendored inside). Recipient doesn't need wlens installed.",
            "",
            "  Fallback (edit config by hand, uses your installed `wlens`):",
            "",
            f"  {desktop_path}",
        ]
    else:
        banner += [
            "  .mcpb bundle skipped — couldn't find `uv` or `pip` to vendor",
            "  Python deps at build time. The JSON config below still works",
            "  (it uses the `wlens` binary on your PATH as the stdio bridge).",
            "",
            f"  {desktop_path}",
            "",
            "  Drop into ~/Library/Application Support/Claude/ (if empty) or",
            "  merge into Settings → Developer → Edit Config.",
        ]
    banner += [
        "",
        "─── Claude Code ──────────────────────────────────────────────────",
        "",
        f"  {code_path}",
        "",
        "  Move this file into the root of any project; Claude Code picks it",
        "  up on next launch (native HTTP — no proxy needed). Or run:",
        "",
        _claude_code_cli_command(url=mcp_url, token=token),
        "",
        "─── Gemini CLI / Antigravity ─────────────────────────────────────",
        "",
        f"  {gemini_path}",
        "",
        "  Gemini CLI: run",
        "",
        _gemini_cli_command(url=mcp_url, token=token),
        "",
        "  …or merge the JSON snippet into ~/.gemini/settings.json.",
        "  Antigravity: Settings → MCP Servers → Manage → View raw config →",
        "  paste the snippet from the file above.",
        "",
        "─── Codex CLI ────────────────────────────────────────────────────",
        "",
        f"  {codex_path}",
        "",
        "  Merge the [mcp_servers.wlens] block into ~/.codex/config.toml.",
        "",
        "─── Other clients (Cursor, VS Code Copilot, Windsurf, ChatGPT, …) ─",
        "",
        "  See docs/mcp.md for paste-ready snippets per client. Most accept",
        "  the Claude Code .mcp.json shape unchanged at a different path.",
        "",
        "Press Ctrl-C to stop the tunnel, delete the drop-in files, and shut down.",
        "",
    ]
    print("\n".join(banner), flush=True)


def _claude_code_cli_command(*, url: str, token: str) -> str:
    return (
        "  claude mcp add --transport http --scope project wlens \\\n"
        f"    {url} \\\n"
        f'    --header "Authorization: Bearer {token}"'
    )


def _gemini_cli_command(*, url: str, token: str) -> str:
    return (
        "  gemini mcp add --transport http wlens \\\n"
        f"    {url} \\\n"
        f'    --header "Authorization: Bearer {token}"'
    )


def _claude_code_mcp_json(*, url: str, token: str) -> str:
    config = {
        "mcpServers": {
            "wlens": {
                "type": "http",
                "url": url,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        }
    }
    return json.dumps(config, indent=2)


def _gemini_mcp_json(*, url: str, token: str) -> str:
    """Gemini CLI / Antigravity drop-in for ~/.gemini/settings.json.

    Differs from the Claude Code shape by one field name: `httpUrl` instead
    of `url` (and no `type` discriminator). Same `mcpServers` envelope, same
    `headers` map.
    """
    config = {
        "mcpServers": {
            "wlens": {
                "httpUrl": url,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        }
    }
    return json.dumps(config, indent=2)


def _codex_mcp_toml(*, url: str, token: str) -> str:
    """Codex CLI drop-in for ~/.codex/config.toml (or .codex/config.toml).

    Codex uses TOML, not JSON. The HTTP transport schema is `url` plus an
    `http_headers` inline map (per OpenAI's Codex config reference). We hand-
    format rather than pull in a `tomli_w` dep — the structure is fixed.
    """
    return (
        "[mcp_servers.wlens]\n"
        f'url = "{url}"\n'
        f'http_headers = {{ Authorization = "Bearer {token}" }}\n'
    )


def _claude_desktop_full_config(*, url: str, token: str, wlens_binary: str) -> str:
    """Full claude_desktop_config.json.

    Points at the user's already-installed `wlens` binary running
    `mcp-proxy`. No Node, no npx, no bundled JS — pure Python bridge
    between Claude Desktop's stdio transport and the remote HTTP wlens
    server. Token travels via env, not argv, so it's not visible in
    Claude Desktop's process listing.
    """
    config = {
        "mcpServers": {
            "wlens": {
                "command": wlens_binary,
                "args": ["mcp-proxy", url],
                "env": {"WLENS_AUTH_TOKEN": token},
            }
        }
    }
    return json.dumps(config, indent=2)


def _claude_desktop_snippet(*, url: str, token: str, wlens_binary: str = "wlens") -> str:
    """Back-compat alias — same JSON shape as _claude_desktop_full_config."""
    return _claude_desktop_full_config(url=url, token=token, wlens_binary=wlens_binary)
