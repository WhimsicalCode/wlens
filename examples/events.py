"""Worked example — the most advanced `TableCatalog` subclass.

This file is **not** auto-registered. It sits next to `plans.py` as the
larger of two worked examples: all three hooks overridden (`intro`,
`entry_extras`, `from_config`).

The domain is an analytics events catalog — typically a single wide
table where each row is a `user_track_event` / `clo_event` record, with
the event name in one column and a JSON blob of attributes in another.
This is the catalog every analytics engineer wishes lived next to the
schema: "what does `widget-created` mean? what attributes does it carry?
how do I query for it?"

To use this in your own project, copy `EventsCatalog` into a Python
file in your repo (e.g. `wlens_catalogs.py`) and reference that file
from `plugins:` in `wlens.yml`. Nothing in this file is imported at
runtime — it's here to read, not to depend on.

What this example shows (a step beyond `plans.py`):

- **`intro()`** — a one-line lead-in explaining how to access JSON
  attributes in the SQL dialect (`data."attr"::text`).
- **`entry_extras()`** — a runnable SQL block per event, grep-friendly
  so an LLM searching for an event name lands on a query with the right
  casts and column order.
- **`from_config()`** — reads four `wlens.yml` config keys that don't
  exist on the base class: `column`, `data_column`, `core_columns`,
  `head_limit`. Override this hook whenever your kind needs config keys
  beyond the standard `kind`, `source`, `table`, `title`.

Sample `wlens.yml`:

    plugins:
      - ./wlens_catalogs.py
    entities:
      - kind: events
        source: tools/events.yml
        table: public.events
        column: event                  # the column that holds the event name
        data_column: data              # the JSON column holding attributes
        core_columns: [id, created, event]
        head_limit: 5

Sample `events.yml`:

    widget-created:
      description: A user creates a new widget.
      attributes:
        color: Chosen widget colour.
        source: Where the create dialog was opened from.

Compare with `plans.py` for a smaller example that overrides only
`intro` and `entry_extras` — useful when your kind doesn't need
custom config keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from wlens.entities import TableCatalog

if TYPE_CHECKING:
    from wlens.config import EntityConfig


@dataclass
class EventsCatalog(TableCatalog):
    kind: str = "events"
    title: str = "Events"

    column: str = "event"
    data_column: str = "data"
    core_columns: list[str] = field(default_factory=lambda: ["id", "created", "event"])
    head_limit: int = 5

    def intro(self) -> list[str]:
        return [
            f"The `{self.column}` column below is a filter on the event name. "
            f"Attributes live in the `{self.data_column}` column and are accessed as "
            f'`{self.data_column}."attr"::text`.'
        ]

    def entry_extras(self, name: str, spec: dict[str, Any]) -> list[str]:
        if not self.table:
            return [""]
        attrs = spec.get("attributes") or {}
        attr_cols = [f'{self.data_column}."{a}"::text as "{a}"' for a in sorted(attrs.keys())]
        query_cols = list(self.core_columns) + attr_cols
        if not query_cols:
            return [""]
        sql = (
            "select\n    " + ",\n    ".join(query_cols) + "\n"
            f"from {self.table}\n"
            f"where {self.column} = '{name}'\n"
            f"limit {self.head_limit}"
        )
        return ["", "**Example query:**", "", "```sql", sql, "```", ""]

    @classmethod
    def from_config(cls, entry: "EntityConfig", source_path: Path) -> "EventsCatalog":
        entries = yaml.safe_load(source_path.read_text()) or {}
        x = entry.extra or {}
        return cls(
            entries=entries,
            table=entry.table or "",
            column=x.get("column", "event"),
            data_column=x.get("data_column", "data"),
            core_columns=list(x.get("core_columns") or ["id", "created", "event"]),
            head_limit=int(x.get("head_limit", 5)),
        )
