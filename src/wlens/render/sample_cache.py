"""On-disk cache for sample rows rendered into per-table markdown.

Goal: skip the warehouse roundtrip on subsequent `wlens generate` runs when
nothing schema-relevant has changed, and pin the rendered rows so they don't
churn the git diff between runs.

Cache files live under `<cache_dir>/<schema>.<table>.json` (default
`wlens/.cache/samples/`). Each file stores the **post-obfuscation,
post-truncation** cell strings — exactly what gets rendered into the .md —
so the cache itself adds zero new PII surface vs. the markdown that's
already committed.

Cache invalidates when any of these change for an entity:

- Set of column names
- Any column's data_type
- Any column's PII flag (added/removed `pii: true` in the dbt schema)
- The effective obfuscation ruleset (defaults + `output.obfuscate` in wlens.yml)
- The cache format `version`
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .obfuscate import compile_rules
from .pii import pii_column_names

if TYPE_CHECKING:
    from ..adapters.base import Entity
    from ..config import Config

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
_DOC = (
    "wlens-managed sample cache. Schema docs live in wlens/schema/. "
    "Do not read this file — read the .md instead."
)


def cache_path(entity: "Entity", cfg: "Config") -> Path:
    return cfg.cache_dir / f"{entity.slug}.json"


def obfuscation_hash(cfg: "Config") -> str:
    """sha256 over the effective scrubber config (defaults + user extras)."""
    rules = compile_rules(cfg.output.obfuscate)
    payload = [(r.name, r.pattern.pattern, r.replacement) for r in rules]
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()


def load(entity: "Entity", cfg: "Config", obf_hash: str) -> list[dict] | None:
    """Return cached rendered rows if the cache is valid; otherwise None."""
    path = cache_path(entity, cfg)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"  sample cache for {entity.slug} unreadable ({e}); refetching")
        return None
    if data.get("version") != CACHE_VERSION:
        return None
    if data.get("obfuscation_hash") != obf_hash:
        return None
    if _columns_from_cache(data.get("columns") or []) != _columns_view(entity):
        return None
    rows = data.get("rows")
    if not isinstance(rows, list):
        return None
    return rows


def save(
    entity: "Entity",
    rendered_rows: list[dict],
    cfg: "Config",
    obf_hash: str,
) -> None:
    """Write rendered (post-obfuscation) rows to the cache."""
    path = cache_path(entity, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_doc": _DOC,
        "version": CACHE_VERSION,
        "schema": entity.schema_name,
        "table": entity.table_name,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "obfuscation_hash": obf_hash,
        "columns": _columns_for_write(entity),
        "rows": rendered_rows,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _columns_for_write(entity: "Entity") -> list[dict]:
    pii = pii_column_names(entity.columns)
    return [
        {"name": c.name, "data_type": c.data_type, "pii": c.name in pii}
        for c in entity.columns.values()
    ]


def _columns_view(entity: "Entity") -> dict:
    pii = pii_column_names(entity.columns)
    return {
        c.name: {"data_type": c.data_type, "pii": c.name in pii}
        for c in entity.columns.values()
    }


def _columns_from_cache(cached: list[dict]) -> dict:
    return {
        c["name"]: {"data_type": c.get("data_type"), "pii": bool(c.get("pii", False))}
        for c in cached
        if isinstance(c, dict) and "name" in c
    }
