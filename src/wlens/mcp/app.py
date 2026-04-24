"""Build the ASGI app and uvicorn runner for `wlens mcp`.

The parent Starlette app is FastMCP's `streamable_http_app()` wrapped in our
bearer-auth middleware. `/health` and `/refresh` are registered on the same
app via FastMCP's `custom_route` decorator so everything lives on one port.
"""

from __future__ import annotations

import asyncio
import signal
import time
from typing import Any

import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import Config
from . import logs
from .auth import (
    AUTH_ENV_VAR,
    BearerAuthMiddleware,
    DEFAULT_OPEN_PATHS,
    expected_token,
    should_refuse_to_start,
)
from .server import WlensMCPServer, create_server


def create_app(
    config: Config,
    *,
    token: str | None,
    no_auth: bool,
    allowed_hosts: list[str] | None = None,
) -> tuple[Any, WlensMCPServer]:
    """Build the ASGI app + return the server bundle for lifecycle hooks."""
    server = create_server(config, allowed_hosts=allowed_hosts)
    _register_custom_routes(server, config)
    app = server.mcp.streamable_http_app()
    wrapped = BearerAuthMiddleware(
        app,
        token=token,
        disabled=no_auth,
        open_paths=DEFAULT_OPEN_PATHS,
    )
    return wrapped, server


def run(
    config: Config,
    *,
    host: str,
    port: int,
    dangerously_share: bool,
    no_auth: bool,
    token_override: str | None = None,
    allowed_hosts: list[str] | None = None,
) -> int:
    """Start the uvicorn server. Blocks until SIGINT/SIGTERM."""
    token = token_override if token_override is not None else expected_token()

    refusal = should_refuse_to_start(
        host=host,
        token=token,
        no_auth=no_auth,
        dangerously_share=dangerously_share,
    )
    if refusal:
        logs.event("boot_refused", reason=refusal)
        print(f"error: {refusal}")
        return 2

    app, server = create_app(
        config,
        token=token,
        no_auth=no_auth,
        allowed_hosts=allowed_hosts,
    )

    logs.event(
        "boot",
        host=host,
        port=port,
        auth="token" if token else ("off" if no_auth else "none"),
        executor=config.executor.kind or "none",
        output_dir=str(config.output_dir),
    )

    uconfig = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        lifespan="on",
    )
    uvserver = uvicorn.Server(uconfig)

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_serve_with_shutdown(uvserver, server))
    finally:
        loop.close()
        logs.event("shutdown")
    return 0


async def _serve_with_shutdown(uvserver: uvicorn.Server, server: WlensMCPServer) -> None:
    """Run uvicorn until it exits; close the warehouse connection on the way out."""
    try:
        await uvserver.serve()
    finally:
        server.close()


# ─── Custom routes: /health + /refresh ─────────────────────────────────────


def _register_custom_routes(server: WlensMCPServer, config: Config) -> None:
    mcp = server.mcp

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> Response:
        return JSONResponse(
            {
                "status": "ok",
                "executor": config.executor.kind or None,
                "auth": "token" if expected_token() else "off",
            }
        )

    @mcp.custom_route("/refresh", methods=["POST"])
    async def refresh(_request: Request) -> Response:
        started = time.perf_counter()
        try:
            count = _do_refresh(config)
        except Exception as e:  # noqa: BLE001
            logs.event("refresh_failed", error=repr(e))
            return JSONResponse({"error": str(e)}, status_code=500)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logs.event("refresh_ok", entities=count, elapsed_ms=elapsed_ms)
        return JSONResponse({"entities": count, "elapsed_ms": elapsed_ms})


def _do_refresh(config: Config) -> int:
    """Re-run `wlens generate` in-process. Returns the entity count."""
    from ..adapters.dbt import DbtAdapter
    from ..entities.loader import load_entities
    from ..executor import build_executor
    from ..render.markdown import render_and_write_all

    if config.adapter.kind != "dbt":
        raise NotImplementedError(f"refresh not implemented for adapter {config.adapter.kind!r}")

    adapter = DbtAdapter(config)
    entities = adapter.list_entities()
    custom_entities = load_entities(config)

    executor = None
    if config.output.include_sample_rows:
        try:
            executor = build_executor(config)
        except Exception as e:  # noqa: BLE001
            logs.event("refresh_executor_failed", error=repr(e))
            executor = None
    try:
        count = render_and_write_all(entities, custom_entities, executor, config)
    finally:
        if executor is not None:
            executor.close()
    return count
