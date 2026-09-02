"""Warehouse executors — run read-only SQL, return rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Executor, ReadOnlyViolation, format_markdown_table

if TYPE_CHECKING:
    from ..config import Config

__all__ = ["Executor", "ReadOnlyViolation", "build_executor", "format_markdown_table"]


def build_executor(config: Config) -> Executor:
    """Instantiate the executor indicated by `wlens.yml`."""
    from .credentials import resolve_credentials

    kind = (config.executor.kind or "").lower()
    if kind in {"redshift", "postgres", "postgresql", "duckdb"}:
        config = resolve_credentials(config)
    if kind in ("redshift",):
        from .redshift import RedshiftExecutor
        return RedshiftExecutor(config)
    if kind in ("postgres", "postgresql"):
        from .postgres import PostgresExecutor
        return PostgresExecutor(config)
    if kind in ("duckdb",):
        from .duckdb import DuckDBExecutor
        return DuckDBExecutor(config)
    if not kind:
        raise ValueError(
            "No `executor.kind` set in wlens.yml. Choose one of: redshift, postgres, duckdb."
        )
    raise ValueError(
        f"Unsupported executor kind: {kind!r}. v0.1 supports: redshift, postgres, duckdb."
    )
