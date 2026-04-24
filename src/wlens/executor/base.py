"""Executor ABC with shared read-only guard and disk cache.

The read-only guard is applied in `run()` before the SQL ever hits the
warehouse — subclasses that need to skip the guard (e.g. internal metadata
queries for sample-row fetching) use `run_direct()` instead.

Cache is keyed by (SQL text, today's date). Including the date guarantees
queries using `CURRENT_DATE` get fresh results daily.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Config

logger = logging.getLogger(__name__)

CACHE_DIRNAME = "wlens/cache"
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours
CACHE_SUBDIR = "sql"


class ReadOnlyViolation(ValueError):
    """Raised when the read-only guard rejects a query."""


_MUTATION_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "COPY", "GRANT", "REVOKE", "MERGE", "CALL", "EXECUTE", "DO", "COMMENT",
    "VACUUM", "ANALYZE", "LOCK", "SET", "RESET", "BEGIN", "COMMIT", "ROLLBACK",
    "SAVEPOINT", "REINDEX", "UNLOAD",
}

_COMMENT_LINE = re.compile(r"--[^\n]*")
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def assert_read_only(sql: str) -> None:
    """Reject any SQL that is not a single SELECT / WITH … SELECT statement.

    Implementation is deliberately conservative: strip comments, reject any
    trailing semicolon-separated second statement, require the first token to
    be SELECT or WITH, and scan the full text for mutation keywords as a
    belt-and-suspenders safety net.

    Raises ReadOnlyViolation on rejection.
    """
    if not sql or not sql.strip():
        raise ReadOnlyViolation("empty SQL")

    stripped = _strip_comments(sql).strip()
    # Drop a single trailing semicolon, but reject anything after it.
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    if ";" in stripped:
        raise ReadOnlyViolation("multiple statements are not allowed")

    first_match = _IDENTIFIER.search(stripped)
    if first_match is None:
        raise ReadOnlyViolation("no SQL keyword found")
    first_token = first_match.group(0).upper()
    if first_token not in {"SELECT", "WITH"}:
        raise ReadOnlyViolation(
            f"only SELECT / WITH queries are allowed (got: {first_token})"
        )

    # Belt-and-suspenders: block any mutation keyword anywhere in the text.
    # This catches injection attempts like `WITH x AS (SELECT 1) DELETE …`.
    for token in _IDENTIFIER.findall(stripped.upper()):
        if token in _MUTATION_KEYWORDS:
            raise ReadOnlyViolation(
                f"query contains disallowed keyword: {token}"
            )


def _strip_comments(sql: str) -> str:
    sql = _COMMENT_BLOCK.sub("", sql)
    sql = _COMMENT_LINE.sub("", sql)
    return sql


@dataclass
class Executor(ABC):
    """Connects to a warehouse and runs read-only SQL."""

    config: "Config"

    def __post_init__(self) -> None:
        self._conn: Any | None = None

    # ────────────────────────────────────────────────────────────────────
    # Public API

    def run(
        self,
        sql: str,
        *,
        use_cache: bool = True,
    ) -> tuple[list[str], list[tuple], bool]:
        """Execute a guarded query, return (headers, rows, cache_hit).

        Caches to `.wlens-cache/sql/` with a daily TTL.
        """
        assert_read_only(sql)
        if use_cache:
            cached = self._cache_get(sql)
            if cached is not None:
                return cached[0], cached[1], True
        headers, rows = self.run_direct(sql)[:2]
        if use_cache:
            self._cache_put(sql, headers, rows)
        return headers, rows, False

    def run_direct(self, sql: str) -> tuple[list[str], list[tuple], bool]:
        """Execute a query without the read-only guard or cache.

        Intended for internal metadata queries (sample-row fetching). Still
        returns the same tuple shape as `run()` for uniform call sites.
        """
        cursor = self._cursor()
        try:
            cursor.execute(sql)
            headers = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = list(cursor.fetchall()) if cursor.description else []
            return headers, rows, False
        finally:
            cursor.close()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ────────────────────────────────────────────────────────────────────
    # Subclass hooks

    @abstractmethod
    def _connect(self) -> Any:
        """Return a DB-API 2.0 connection."""

    def _cursor(self) -> Any:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn.cursor()

    # ────────────────────────────────────────────────────────────────────
    # Cache

    def _cache_dir(self) -> Path:
        return self.config.repo_root / CACHE_DIRNAME / CACHE_SUBDIR

    def _cache_key(self, sql: str) -> str:
        today = time.strftime("%Y-%m-%d")
        normalized = " ".join(sql.split()) + "::" + today
        return hashlib.sha256(normalized.encode()).hexdigest()[:24]

    def _cache_get(self, sql: str) -> tuple[list[str], list[tuple]] | None:
        path = self._cache_dir() / f"{self._cache_key(sql)}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            if time.time() - data["cached_at"] > CACHE_TTL_SECONDS:
                path.unlink(missing_ok=True)
                return None
            return list(data["headers"]), [tuple(r) for r in data["rows"]]
        except (json.JSONDecodeError, KeyError):
            path.unlink(missing_ok=True)
            return None

    def _cache_put(self, sql: str, headers: list[str], rows: list[tuple]) -> None:
        cache_dir = self._cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"{self._cache_key(sql)}.json"
        path.write_text(
            json.dumps({
                "cached_at": time.time(),
                "headers": headers,
                "rows": [list(r) for r in rows],
            }, default=str)
        )


# ─── Markdown table formatting ──────────────────────────────────────────────


def format_markdown_table(headers: list[str], rows: list[tuple]) -> str:
    if not headers:
        return "No results"
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(_cell_str(cell)))

    lines: list[str] = []
    lines.append("| " + " | ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)) + " |")
    lines.append("| " + " | ".join("-" * w for w in widths) + " |")
    for row in rows:
        cells = [_cell_str(c) for c in row]
        lines.append("| " + " | ".join(cells[i].ljust(widths[i]) for i in range(len(cells))) + " |")
    return "\n".join(lines)


def _cell_str(v: Any) -> str:
    return "NULL" if v is None else str(v)
