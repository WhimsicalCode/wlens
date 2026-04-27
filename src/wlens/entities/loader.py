r"""Per-table catalogs of named row-instances.

A `TableCatalog` describes the kinds of rows that can appear in a dbt table —
analytics events, feature flags, customer attributes, marketing channels.
Each catalog loads a YAML file of `{name: spec}` entries and renders a
markdown section that gets inlined into the target table's docs.

Two ways to add a new kind:

1. **No code.** Declare it in `wlens.yml` with a `kind`, `title`, `source`
   and `table`. The default render produces `## Title` → `### \`name\``
   per entry with description + attributes.

2. **One Python file.** Subclass `TableCatalog`, override `intro()` and/or
   `entry_extras()`, and point `wlens.yml` at the file via `plugins: [...]`.
   The subclass auto-registers on import.

Two worked examples ship alongside (read-only, copy into your repo to
use): `examples/plans.py` (mid-complexity) and `examples/events.py`
(fully-featured, including a `from_config` override).
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from ..config import Config, EntityConfig

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type["TableCatalog"]] = {}


@dataclass
class TableCatalog:
    """A catalog of named row-instances belonging to a table.

    Instantiable directly for catalogs that fit the standard render shape.
    Subclass to add an `intro()` line or per-entry extras like an example
    SQL block.
    """

    kind: str = ""
    title: str = ""
    table: str = ""
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __init_subclass__(cls, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        kind = getattr(cls, "kind", "")
        if kind:
            _REGISTRY[kind] = cls

    @property
    def inline_into(self) -> str:
        return self.table

    def render(self) -> list[str]:
        if not self.entries:
            return []
        title = self.title or self.kind.replace("_", " ").title()
        lines: list[str] = [f"## {title}", ""]
        intro = self.intro()
        if intro:
            lines.extend(intro)
            lines.append("")
        for name in sorted(self.entries):
            spec = self.entries[name] or {}
            lines.append(f"### `{name}`")
            desc = (spec.get("description") or "").strip()
            if desc:
                lines.append("")
                lines.append(desc)
            for key, value in spec.items():
                if key == "description":
                    continue
                lines.extend(_render_spec_field(key, value))
            extras = self.entry_extras(name, spec)
            if extras:
                lines.extend(extras)
        return lines

    def intro(self) -> list[str]:
        return []

    def entry_extras(self, name: str, spec: dict[str, Any]) -> list[str]:
        return []

    @classmethod
    def from_config(cls, entry: "EntityConfig", source_path: Path) -> "TableCatalog":
        entries = yaml.safe_load(source_path.read_text()) or {}
        x = entry.extra or {}
        return cls(
            kind=entry.kind,
            title=x.get("title", ""),
            table=entry.table or "",
            entries=entries,
        )


def load_entities(config: "Config") -> list[TableCatalog]:
    """Import any `plugins:` files, then build one `TableCatalog` per `entities:` entry."""
    _load_plugins(config.plugins, config.repo_root)
    out: list[TableCatalog] = []
    for entry in config.entities:
        catalog = _build_entity(entry, config.repo_root)
        if catalog is not None:
            out.append(catalog)
    return out


def _build_entity(entry: "EntityConfig", repo_root: Path) -> TableCatalog | None:
    source_path = (repo_root / entry.source).resolve()
    if not source_path.exists():
        logger.warning(f"custom entity source not found: {source_path} — skipping")
        return None
    cls = _REGISTRY.get(entry.kind, TableCatalog)
    return cls.from_config(entry, source_path)


def _render_spec_field(key: str, value: Any) -> list[str]:
    """Render one non-description spec field generically.

    - dict → ``**Label:**`` heading then ``- key — value`` bullets (sorted).
    - list → ``**Label:**`` heading then one bullet per item.
    - string / int / float / bool → ``**Label:** value`` inline.
    - Anything empty (None, "", {}, []) renders nothing.
    """
    label = key.replace("_", " ").capitalize()
    if isinstance(value, dict):
        if not value:
            return []
        lines = ["", f"**{label}:**"]
        for k, v in sorted(value.items()):
            v_str = v.strip() if isinstance(v, str) else "" if v is None else str(v)
            lines.append(f"- `{k}` — {v_str}" if v_str else f"- `{k}`")
        return lines
    if isinstance(value, list):
        if not value:
            return []
        return ["", f"**{label}:**", *[f"- {item}" for item in value]]
    if isinstance(value, bool):
        return ["", f"**{label}:** {'yes' if value else 'no'}"]
    if isinstance(value, (int, float)):
        return ["", f"**{label}:** {value}"]
    if isinstance(value, str):
        v = value.strip()
        return ["", f"**{label}:** {v}"] if v else []
    return []


def _load_plugins(paths: list[str], repo_root: Path) -> None:
    for raw_path in paths:
        path = (repo_root / raw_path).resolve()
        if not path.exists():
            logger.warning(f"plugin file not found: {path} — skipping")
            continue
        try:
            module_name = f"wlens_plugin_{path.stem}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                logger.warning(f"could not load plugin: {path}")
                continue
            module = importlib.util.module_from_spec(spec)
            # Register before exec_module so @dataclass introspection (which
            # looks up cls.__module__ in sys.modules) works for plugins that
            # use `from __future__ import annotations`.
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as e:
            logger.warning(f"plugin {path} raised on import: {e!r} — skipping")
