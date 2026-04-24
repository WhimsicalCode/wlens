"""Bearer-auth middleware + fail-closed startup rules."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from wlens.mcp.auth import (
    BearerAuthMiddleware,
    DEFAULT_OPEN_PATHS,
    should_refuse_to_start,
)


def _app(*, token: str | None, disabled: bool = False) -> Starlette:
    async def health(_request):
        return JSONResponse({"status": "ok"})

    async def protected(_request):
        return JSONResponse({"data": "secret"})

    inner = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/mcp", protected, methods=["GET"]),
            Route("/refresh", protected, methods=["POST"]),
        ]
    )
    wrapped = BearerAuthMiddleware(
        inner,
        token=token,
        disabled=disabled,
        open_paths=DEFAULT_OPEN_PATHS,
    )

    # TestClient needs an ASGI app at the top — BearerAuthMiddleware IS one,
    # but TestClient expects a Starlette-like. Wrap it.
    outer = Starlette(lifespan=None)
    outer.router.lifespan_context = None  # type: ignore[assignment]
    outer = _wrap_for_testclient(wrapped)
    return outer


def _wrap_for_testclient(asgi_app):
    """Make a raw ASGI middleware testable via starlette.TestClient."""
    # Starlette's TestClient works with any ASGI callable; just return it.
    return asgi_app  # type: ignore[return-value]


def test_health_is_always_open():
    app = _app(token="s3cret")
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200


def test_protected_without_token_is_rejected():
    app = _app(token="s3cret")
    with TestClient(app) as client:
        r = client.get("/mcp")
        assert r.status_code == 401
        assert r.json() == {"error": "missing_bearer_token"}
        assert r.headers["www-authenticate"].startswith("Bearer")


def test_wrong_scheme_is_rejected():
    app = _app(token="s3cret")
    with TestClient(app) as client:
        r = client.get("/mcp", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert r.status_code == 401


def test_wrong_token_is_rejected():
    app = _app(token="s3cret")
    with TestClient(app) as client:
        r = client.get("/mcp", headers={"Authorization": "Bearer WRONG"})
        assert r.status_code == 401
        assert r.json() == {"error": "invalid_bearer_token"}


def test_correct_token_is_accepted():
    app = _app(token="s3cret")
    with TestClient(app) as client:
        r = client.get("/mcp", headers={"Authorization": "Bearer s3cret"})
        assert r.status_code == 200


def test_disabled_middleware_passes_everything():
    app = _app(token="s3cret", disabled=True)
    with TestClient(app) as client:
        r = client.get("/mcp")  # no auth
        assert r.status_code == 200


def test_no_token_configured_passes_everything():
    app = _app(token=None)
    with TestClient(app) as client:
        assert client.get("/mcp").status_code == 200


# ─── Fail-closed rules ──────────────────────────────────────────────────────


def test_refuse_on_public_bind_without_token():
    refusal = should_refuse_to_start(
        host="0.0.0.0", token=None, no_auth=False, dangerously_share=False
    )
    assert refusal and "Refusing to start" in refusal


def test_refuse_no_auth_on_public_bind():
    refusal = should_refuse_to_start(
        host="0.0.0.0", token=None, no_auth=True, dangerously_share=False
    )
    assert refusal and "--no-auth" in refusal


def test_allow_public_bind_with_token():
    assert (
        should_refuse_to_start(
            host="0.0.0.0", token="s3cret", no_auth=False, dangerously_share=False
        )
        is None
    )


def test_allow_localhost_without_token():
    for host in ("127.0.0.1", "localhost", "::1"):
        assert (
            should_refuse_to_start(
                host=host, token=None, no_auth=False, dangerously_share=False
            )
            is None
        )


def test_dangerously_share_bypasses_refusal():
    assert (
        should_refuse_to_start(
            host="0.0.0.0", token=None, no_auth=False, dangerously_share=True
        )
        is None
    )
