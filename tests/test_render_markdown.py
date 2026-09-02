"""End-to-end render: tiny manifest → markdown shape."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from wlens.adapters.base import Column, Entity
from wlens.adapters.dbt import DbtAdapter
from wlens.config import load_config
from wlens.entities.loader import load_entities
from wlens.render.markdown import render_and_write_all


def _setup_project(tmp_path: Path, dbt_project: Path, extra: str = "") -> Path:
    cfg_path = tmp_path / "wlens.yml"
    cfg_path.write_text(
        textwrap.dedent(f"""
            adapter:
              kind: dbt
              project_dir: {dbt_project.relative_to(tmp_path)}
              include_prefixes: [dim_, fct_, raw_]
            output:
              dir: .claude/schema
              include_sample_rows: false
              sample_size: 5
            {extra}
        """).lstrip()
    )
    return cfg_path


class _StubExecutor:
    """Minimal `run_direct(sql)` stand-in for sample-row fetching."""

    def __init__(self, headers: list[str], rows: list[tuple]):
        self._headers = headers
        self._rows = rows
        self.calls = 0

    def run_direct(self, sql: str):  # noqa: ARG002 — sql ignored, fixed payload
        self.calls += 1
        return self._headers, self._rows, False


def test_sample_rows_obfuscate_embedded_pii(tmp_path, dbt_project):
    uuid_pattern = (
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    )
    cfg_path = tmp_path / "wlens.yml"
    cfg_path.write_text(
        textwrap.dedent(f"""
            adapter:
              kind: dbt
              project_dir: {dbt_project.relative_to(tmp_path)}
              include_prefixes: [dim_]
            output:
              dir: .claude/schema
              include_sample_rows: true
              sample_size: 2
              obfuscate:
                - pattern: '{uuid_pattern}'
                  replacement: '<uuid>'
                - pattern: '\\bemp-\\d{{6}}\\b'
                  replacement: '<employee_id>'
        """).lstrip()
    )
    cfg = load_config(cfg_path)
    entities = [e for e in DbtAdapter(cfg).list_entities() if e.slug == "prod.dim_widget"]
    assert entities, "expected dim_widget in fixtures"

    executor = _StubExecutor(
        headers=["widget_id", "color", "notes"],
        rows=[
            (1, "red", "owner foo@bar.com id 550e8400-e29b-41d4-a716-446655440000"),
            (2, "blue", "ticket from 10.0.0.5 by emp-123456"),
        ],
    )
    render_and_write_all(entities, [], executor, cfg)

    md = (cfg.output_dir / "prod.dim_widget.md").read_text()
    # Built-in patterns scrubbed.
    assert "foo@bar.com" not in md
    assert "10.0.0.5" not in md
    assert "<email>" in md
    assert "<ip>" in md
    # User-supplied extras applied.
    assert "550e8400" not in md
    assert "<uuid>" in md
    assert "emp-123456" not in md
    assert "<employee_id>" in md


def test_renders_one_file_per_entity(tmp_path, dbt_project):
    cfg = load_config(_setup_project(tmp_path, dbt_project))
    entities = DbtAdapter(cfg).list_entities()
    custom = load_entities(cfg)

    count = render_and_write_all(entities, custom, None, cfg)
    assert count == len(entities)

    out = cfg.output_dir
    assert (out / "_index.md").exists()
    assert (out / "prod.dim_widget.md").exists()
    assert (out / "prod.fct_sale.md").exists()
    assert (out / "raw.raw_widget.md").exists()


def test_markdown_shape_and_pii_handling(tmp_path, dbt_project):
    cfg = load_config(_setup_project(tmp_path, dbt_project))
    entities = DbtAdapter(cfg).list_entities()
    render_and_write_all(entities, [], None, cfg)

    widget_md = (cfg.output_dir / "prod.dim_widget.md").read_text()
    # Per-column H3 headers for greppability.
    assert "### widget_id" in widget_md
    assert "### color" in widget_md
    # The long-form description survives.
    assert "Dimension of every widget" in widget_md
    # Parents section surfaces the source dependency.
    assert "source:raw_widget" in widget_md
    # Compiled SQL section appears (we fall back to raw_code).
    assert "## Compiled SQL" in widget_md


def test_manual_notes_preserved(tmp_path, dbt_project):
    cfg = load_config(_setup_project(tmp_path, dbt_project))
    entities = DbtAdapter(cfg).list_entities()
    render_and_write_all(entities, [], None, cfg)

    widget_path = cfg.output_dir / "prod.dim_widget.md"
    existing = widget_path.read_text()
    widget_path.write_text(
        existing
        + "\n"
        + "<!-- ↓ MANUAL NOTES BELOW (PRESERVED ACROSS REGENERATION) ↓ -->\n"
        + "Team note: avoid joining on `color`, prefer `widget_id`.\n"
    )

    # Regenerate — the manual note must survive.
    render_and_write_all(entities, [], None, cfg)
    after = widget_path.read_text()
    assert "Team note: avoid joining on `color`" in after


def test_index_uses_first_sentence_summary(tmp_path, dbt_project):
    cfg = load_config(_setup_project(tmp_path, dbt_project))
    entities = DbtAdapter(cfg).list_entities()
    render_and_write_all(entities, [], None, cfg)

    index = (cfg.output_dir / "_index.md").read_text()
    # dim_widget's description starts with a short sentence.
    assert "Dimension of every widget." in index
    # The full multi-sentence description is NOT in the index.
    assert "One row per widget." not in index


def test_events_inlined_into_source(tmp_path, dbt_project, fixtures):
    """End-to-end: load `examples/events.py` via plugins, render against a dbt source."""
    events_example = Path(__file__).resolve().parents[1] / "examples" / "events.py"
    cfg_path = tmp_path / "wlens.yml"
    cfg_path.write_text(
        textwrap.dedent(f"""
            adapter:
              kind: dbt
              project_dir: {dbt_project.relative_to(tmp_path)}
              include_prefixes: [raw_]
            output:
              dir: .claude/schema
              include_sample_rows: false
            plugins:
              - {events_example}
            entities:
              - kind: events
                source: events.tiny.yml
                table: raw.raw_event
                core_columns: [id, created, event]
        """).lstrip()
    )
    (tmp_path / "events.tiny.yml").write_text((fixtures / "events.tiny.yml").read_text())

    cfg = load_config(cfg_path)
    entities = DbtAdapter(cfg).list_entities()
    custom = load_entities(cfg)
    render_and_write_all(entities, custom, None, cfg)

    event_md = (cfg.output_dir / "raw.raw_event.md").read_text()
    assert "### `widget-created`" in event_md
    assert "**Attributes:**" in event_md
    assert "**Example query:**" in event_md
    assert 'data."color"::text as "color"' in event_md


def test_table_catalog_zero_code(tmp_path, dbt_project):
    """A user can declare a brand-new kind in wlens.yml with no Python."""
    (tmp_path / "flags.yml").write_text(
        textwrap.dedent("""
            beta-dashboard:
              description: Enables the redesigned dashboard.
              attributes:
                owner: dashboard-team
                rollout: 25%
        """).lstrip()
    )
    cfg_path = tmp_path / "wlens.yml"
    cfg_path.write_text(
        textwrap.dedent(f"""
            adapter:
              kind: dbt
              project_dir: {dbt_project.relative_to(tmp_path)}
              include_prefixes: [raw_]
            output:
              dir: .claude/schema
              include_sample_rows: false
            entities:
              - kind: feature_flags
                title: Feature flags
                source: flags.yml
                table: raw.raw_event
        """).lstrip()
    )

    cfg = load_config(cfg_path)
    entities = DbtAdapter(cfg).list_entities()
    custom = load_entities(cfg)
    render_and_write_all(entities, custom, None, cfg)

    md = (cfg.output_dir / "raw.raw_event.md").read_text()
    assert "## Feature flags" in md
    assert "### `beta-dashboard`" in md
    assert "Enables the redesigned dashboard." in md
    assert "**Attributes:**" in md
    assert "- `owner` — dashboard-team" in md


def test_table_catalog_plugin(tmp_path, dbt_project):
    """A user can subclass TableCatalog from a plugin file referenced in wlens.yml."""
    (tmp_path / "wlens_catalogs.py").write_text(
        textwrap.dedent('''
            from dataclasses import dataclass
            from wlens.entities import TableCatalog

            @dataclass
            class IncidentsCatalog(TableCatalog):
                kind: str = "incidents"
                title: str = "Incidents"

                def entry_extras(self, name, spec):
                    runbook = spec.get("runbook")
                    return ["", f"[Runbook]({runbook})", ""] if runbook else []
        ''').lstrip()
    )
    (tmp_path / "incidents.yml").write_text(
        textwrap.dedent("""
            login-outage-2024-09:
              description: Login flow returned 500s for 14 minutes.
              severity: high
              runbook: https://runbooks.example/login-outage
        """).lstrip()
    )
    cfg_path = tmp_path / "wlens.yml"
    cfg_path.write_text(
        textwrap.dedent(f"""
            adapter:
              kind: dbt
              project_dir: {dbt_project.relative_to(tmp_path)}
              include_prefixes: [raw_]
            output:
              dir: .claude/schema
              include_sample_rows: false
            plugins:
              - ./wlens_catalogs.py
            entities:
              - kind: incidents
                source: incidents.yml
                table: raw.raw_event
        """).lstrip()
    )

    cfg = load_config(cfg_path)
    entities = DbtAdapter(cfg).list_entities()
    custom = load_entities(cfg)
    render_and_write_all(entities, custom, None, cfg)

    md = (cfg.output_dir / "raw.raw_event.md").read_text()
    assert "## Incidents" in md
    assert "### `login-outage-2024-09`" in md
    # Auto-rendered scalar from the base TableCatalog.
    assert "**Severity:** high" in md
    # Subclass entry_extras still runs (auto-render can't make a markdown link).
    assert "[Runbook](https://runbooks.example/login-outage)" in md


def test_plans_example_renders():
    """Smoke test for the in-tree PlansCatalog example so it doesn't bitrot.

    Loads `examples/plans.py` by path (the same way the plugin loader does)
    so the file's external location matches how a user would consume it.
    """
    import importlib.util
    import sys

    example_path = Path(__file__).resolve().parents[1] / "examples" / "plans.py"
    spec = importlib.util.spec_from_file_location("plans_example", example_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    cat = mod.PlansCatalog(
        table="public.plans",
        entries={
            "free": {
                "description": "Free tier.",
                "price": "$0",
                "seats_included": 1,
            },
            "pro": {
                "description": "Working teams.",
                "price": "$12 / seat / month",
                "seats_included": 5,
                "pricing_page": "https://example.com/pro",
            },
            "legacy": {
                "description": "Old plan, kept for grandfathered customers.",
                "price": "$8 / seat / month",
                "seats_included": 3,
                "deprecated": True,
            },
        },
    )
    md = "\n".join(cat.render())

    assert "## Plans" in md
    # intro() comparison table appears once at the top.
    assert "At a glance:" in md
    assert "| Tier | Price | Seats |" in md
    assert "| `free` | $0 | 1 |" in md
    assert "| `pro` | $12 / seat / month | 5 |" in md
    # entry_extras() — link only on entries with pricing_page.
    assert "[See `pro` pricing](https://example.com/pro)" in md
    assert "[See `free` pricing]" not in md
    # entry_extras() — conditional callout only when deprecated.
    assert "**Deprecated** — new signups are not allowed." in md
    # auto-render still handles plain scalars.
    assert "**Price:** $0" in md
    assert "**Seats included:** 5" in md


# ─── Sample-row cache ───────────────────────────────────────────────────────


def _widget_executor() -> _StubExecutor:
    return _StubExecutor(
        headers=["widget_id", "color"],
        rows=[(1, "red"), (2, "blue")],
    )


def _widget_only(entities):
    return [e for e in entities if e.slug == "prod.dim_widget"]


def test_duplicate_model_and_source_share_one_cache_entry(tmp_path, dbt_project):
    """A model/source pair for one physical relation must not overwrite the
    same cache twice on every generate run."""
    cfg = load_config(_setup_project(tmp_path, dbt_project))
    cfg.output.include_sample_rows = True
    model = _widget_only(DbtAdapter(cfg).list_entities())[0]
    source = Entity(
        kind="source",
        schema_name=model.schema_name,
        table_name=model.table_name,
        description="Source definition wins, matching the old final output.",
        columns={
            "widget_id": Column(name="widget_id"),
            # Deliberately differs from the model fingerprint. Before
            # deduplication, these two entities invalidated each other.
            "source_color": Column(name="source_color"),
        },
    )
    executor = _StubExecutor(
        headers=["widget_id", "source_color"],
        rows=[(1, "red")],
    )

    count = render_and_write_all([model, source], [], executor, cfg)
    assert count == 1
    assert executor.calls == 1
    first_cache = (cfg.cache_dir / "prod.dim_widget.json").read_text()

    count = render_and_write_all([model, source], [], executor, cfg)
    assert count == 1
    assert executor.calls == 1
    assert (cfg.cache_dir / "prod.dim_widget.json").read_text() == first_cache
    assert "(source)" in (cfg.output_dir / "prod.dim_widget.md").read_text()


def test_sample_cache_skips_requery_on_second_run(tmp_path, dbt_project):
    cfg = load_config(_setup_project(tmp_path, dbt_project))
    cfg.output.include_sample_rows = True
    entities = _widget_only(DbtAdapter(cfg).list_entities())
    executor = _widget_executor()

    render_and_write_all(entities, [], executor, cfg)
    first_md = (cfg.output_dir / "prod.dim_widget.md").read_text()
    assert executor.calls == 1
    cache_file = cfg.cache_dir / "prod.dim_widget.json"
    assert cache_file.exists()

    # Second run: same executor, same cache → zero new queries, identical output.
    render_and_write_all(entities, [], executor, cfg)
    second_md = (cfg.output_dir / "prod.dim_widget.md").read_text()
    assert executor.calls == 1, "cache hit must not re-query"
    assert first_md == second_md


def test_sample_cache_hash_ignores_type_and_column_order_drift(tmp_path, dbt_project):
    cfg = load_config(_setup_project(tmp_path, dbt_project))
    cfg.output.include_sample_rows = True
    entities = _widget_only(DbtAdapter(cfg).list_entities())
    executor = _widget_executor()

    render_and_write_all(entities, [], executor, cfg)
    assert executor.calls == 1

    cache_file = cfg.cache_dir / "prod.dim_widget.json"
    assert json.loads(cache_file.read_text())["column_names_hash"].startswith("sha256:")

    # Neither data types nor manifest column order affect rendered sample cells.
    # Keep the cache pinned when only that metadata drifts between dbt compiles.
    next(iter(entities[0].columns.values())).data_type = "TOTALLY_NEW_TYPE"
    entities[0].columns = dict(reversed(entities[0].columns.items()))
    render_and_write_all(entities, [], executor, cfg)
    assert executor.calls == 1


def test_sample_cache_refetches_when_column_names_change(tmp_path, dbt_project):
    cfg = load_config(_setup_project(tmp_path, dbt_project))
    cfg.output.include_sample_rows = True
    entities = _widget_only(DbtAdapter(cfg).list_entities())
    executor = _widget_executor()

    render_and_write_all(entities, [], executor, cfg)
    assert executor.calls == 1

    entities[0].columns["new_column"] = Column(name="new_column", data_type="varchar")
    render_and_write_all(entities, [], executor, cfg)
    assert executor.calls == 2


def test_sample_cache_refetches_when_pii_status_changes(tmp_path, dbt_project):
    cfg = load_config(_setup_project(tmp_path, dbt_project))
    cfg.output.include_sample_rows = True
    entities = _widget_only(DbtAdapter(cfg).list_entities())
    executor = _widget_executor()

    render_and_write_all(entities, [], executor, cfg)
    assert executor.calls == 1

    entities[0].columns["color"].meta["pii"] = True
    render_and_write_all(entities, [], executor, cfg)
    assert executor.calls == 2
    md = (cfg.output_dir / "prod.dim_widget.md").read_text()
    assert "- `color`: <pii> | <pii>" in md


def test_sample_cache_refetches_when_obfuscation_changes(tmp_path, dbt_project):
    cfg = load_config(_setup_project(tmp_path, dbt_project))
    cfg.output.include_sample_rows = True
    entities = _widget_only(DbtAdapter(cfg).list_entities())
    executor = _widget_executor()

    render_and_write_all(entities, [], executor, cfg)
    assert executor.calls == 1

    cfg.output.obfuscate = [{"pattern": "red", "replacement": "<color>"}]
    render_and_write_all(entities, [], executor, cfg)
    assert executor.calls == 2
    md = (cfg.output_dir / "prod.dim_widget.md").read_text()
    assert "<color>" in md


def test_refresh_samples_all_forces_requery(tmp_path, dbt_project):
    cfg = load_config(_setup_project(tmp_path, dbt_project))
    cfg.output.include_sample_rows = True
    entities = _widget_only(DbtAdapter(cfg).list_entities())
    executor = _widget_executor()

    render_and_write_all(entities, [], executor, cfg)
    assert executor.calls == 1

    render_and_write_all(entities, [], executor, cfg, refresh_samples=True)
    assert executor.calls == 2


def test_refresh_samples_slug_targets_one_entity(tmp_path, dbt_project):
    cfg = load_config(_setup_project(tmp_path, dbt_project))
    cfg.output.include_sample_rows = True
    entities = [
        e for e in DbtAdapter(cfg).list_entities()
        if e.slug in {"prod.dim_widget", "prod.fct_sale"}
    ]
    executor = _StubExecutor(headers=["a"], rows=[(1,)])

    render_and_write_all(entities, [], executor, cfg)
    base_calls = executor.calls
    assert base_calls >= 2  # one per entity on cold cache

    render_and_write_all(
        entities, [], executor, cfg, refresh_samples={"prod.dim_widget"}
    )
    # Only one entity refetched; the other should hit the cache.
    assert executor.calls == base_calls + 1


class _FlakyExecutor(_StubExecutor):
    """Returns headers/rows on the first call, then empty on subsequent calls."""

    def run_direct(self, sql: str):  # noqa: ARG002
        self.calls += 1
        if self.calls == 1:
            return self._headers, self._rows, False
        return self._headers, [], False


def test_refresh_falls_back_to_cache_when_fetch_returns_empty(tmp_path, dbt_project):
    """Forced refresh that comes back empty must not drop the cache or the
    rendered section — the next run should still produce identical .md."""
    cfg = load_config(_setup_project(tmp_path, dbt_project))
    cfg.output.include_sample_rows = True
    entities = _widget_only(DbtAdapter(cfg).list_entities())

    executor = _FlakyExecutor(headers=["widget_id", "color"], rows=[(1, "red"), (2, "blue")])

    render_and_write_all(entities, [], executor, cfg)
    first_md = (cfg.output_dir / "prod.dim_widget.md").read_text()
    assert "## Sample rows" in first_md

    # Second run: --refresh-samples forces a fetch, but the executor returns
    # nothing. The .md must still contain the previously-cached rows.
    render_and_write_all(entities, [], executor, cfg, refresh_samples=True)
    second_md = (cfg.output_dir / "prod.dim_widget.md").read_text()
    assert second_md == first_md
    assert executor.calls == 2  # the refresh did try to fetch


def test_sample_cache_reused_when_no_executor(tmp_path, dbt_project):
    cfg = load_config(_setup_project(tmp_path, dbt_project))
    cfg.output.include_sample_rows = True
    entities = _widget_only(DbtAdapter(cfg).list_entities())
    executor = _widget_executor()

    render_and_write_all(entities, [], executor, cfg)
    md_with_executor = (cfg.output_dir / "prod.dim_widget.md").read_text()

    # Wipe the markdown so we know the second render had to build it from scratch.
    (cfg.output_dir / "prod.dim_widget.md").unlink()

    render_and_write_all(entities, [], None, cfg)
    md_offline = (cfg.output_dir / "prod.dim_widget.md").read_text()
    assert "## Sample rows" in md_offline
    assert md_offline == md_with_executor
