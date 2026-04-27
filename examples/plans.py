"""Worked example — a richer `TableCatalog` subclass.

This file is **not** auto-registered. It sits next to `events.py` as a
mid-complexity worked example: two hooks overridden (`intro` and
`entry_extras`), no `from_config`.

The domain is the kind of table every SaaS analytics warehouse already
has: a `plans` dimension — one row per subscription tier (`free`,
`pro`, `team`, `enterprise`…). Joining a fact table back to it is one
of the first things any analyst does; documenting what each tier
actually *means* (price, seats, included features, hard limits) is the
kind of context that lives in slide decks and PRDs and never makes it
into the warehouse on its own. A `TableCatalog` puts it next to the
columns.

To use this in your own project, copy `PlansCatalog` into a Python file
in your repo (e.g. `wlens_catalogs.py`) and reference that file from
`plugins:` in `wlens.yml`. Nothing in this file is imported at runtime —
it's here to read, not to depend on.

What this example shows (two things Option 1's auto-render cannot do):

- **Cross-entry view** — `intro()` builds a comparison table across all
  tiers (price + seats side-by-side). Auto-render iterates one entry at
  a time and can't see the whole catalog, so it can't produce this.
- **Conditional formatting** — `entry_extras()` emits a "Deprecated"
  callout only when `deprecated: true` is set on an entry, plus a
  pricing-page link. Auto-render is type-driven and has no way to
  branch on values.

Sample `wlens.yml`:

    plugins:
      - ./wlens_catalogs.py
    entities:
      - kind: plans
        source: plans.yml
        table: public.plans

Sample `plans.yml` (every type below auto-renders — see the auto-render
table in `docs/table-catalogs.md`):

    pro:
      description: Per-seat tier for working teams.
      price: $12 / seat / month        # string scalar  → **Price:** $12 / seat / month
      seats_included: 5                # int scalar     → **Seats included:** 5
      features:                        # list           → **Features:** + bullets
        - Unlimited boards
        - 30-day version history
        - SSO
      limits:                          # dict           → **Limits:** + bullets
        boards_per_workspace: unlimited
        export_quota_gb: 50
      pricing_page: https://example.com/pricing/pro
      deprecated: false

The comparison table from `intro()` and the conditional callout from
`entry_extras()` are produced by the methods below, not by auto-render.

Compare with `events.py` for the next step up: it overrides `intro()`,
`entry_extras()`, *and* `from_config()` because the `events` kind
accepts extra `wlens.yml` config keys (`column`, `data_column`,
`core_columns`, `head_limit`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wlens.entities import TableCatalog


@dataclass
class PlansCatalog(TableCatalog):
    kind: str = "plans"
    title: str = "Plans"

    def intro(self) -> list[str]:
        """A comparison table at the top of the section — cross-entry view."""
        if not self.entries:
            return []
        header = ("Tier", "Price", "Seats")
        rows: list[tuple[str, ...]] = [
            (
                f"`{name}`",
                str((self.entries[name] or {}).get("price", "—")),
                str((self.entries[name] or {}).get("seats_included", "—")),
            )
            for name in sorted(self.entries)
        ]
        sep = ("---",) * len(header)
        format_row = lambda r: "| " + " | ".join(r) + " |"  # noqa: E731
        return [
            "At a glance:",
            "",
            format_row(header),
            format_row(sep),
            *(format_row(r) for r in rows),
        ]

    def entry_extras(self, name: str, spec: dict[str, Any]) -> list[str]:
        """Per-entry extras — conditional callout + a pricing-page link."""
        out: list[str] = []
        if spec.get("deprecated"):
            out.extend(["", "> **Deprecated** — new signups are not allowed."])
        url = spec.get("pricing_page")
        if url:
            out.extend(["", f"[See `{name}` pricing]({url})"])
        if out:
            out.append("")
        return out
