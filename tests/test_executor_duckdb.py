"""DuckDB executor — actually runs queries against a temp DB file."""

from __future__ import annotations

import textwrap
from pathlib import Path

import duckdb
import pytest

from wlens.config import load_config
from wlens.executor import build_executor
from wlens.executor.base import ReadOnlyViolation


def _make_db(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE widget (id INTEGER, color VARCHAR)")
    con.execute("INSERT INTO widget VALUES (1, 'blue'), (2, 'red'), (3, 'green')")
    con.close()


def _config(tmp_path: Path, db_path: str) -> Path:
    cfg_path = tmp_path / "wlens.yml"
    cfg_path.write_text(
        textwrap.dedent(f"""
            adapter:
              kind: dbt
            executor:
              kind: duckdb
              path: "{db_path}"
        """).lstrip()
    )
    return cfg_path


def test_runs_select_and_returns_rows(tmp_path: Path):
    db = tmp_path / "w.duckdb"
    _make_db(db)
    cfg = load_config(_config(tmp_path, "w.duckdb"))
    ex = build_executor(cfg)
    try:
        headers, rows, cached = ex.run("SELECT color FROM widget ORDER BY id")
        assert headers == ["color"]
        assert [r[0] for r in rows] == ["blue", "red", "green"]
        assert cached is False
    finally:
        ex.close()


def test_read_only_guard_still_applies(tmp_path: Path):
    db = tmp_path / "w.duckdb"
    _make_db(db)
    cfg = load_config(_config(tmp_path, "w.duckdb"))
    ex = build_executor(cfg)
    try:
        with pytest.raises(ReadOnlyViolation):
            ex.run("DELETE FROM widget")
    finally:
        ex.close()


def test_read_only_connection_blocks_writes_at_driver_level(tmp_path: Path):
    """Even if the generic guard were bypassed, DuckDB itself opens read-only."""
    db = tmp_path / "w.duckdb"
    _make_db(db)
    cfg = load_config(_config(tmp_path, "w.duckdb"))
    ex = build_executor(cfg)
    try:
        with pytest.raises(Exception):  # noqa: PT011 — duckdb raises its own error type
            ex.run_direct("INSERT INTO widget VALUES (4, 'yellow')")
    finally:
        ex.close()


def test_missing_file_gives_clear_error(tmp_path: Path):
    cfg = load_config(_config(tmp_path, "does-not-exist.duckdb"))
    ex = build_executor(cfg)
    try:
        with pytest.raises(FileNotFoundError, match="DuckDB file not found"):
            ex.run("SELECT 1")
    finally:
        ex.close()


def test_memory_mode(tmp_path: Path):
    cfg = load_config(_config(tmp_path, ":memory:"))
    ex = build_executor(cfg)
    try:
        headers, rows, _ = ex.run("SELECT 1 + 1 AS n")
        assert headers == ["n"]
        assert rows == [(2,)]
    finally:
        ex.close()


def test_missing_path_raises(tmp_path: Path):
    cfg_path = tmp_path / "wlens.yml"
    cfg_path.write_text("adapter:\n  kind: dbt\nexecutor:\n  kind: duckdb\n")
    ex = build_executor(load_config(cfg_path))
    try:
        with pytest.raises(ValueError, match="executor.path"):
            ex.run("SELECT 1")
    finally:
        ex.close()
