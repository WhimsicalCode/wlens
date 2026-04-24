"""DbtAdapter.list_entities against the tiny manifest fixture."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from wlens.adapters.dbt import DbtAdapter
from wlens.config import load_config


def _config(tmp_path: Path, dbt_project: Path, extra: str = "") -> Path:
    cfg_path = tmp_path / "wlens.yml"
    cfg_path.write_text(
        textwrap.dedent(f"""
            adapter:
              kind: dbt
              project_dir: {dbt_project.relative_to(tmp_path)}
              {extra}
        """).lstrip()
    )
    return cfg_path


def test_lists_models_and_sources(tmp_path, dbt_project):
    cfg = load_config(_config(tmp_path, dbt_project))
    entities = DbtAdapter(cfg).list_entities()
    names = {(e.kind, e.table_name) for e in entities}
    # stg_* is a model too — no filters = everything
    assert ("model", "dim_widget") in names
    assert ("model", "fct_sale") in names
    assert ("model", "stg_raw_widget") in names
    assert ("source", "raw_widget") in names
    assert ("source", "raw_event") in names


def test_include_prefixes_filters_to_marts(tmp_path, dbt_project):
    cfg = load_config(_config(tmp_path, dbt_project, "include_prefixes: [dim_, fct_]"))
    entities = DbtAdapter(cfg).list_entities()
    model_names = {e.table_name for e in entities if e.kind == "model"}
    assert model_names == {"dim_widget", "fct_sale"}
    # Sources also filtered by same prefix list, so they are excluded.
    assert not any(e.kind == "source" for e in entities)


def test_exclude_prefixes_skips_staging(tmp_path, dbt_project):
    cfg = load_config(_config(tmp_path, dbt_project, "exclude_prefixes: [stg_]"))
    entities = DbtAdapter(cfg).list_entities()
    model_names = {e.table_name for e in entities if e.kind == "model"}
    assert "stg_raw_widget" not in model_names
    assert {"dim_widget", "fct_sale"}.issubset(model_names)


def test_columns_and_description_propagate(tmp_path, dbt_project):
    cfg = load_config(_config(tmp_path, dbt_project, "include_prefixes: [dim_]"))
    (entity,) = [e for e in DbtAdapter(cfg).list_entities() if e.table_name == "dim_widget"]
    assert "widget_id" in entity.columns
    assert entity.columns["widget_id"].tests == ["not_null", "unique"]
    assert "widget" in entity.description.lower()


def test_relationships_test_surfaces_with_kwargs(tmp_path, dbt_project):
    """The whole reason tests matter: `relationships` tells the LLM about foreign keys."""
    cfg = load_config(_config(tmp_path, dbt_project, "include_prefixes: [fct_]"))
    (fct,) = [e for e in DbtAdapter(cfg).list_entities() if e.table_name == "fct_sale"]
    widget_fk_tests = fct.columns["widget_id"].tests
    assert any("relationships" in t for t in widget_fk_tests)
    rel_test = next(t for t in widget_fk_tests if "relationships" in t)
    # The `to` and `field` kwargs must survive into the rendered string so the
    # LLM can see which table this FK points at.
    assert "to=ref('dim_widget')" in rel_test
    assert "field=widget_id" in rel_test


def test_accepted_values_test_surfaces_enum(tmp_path, dbt_project):
    cfg = load_config(_config(tmp_path, dbt_project, "include_prefixes: [fct_]"))
    (fct,) = [e for e in DbtAdapter(cfg).list_entities() if e.table_name == "fct_sale"]
    status_tests = fct.columns["status"].tests
    assert any("accepted_values" in t for t in status_tests)
    av = next(t for t in status_tests if "accepted_values" in t)
    # Enum members rendered inline so the LLM can see the allowed values.
    assert "values=[shipped, completed, pending]" in av


def test_columns_without_tests_have_empty_list(tmp_path, dbt_project):
    cfg = load_config(_config(tmp_path, dbt_project, "include_prefixes: [dim_]"))
    (entity,) = [e for e in DbtAdapter(cfg).list_entities() if e.table_name == "dim_widget"]
    # `color` has no tests — should come back as [].
    assert entity.columns["color"].tests == []


def test_manifest_missing_raises(tmp_path):
    cfg_path = tmp_path / "wlens.yml"
    cfg_path.write_text("adapter:\n  kind: dbt\n  project_dir: nope\n")
    cfg = load_config(cfg_path)
    with pytest.raises(FileNotFoundError, match="manifest.json"):
        DbtAdapter(cfg).list_entities()
