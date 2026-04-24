"""DuckDB executor.

DuckDB is a file-based embedded analytical database. Config in wlens.yml
uses a single `path` field instead of host/port/user/password:

    executor:
      kind: duckdb
      path: warehouse.duckdb      # or ":memory:" for an ephemeral in-memory DB

Paths are resolved relative to the wlens.yml location. The connection is
opened **read-only** by default (belt-and-suspenders alongside the generic
read-only guard in `executor/base.py`); set `path: :memory:` or use a
writable path to create a new DB if needed for fixtures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from .base import Executor


class DuckDBExecutor(Executor):
    def _connect(self) -> Any:
        ex = self.config.executor
        if not ex.path:
            raise ValueError("wlens.yml is missing `executor.path` — required for the duckdb executor.")

        # `:memory:` is a special DuckDB URI; don't try to resolve it as a path.
        target: str
        if ex.path == ":memory:":
            target = ":memory:"
            read_only = False
        else:
            target = str((self.config.repo_root / ex.path).resolve())
            read_only = True

        try:
            return duckdb.connect(target, read_only=read_only)
        except duckdb.Error as e:
            # Surface a clearer error than DuckDB's default when the file doesn't exist.
            if read_only and not Path(target).exists():
                raise FileNotFoundError(
                    f"DuckDB file not found: {target}. "
                    "Create it first, or point `executor.path` at an existing database."
                ) from e
            raise
