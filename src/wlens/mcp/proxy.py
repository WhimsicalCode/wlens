"""Stdio ↔ HTTP MCP proxy.

Lets any MCP client that speaks stdio (Claude Desktop, Cursor-via-stdio,
older integrations) talk to a remote wlens HTTP MCP server. Reads JSON-RPC
on stdin, forwards to the remote over streamable-http with a Bearer token,
streams responses back to stdout.

This is the pure-Python alternative to Claude Desktop's usual `mcp-remote`
NPM shim. No Node, no npm, no bundled dependencies — just the `mcp` Python
SDK which wlens already depends on.

Usage (normally invoked by Claude Desktop via the drop-in config):

    WLENS_AUTH_TOKEN=<token> wlens mcp-proxy <remote_url>
"""

from __future__ import annotations

import asyncio
import os
import sys

import anyio

from . import logs

AUTH_ENV_VAR = "WLENS_AUTH_TOKEN"


async def _run(url: str) -> int:
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.server.stdio import stdio_server

    token = os.environ.get(AUTH_ENV_VAR, "")
    headers = {"Authorization": f"Bearer {token}"} if token else None

    logs.event("proxy_connecting", url=url, auth="token" if token else "none")

    async with streamablehttp_client(url, headers=headers) as (remote_read, remote_write, _sid):
        async with stdio_server() as (stdio_read, stdio_write):
            async with anyio.create_task_group() as tg:
                tg.start_soon(_forward, stdio_read, remote_write)
                tg.start_soon(_forward, remote_read, stdio_write)
    return 0


async def _forward(src, dst) -> None:
    """Pipe session messages from one memory stream to another.

    Any `Exception` object arriving on `src` (streamablehttp_client yields
    them on transport errors) is logged + re-raised so the task group tears
    the whole proxy down cleanly.
    """
    async for msg in src:
        if isinstance(msg, Exception):
            logs.event("proxy_transport_error", error=repr(msg))
            raise msg
        await dst.send(msg)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(
            "Usage: wlens mcp-proxy <remote_url>\n"
            "       (WLENS_AUTH_TOKEN env var carries the bearer token).",
            file=sys.stderr,
        )
        return 2
    url = argv[0]
    try:
        return asyncio.run(_run(url))
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001 — fail loud with a readable message
        logs.event("proxy_fatal", error=repr(e))
        print(f"wlens mcp-proxy: {e}", file=sys.stderr)
        return 1
