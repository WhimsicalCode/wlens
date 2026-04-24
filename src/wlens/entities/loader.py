"""Custom entity types declared in `wlens.yml`.

A custom entity loads data from a YAML file and renders a markdown section
inlined into a target dbt source (or model) file.

Built-in kinds:
  - events: catalog of analytics events. Each event renders as
    `### <event-name>` with a description, attribute list, and example SQL.

Adding a new kind: register a CustomEntity subclass in `_REGISTRY`. The
subclass's `render()` returns the list of markdown lines to inject.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from ..config import Config, EntityConfig

logger = logging.getLogger(__name__)


class CustomEntity(ABC):
    """Base class for custom entity types."""

    kind: str = ""
    inline_into: str | None = None

    @abstractmethod
    def render(self) -> list[str]:
        """Return the markdown lines to inject into the target entity's file."""


@dataclass
class EventsCatalog(CustomEntity):
    """A YAML catalog of analytics events.

    Expected YAML shape:

        event-name:
          description: What this event means.
          attributes:
            some-attr: Description of the attribute.
            another:   Description.
    """

    kind: str = "events"
    events: dict[str, dict[str, Any]] = None  # type: ignore[assignment]
    source_table: str = ""                    # qualified name, e.g. "public.user_track_event"
    event_column: str = "event"
    data_column: str = "data"
    core_columns: list[str] = None            # type: ignore[assignment]
    head_limit: int = 5
    inline_into: str | None = None

    def render(self) -> list[str]:
        if not self.events:
            return []
        lines: list[str] = ["## Events", ""]
        lines.append(
            f"The `{self.event_column}` column below is a filter on the event name. "
            f"Attributes live in the `{self.data_column}` column and are accessed as "
            f'`{self.data_column}."attr"::text`.'
        )
        lines.append("")
        for event_name in sorted(self.events):
            ev = self.events[event_name] or {}
            lines.append(f"### `{event_name}`")
            desc = (ev.get("description") or "").strip()
            if desc:
                lines.append("")
                lines.append(desc)
            attrs = ev.get("attributes") or {}
            if attrs:
                lines.append("")
                lines.append("**Attributes:**")
                for attr_name, attr_desc in sorted(attrs.items()):
                    attr_desc = (attr_desc or "").strip()
                    if attr_desc:
                        lines.append(f"- `{attr_name}` — {attr_desc}")
                    else:
                        lines.append(f"- `{attr_name}`")
            lines.extend(self._example_query(event_name, attrs))
        return lines

    def _example_query(self, event_name: str, attrs: dict) -> list[str]:
        if not self.source_table:
            return [""]
        core = self.core_columns or []
        attr_cols = [f'{self.data_column}."{a}"::text as "{a}"' for a in sorted(attrs.keys())]
        query_cols = list(core) + attr_cols
        if not query_cols:
            return [""]
        sql = (
            "select\n    " + ",\n    ".join(query_cols) + "\n"
            f"from {self.source_table}\n"
            f"where {self.event_column} = '{event_name}'\n"
            f"limit {self.head_limit}"
        )
        return ["", "**Example query:**", "", "```sql", sql, "```", ""]


def load_entities(config: "Config") -> list[CustomEntity]:
    """Instantiate one CustomEntity per `entities:` entry in wlens.yml."""
    out: list[CustomEntity] = []
    for entry in config.entities:
        entity = _build_entity(entry, config.repo_root)
        if entity is not None:
            out.append(entity)
    return out


def _build_entity(entry: "EntityConfig", repo_root: Path) -> CustomEntity | None:
    source_path = (repo_root / entry.source).resolve()
    if not source_path.exists():
        logger.warning(f"custom entity source not found: {source_path} — skipping")
        return None

    if entry.kind == "events":
        events = yaml.safe_load(source_path.read_text()) or {}
        extra = entry.extra or {}
        return EventsCatalog(
            events=events,
            inline_into=entry.inline_into,
            source_table=extra.get("source_table", entry.inline_into or ""),
            event_column=extra.get("event_column", "event"),
            data_column=extra.get("data_column", "data"),
            core_columns=list(extra.get("core_columns") or ["id", "created", "event"]),
            head_limit=int(extra.get("head_limit", 5)),
        )

    logger.warning(f"unknown custom entity kind: {entry.kind!r} — skipping")
    return None
