"""PII column detection and yml tagging.

Single source of truth for what counts as PII by column name:

  1. Explicit `meta.pii: true` on the dbt yml column (authoritative).
  2. Regex safety net — catches unflagged columns by name.

The regex set is conservative — designed to catch clear cases without
false-positives on booleans / aggregate counts. Projects that want a
different ruleset can monkey-patch `PII_PATTERNS` or subclass; a proper
config surface is on the v0.2 roadmap.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterator

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

logger = logging.getLogger(__name__)

PII_REDACTED = "<pii>"

# Prefixes that indicate a column is NOT the raw PII value, but a boolean,
# count, or setting that happens to mention a PII-adjacent concept.
SKIP_PREFIXES = re.compile(r"^(is_|has_|count_|num_|total_|send_|users_)")

# Column-name patterns that strongly suggest PII.
PII_PATTERNS = re.compile(
    r"^email$"
    r"|_email$"
    r"|^email_address$"
    r"|^(first|last|middle|full)_name$"
    r"|^phone(_number)?$|_phone$"
    r"|^(twitter|linkedin|facebook|instagram|github)_(handle|bio|username|profile|followers|url)$"
    r"|^ip(_addr(ess)?)?$|_ip_addr(ess)?$"
    r"|^bio$"
    r"|^job_title$"
    r"|^password(_hash)?$|^secret(_key)?$|_secret$"
    r"|^mfa_"
)


def column_name_looks_like_pii(name: str) -> bool:
    """Regex-based heuristic for detecting PII columns from their name alone."""
    if not name:
        return False
    if SKIP_PREFIXES.match(name):
        return False
    return bool(PII_PATTERNS.search(name))


def column_is_pii(name: str, meta: dict | None) -> bool:
    """Authoritative PII check combining `meta.pii: true` and the regex safety net."""
    if meta and meta.get("pii") is True:
        return True
    return column_name_looks_like_pii(name)


def pii_column_names(columns: dict[str, Any]) -> set[str]:
    """Return the set of columns to redact in sample rendering.

    `columns` is a dict of Column-like objects. The caller may pass raw dicts
    (legacy path) or the Column dataclass used by the adapters — we peek at
    `.meta` / `["meta"]` defensively.
    """
    out: set[str] = set()
    for name, col in (columns or {}).items():
        meta: dict | None
        if hasattr(col, "meta"):
            meta = getattr(col, "meta") or {}
        elif isinstance(col, dict):
            meta = col.get("meta") or {}
        else:
            meta = None
        if column_is_pii(name, meta):
            out.add(name)
    return out


# ─── YAML tagger (writes `meta: pii: true` into dbt yml) ────────────────────


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 80
    y.indent(mapping=2, sequence=2, offset=0)
    return y


def _ensure_pii_flag(col: CommentedMap) -> bool:
    meta = col.get("meta")
    if isinstance(meta, dict):
        if meta.get("pii") is True:
            return False
        meta["pii"] = True
        return True
    col["meta"] = CommentedMap({"pii": True})
    return True


def _iter_columns(doc: dict) -> Iterator[tuple[str, CommentedMap]]:
    for model in (doc.get("models") or []):
        entity = model.get("name")
        for col in (model.get("columns") or []):
            yield entity, col
    for source in (doc.get("sources") or []):
        for table in (source.get("tables") or []):
            entity = table.get("name")
            for col in (table.get("columns") or []):
                yield entity, col


def scan_and_tag(models_dir: Path, *, dry_run: bool, repo_root: Path | None = None) -> None:
    """Walk `models_dir/**/*.yml` and add `meta: pii: true` to PII-looking columns."""
    yaml = _yaml()
    tagged: list[tuple[Path, str, str]] = []
    already: list[tuple[Path, str, str]] = []

    def _rel(path: Path) -> str:
        try:
            return str(path.relative_to(repo_root)) if repo_root else str(path)
        except ValueError:
            return str(path)

    for yml_path in sorted(models_dir.rglob("*.yml")):
        try:
            with yml_path.open("r") as f:
                doc = yaml.load(f)
        except Exception as e:
            logger.warning(f"  skip (parse error): {_rel(yml_path)} — {e!r}")
            continue
        if not isinstance(doc, dict):
            continue

        file_changed = False
        for entity, col in _iter_columns(doc):
            col_name = col.get("name")
            if not col_name or not column_name_looks_like_pii(col_name):
                continue
            meta = col.get("meta")
            if isinstance(meta, dict) and meta.get("pii") is True:
                already.append((yml_path, entity, col_name))
                continue
            if _ensure_pii_flag(col):
                tagged.append((yml_path, entity, col_name))
                file_changed = True

        if file_changed and not dry_run:
            with yml_path.open("w") as f:
                yaml.dump(doc, f)

    prefix = "[DRY RUN] " if dry_run else ""
    if dry_run:
        print(f"\n{prefix}Would tag {len(tagged)} columns.")
    else:
        print(f"\nTagged {len(tagged)} columns with `meta.pii: true`.")
    for path, entity, col_name in tagged:
        print(f"  + {_rel(path)} :: {entity}.{col_name}")

    if already:
        print(f"\nAlready flagged ({len(already)}) — no change:")
        for path, entity, col_name in already:
            print(f"  = {_rel(path)} :: {entity}.{col_name}")
