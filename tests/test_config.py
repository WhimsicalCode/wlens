"""wlens.yml loading + env-var expansion."""

from __future__ import annotations

import textwrap
from pathlib import Path

from wlens.config import load_config


def _write(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip())


def test_env_var_expansion(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WL_TEST_HOST", "db.example.com")
    cfg_path = tmp_path / "wlens.yml"
    _write(cfg_path, """
        adapter:
          kind: dbt
          project_dir: proj
        executor:
          kind: redshift
          host: ${WL_TEST_HOST}
          port: 5439
          database: db
          user: me
          password: secret
    """)

    cfg = load_config(cfg_path)
    assert cfg.executor.host == "db.example.com"
    assert cfg.executor.port == 5439
    assert cfg.adapter.project_dir == "proj"


def test_missing_env_var_becomes_empty(tmp_path: Path):
    cfg_path = tmp_path / "wlens.yml"
    _write(cfg_path, """
        adapter:
          kind: dbt
        executor:
          kind: redshift
          host: ${DOES_NOT_EXIST_123}
    """)
    cfg = load_config(cfg_path)
    assert cfg.executor.host is None  # empty string becomes None


def test_defaults_when_sections_missing(tmp_path: Path):
    cfg_path = tmp_path / "wlens.yml"
    _write(cfg_path, "adapter:\n  kind: dbt\n")
    cfg = load_config(cfg_path)
    assert cfg.adapter.default_schema == "prod"
    # All generated artifacts default under `wlens/` so a consumer repo only
    # sees `wlens.yml` + `wlens/` at its root.
    assert cfg.output.dir == "wlens/schema"
    assert cfg.output.sample_size == 5
    assert cfg.entities == []
