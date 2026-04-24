"""Bearer-token auth middleware for the wlens MCP server.

The server reads the expected token from `WLENS_AUTH_TOKEN` at startup. The
middleware rejects any request that doesn't present `Authorization: Bearer <token>`
matching that value, except for a small exemption list (default: `/health`).

Fail-closed rules are enforced in `should_refuse_to_start()`:
- If the bind host is non-local AND `WLENS_AUTH_TOKEN` is unset AND `--no-auth`
  was not explicitly passed, refuse to boot.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

AUTH_ENV_VAR = "WLENS_AUTH_TOKEN"
DEFAULT_OPEN_PATHS = frozenset({"/health"})


def expected_token() -> str | None:
    """Return the configured bearer token, or None if not set."""
    token = os.environ.get(AUTH_ENV_VAR)
    return token if token else None


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests without a matching bearer token.

    Behaviour:
    - If `disabled=True` → pass every request through (used by `--no-auth`).
    - Else if `token is None` → pass everything through (server not configured
      for auth; should only happen on a local bind, enforced at startup).
    - Else → require `Authorization: Bearer <token>` on every path not in
      `open_paths`. Return 401 otherwise.
    """

    def __init__(
        self,
        app,
        *,
        token: str | None,
        disabled: bool = False,
        open_paths: frozenset[str] = DEFAULT_OPEN_PATHS,
    ) -> None:
        super().__init__(app)
        self._token = token
        self._disabled = disabled
        self._open_paths = open_paths

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if self._disabled or self._token is None:
            return await call_next(request)
        if request.url.path in self._open_paths:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value:
            return JSONResponse(
                {"error": "missing_bearer_token"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="wlens"'},
            )
        if not hmac.compare_digest(value, self._token):
            return JSONResponse({"error": "invalid_bearer_token"}, status_code=401)

        return await call_next(request)


def should_refuse_to_start(
    *, host: str, token: str | None, no_auth: bool, dangerously_share: bool
) -> str | None:
    """Return an error message if the server must refuse to start; else None.

    Rules:
    - `--dangerously-share` is its own world (auth is auto-generated, not
      configured) — never refuses via this function.
    - `--no-auth` is only allowed on a localhost bind.
    - If the host is non-local and no token is set, refuse.
    """
    if dangerously_share:
        return None

    is_local = host in {"127.0.0.1", "localhost", "::1"}
    if no_auth and not is_local:
        return (
            "--no-auth is only allowed on a localhost bind. "
            f"Current host is {host!r}. Set WLENS_AUTH_TOKEN or bind to 127.0.0.1."
        )
    if not no_auth and not token and not is_local:
        return (
            f"Refusing to start on a non-local bind ({host!r}) without authentication. "
            f"Set {AUTH_ENV_VAR} to a strong random secret, or pass --no-auth "
            "and bind to 127.0.0.1 for local-only testing."
        )
    return None
