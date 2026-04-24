"""End-to-end render: tiny manifest → markdown shape."""

from __future__ import annotations

import textwrap
from pathlib import Path

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
    # Wire up the events entity pointing at raw.raw_event.
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
              - kind: events
                source: {(fixtures / "events.tiny.yml").relative_to(tmp_path.parent) if False else "events.tiny.yml"}
                inline_into: raw.raw_event
                source_table: raw.raw_event
                core_columns: [id, created, event]
        """).lstrip()
    )
    # Copy the events file so the relative path in the config resolves from cwd.
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
