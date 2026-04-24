"""Structured stdout logger for MCP request-level events.

Default format is `key=value` pairs (human-readable, grep-friendly).
Set `WLENS_LOG_FORMAT=json` to emit JSON lines for log aggregators.

Token values are hashed before logging (never log raw bearer tokens).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from typing import Any


def _format(fields: dict[str, Any]) -> str:
    fmt = os.environ.get("WLENS_LOG_FORMAT", "").lower()
    if fmt == "json":
        return json.dumps(fields, default=str, separators=(",", ":"))
    parts: list[str] = []
    for k, v in fields.items():
        if isinstance(v, str) and (" " in v or "=" in v or '"' in v):
            escaped = v.replace('"', '\\"')
            parts.append(f'{k}="{escaped}"')
        else:
            parts.append(f"{k}={v}")
    return " ".join(parts)


def event(kind: str, **fields: Any) -> None:
    """Emit a single structured log line to stdout."""
    line = _format({"ts": f"{time.time():.3f}", "event": kind, **fields})
    print(line, file=sys.stdout, flush=True)


def hash_token(token: str | None) -> str:
    """Return a short non-reversible fingerprint of a bearer token."""
    if not token:
        return "none"
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def hash_sql(sql: str) -> str:
    """Return a short stable hash for a SQL query (for correlating log lines)."""
    normalized = " ".join(sql.split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]
