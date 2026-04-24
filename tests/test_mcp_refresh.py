"""POST /refresh — regenerates .claude/schema/ in-process."""

from __future__ import annotations

import textwrap
from pathlib import Path

from starlette.testclient import TestClient

from wlens.config import load_config
from wlens.mcp.app import create_app


def _config(tmp_path: Path, dbt_project: Path) -> Path:
    cfg_path = tmp_path / "wlens.yml"
    cfg_path.write_text(
        textwrap.dedent(f"""
            adapter:
              kind: dbt
              project_dir: {dbt_project.relative_to(tmp_path)}
              include_prefixes: [dim_, fct_]
            output:
              dir: .claude/schema
              include_sample_rows: false
        """).lstrip()
    )
    return cfg_path


def test_refresh_generates_markdown_files(tmp_path, dbt_project):
    cfg = load_config(_config(tmp_path, dbt_project))
    app, server = create_app(cfg, token="tok", no_auth=False)
    try:
        with TestClient(app) as client:
            r = client.post("/refresh", headers={"Authorization": "Bearer tok"})
            assert r.status_code == 200
            body = r.json()
            assert body["entities"] == 2  # dim_widget + fct_sale from the fixture
            assert body["elapsed_ms"] >= 0
        assert (cfg.output_dir / "_index.md").exists()
        assert (cfg.output_dir / "prod.dim_widget.md").exists()
        assert (cfg.output_dir / "prod.fct_sale.md").exists()
    finally:
        server.close()


def test_health_open_refresh_protected(tmp_path, dbt_project):
    cfg = load_config(_config(tmp_path, dbt_project))
    app, server = create_app(cfg, token="tok", no_auth=False)
    try:
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            # Refresh without auth → 401
            r = client.post("/refresh")
            assert r.status_code == 401
            # Wrong token → 401
            r = client.post("/refresh", headers={"Authorization": "Bearer wrong"})
            assert r.status_code == 401
    finally:
        server.close()


def test_health_reports_config(tmp_path, dbt_project):
    cfg = load_config(_config(tmp_path, dbt_project))
    app, server = create_app(cfg, token=None, no_auth=True)
    try:
        with TestClient(app) as client:
            body = client.get("/health").json()
            assert body["status"] == "ok"
            # Adapter is dbt; executor is unset in this config.
            assert body["executor"] is None
    finally:
        server.close()
