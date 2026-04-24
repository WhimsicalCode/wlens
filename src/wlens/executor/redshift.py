"""Redshift executor (psycopg2)."""

from __future__ import annotations

from typing import Any

import psycopg2

from .base import Executor


class RedshiftExecutor(Executor):
    def _connect(self) -> Any:
        ex = self.config.executor
        _require(ex.host, "executor.host")
        _require(ex.database, "executor.database")
        _require(ex.user, "executor.user")
        _require(ex.password, "executor.password")
        return psycopg2.connect(
            host=str(ex.host).split(":")[0],
            port=ex.port or 5439,
            database=ex.database,
            user=ex.user,
            password=ex.password,
        )


def _require(value, name: str) -> None:
    if not value:
        raise ValueError(f"wlens.yml is missing `{name}` — required for the redshift executor.")
