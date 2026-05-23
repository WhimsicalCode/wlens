"""Render Entities to per-table markdown files + `_index.md`.

Output layout (one file per entity + one index):

    <output_dir>/
      _index.md
      <schema>.<table>.md    # model or source

Each file is grep-optimised: column names render as `### <name>` H3 headers,
enum values appear in prose (LLMs parse this better than wide tables), and
the first section always contains the long-form description.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..adapters.base import Column, Entity, Parent
from . import sample_cache
from .obfuscate import compile_rules, obfuscate
from .pii import PII_REDACTED, pii_column_names
from .preserve import read_manual_block

if TYPE_CHECKING:
    from ..config import Config
    from ..entities.loader import TableCatalog
    from ..executor.base import Executor

logger = logging.getLogger(__name__)

VALUE_MAX_LEN = 200
CELL_MAX_LEN = 80


# ─── Top-level entry point ──────────────────────────────────────────────────


def render_and_write_all(
    entities: list[Entity],
    custom_entities: list["TableCatalog"],
    executor: "Executor | None",
    config: "Config",
    *,
    refresh_samples: bool | set[str] = False,
) -> int:
    """Render every entity + the index into `config.output_dir`.

    `refresh_samples` controls the sample-row cache:
    - `False` (default): reuse cache when valid, refetch otherwise.
    - `True`: ignore cache for every entity, refetch all.
    - `set[str]` of entity slugs: refetch just those, reuse cache for the rest.
    """
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    inline_map = _inline_map(custom_entities)
    sample_size = config.output.sample_size if config.output.include_sample_rows else 0
    obfuscate_value = functools.partial(
        obfuscate, rules=compile_rules(config.output.obfuscate)
    )
    obf_hash = sample_cache.obfuscation_hash(config)
    refresh_all = refresh_samples is True
    refresh_set: set[str] = (
        set(refresh_samples) if isinstance(refresh_samples, (set, list, tuple)) else set()
    )

    for entity in entities:
        rendered_rows = _resolve_sample_rows(
            entity, executor, sample_size, config, obfuscate_value, obf_hash,
            refresh_all=refresh_all, refresh_set=refresh_set,
        )
        body = _render_entity(entity, rendered_rows, inline_map.get(entity.slug))
        _write_file(output_dir / entity.filename, body)

    index_path = output_dir / "_index.md"
    index_path.write_text(_render_index(entities))
    logger.info(f"wrote {index_path}")
    return len(entities)


# ─── Per-entity rendering ───────────────────────────────────────────────────


def _render_entity(
    entity: Entity,
    rendered_rows: list[dict],
    custom_entity: "TableCatalog | None",
) -> str:
    kind_label = "model" if entity.kind == "model" else "source"
    if custom_entity is not None:
        kind_label = f"source — {custom_entity.kind} catalog"

    lines: list[str] = [f"# `{entity.slug}` ({kind_label})", ""]
    if entity.description.strip():
        lines.append(entity.description.strip())
        lines.append("")
    lines.extend(_render_columns(entity.columns))
    if custom_entity is not None:
        lines.extend(custom_entity.render())
    lines.extend(_render_parents(entity.parents))
    lines.extend(_render_sample_rows(rendered_rows))
    lines.extend(_render_compiled_sql(entity.compiled_sql))
    return "\n".join(lines).rstrip() + "\n"


def _render_columns(columns: dict[str, Column]) -> list[str]:
    if not columns:
        return ["## Columns\n\n_(No columns documented.)_\n"]
    lines = ["## Columns", ""]
    for col in columns.values():
        lines.append(f"### {col.name}")
        if col.data_type:
            lines.append(f"- Type: `{col.data_type}`")
        if col.description:
            lines.append("")
            for ln in col.description.splitlines():
                lines.append(ln if ln.strip() else "")
            lines.append("")
        if col.tests:
            lines.append(f"- Tests: {', '.join(col.tests)}")
        lines.append("")
    return lines


def _render_parents(parents: list[Parent]) -> list[str]:
    if not parents:
        return []
    lines = ["## Parents", ""]
    for p in parents:
        desc = _first_sentence(p.description) or "(no description)"
        lines.append(f"- `{p.name}` — {desc}")
    lines.append("")
    return lines


def _render_sample_rows(rendered_rows: list[dict]) -> list[str]:
    """Column-first (transposed) rendering: one bullet per column, pipe-separated values.

    Cells are expected to be already obfuscated + truncated (see
    `_eagerly_render_rows`).
    """
    if not rendered_rows:
        return []
    cols = list(rendered_rows[0].keys())
    n_rows = len(rendered_rows)
    lines = [f"## Sample rows ({n_rows} rows, one line per column)", ""]
    for c in cols:
        values = " | ".join(row.get(c, "") for row in rendered_rows)
        lines.append(f"- `{c}`: {values}")
    lines.append("")
    return lines


def _render_compiled_sql(sql: str | None) -> list[str]:
    if not sql:
        return []
    return ["## Compiled SQL", "", "```sql", sql, "```", ""]


# ─── Index ──────────────────────────────────────────────────────────────────


def _render_index(entities: list[Entity]) -> str:
    entities = sorted(entities, key=lambda e: (e.kind, e.schema_name, e.table_name))
    lines: list[str] = ["# Schema index", ""]
    lines.append(
        "One file per table/source. Grep or Read directly — no scripts needed."
    )
    lines.append("")
    for label, kind in (("Models", "model"), ("Sources", "source")):
        subset = [e for e in entities if e.kind == kind]
        if not subset:
            continue
        lines.append(f"## {label}")
        lines.append("")
        for e in subset:
            summary = _first_sentence(e.description) or "(no description)"
            lines.append(f"- [`{e.slug}`](./{e.filename}) — {summary}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ─── Sample-row fetching + caching ──────────────────────────────────────────


def _resolve_sample_rows(
    entity: Entity,
    executor: "Executor | None",
    sample_size: int,
    cfg: "Config",
    obfuscate_value: Callable[[str], str],
    obf_hash: str,
    *,
    refresh_all: bool,
    refresh_set: set[str],
) -> list[dict]:
    """Return rendered (post-obfuscation) rows for an entity, using cache when possible."""
    if sample_size <= 0:
        return []

    force_refresh = refresh_all or entity.slug in refresh_set
    if not force_refresh:
        cached = sample_cache.load(entity, cfg, obf_hash)
        if cached is not None:
            return cached

    if executor is None:
        # No way to fetch fresh. Fall back to whatever cache exists, even if
        # the user asked for refresh — they can't have it without a DB.
        cached = sample_cache.load(entity, cfg, obf_hash)
        if cached is not None:
            if force_refresh:
                logger.warning(
                    f"  refresh requested for {entity.slug} but no executor configured; "
                    "reusing cached samples"
                )
            return cached
        return []

    raw = _fetch_head(executor, entity, sample_size)
    if not raw:
        return []
    rendered = _eagerly_render_rows(raw, entity, obfuscate_value)
    try:
        sample_cache.save(entity, rendered, cfg, obf_hash)
    except OSError as e:
        logger.warning(f"  could not write sample cache for {entity.slug}: {e!r}")
    return rendered


def _fetch_head(executor: "Executor", entity: Entity, sample_size: int) -> list[dict]:
    if sample_size <= 0:
        return []
    try:
        headers, rows, _ = executor.run_direct(
            f"select * from {entity.schema_name}.{entity.table_name} limit {sample_size}"
        )
    except Exception as e:
        logger.warning(f"  could not fetch samples for {entity.slug}: {e!r}")
        return []
    return [
        {col: _truncate(val) for col, val in zip(headers, row)}
        for row in rows
    ]


def _eagerly_render_rows(
    head_rows: list[dict],
    entity: Entity,
    obfuscate_value: Callable[[str], str],
) -> list[dict]:
    """Convert raw fetched rows into pre-obfuscated, cell-truncated dicts."""
    pii_cols = pii_column_names(entity.columns)
    rendered: list[dict] = []
    for row in head_rows:
        rendered_row: dict = {}
        for col in row.keys():
            if col in pii_cols:
                rendered_row[col] = PII_REDACTED
            else:
                rendered_row[col] = _cell(row.get(col), obfuscate_value)
        rendered.append(rendered_row)
    return rendered


# ─── File writing + manual-notes preservation ───────────────────────────────


def _write_file(path: Path, new_body: str) -> None:
    preserved = read_manual_block(path)
    final = new_body.rstrip() + "\n\n" + preserved.lstrip()
    path.write_text(final)
    logger.info(f"  wrote {path.name}")


# ─── Inlining custom entities ───────────────────────────────────────────────


def _inline_map(custom_entities: list["TableCatalog"]) -> dict[str, "TableCatalog"]:
    """Map each `inline_into` slug to its custom entity."""
    out: dict[str, "TableCatalog"] = {}
    for ce in custom_entities:
        if ce.inline_into:
            out[ce.inline_into] = ce
    return out


# ─── Helpers ────────────────────────────────────────────────────────────────


@dataclass
class _Sentinel:
    pass


def _first_sentence(text: str) -> str:
    """Return the first sentence of a description.

    Naive cut at the first `". "`. Trade-off: sometimes cuts after "e.g." or
    similar abbreviations. Readability win of sentence boundaries usually
    outweighs occasional early cuts.
    """
    text = " ".join((text or "").split())
    if not text:
        return ""
    for end in (". ", ".\n", "."):
        idx = text.find(end)
        if idx != -1:
            return text[: idx + 1].strip()
    return text.strip()


def _truncate(value) -> str | None:
    if value is None:
        return None
    s = str(value)
    if len(s) > VALUE_MAX_LEN:
        return s[:VALUE_MAX_LEN] + "…"
    return s


def _cell(v, obfuscate_value: Callable[[str], str]) -> str:
    if v is None:
        return "NULL"
    s = str(v).replace("|", "\\|").replace("\n", " ")
    # Obfuscate before truncation so long emails/URLs aren't cut mid-pattern.
    s = obfuscate_value(s)
    if len(s) > CELL_MAX_LEN:
        s = s[:CELL_MAX_LEN] + "…"
    return s
