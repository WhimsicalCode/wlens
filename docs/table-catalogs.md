# Table catalogs

Most schema docs come straight from dbt. But many projects have other
kinds of information that the agent also needs: an analytics event
catalog, a feature-flag list, a customer-attribute glossary, an incident
log. wlens lets you describe these as **table catalogs** — per-table
catalogs of named row-instances with rich metadata — and inlines them
into the right table's markdown.

A `TableCatalog` is just:

- a target `table` (the dbt source/model the catalog describes)
- a `title` (the markdown section header)
- an `entries` map: `{name: {description?, ...any other keys}}`
- shared rendering: `## Title` → `### \`name\`` per entry → description → all other keys (auto-rendered, see below)

## `entities` vs `plugins` in `wlens.yml`

Two orthogonal lists. They're often used together but neither requires
the other:

- **`entities:`** — *declarations*. Each entry says: *"build a catalog
  of kind `X`, source it from `Y.yml`, inline it into table `Z`."*
  Data, not code. Think of it as the **menu** of catalogs your project
  wants rendered.
- **`plugins:`** — *Python file paths*. Each file declares one or more
  `TableCatalog` subclasses that auto-register on import and teach
  wlens *how a kind renders*. Code, not data. Think of it as the
  **recipe templates**.

```yaml
plugins:                          # CODE: how kinds render
  - ./wlens_catalogs.py

entities:                         # DATA: which catalogs to build
  - kind: feature_flags           # uses generic auto-render (no plugin needed)
    title: Feature flags
    source: flags.yml
    table: public.feature_flags
  - kind: events                  # uses a class registered by the plugin above
    source: tools/events.yml
    table: public.events
    column: event
```

You can have plugins with no entities (loaded but unused — harmless).
You can have entities with no plugins (every kind falls back to the
generic auto-renderer — Option 1 below). One file teaches wlens a kind;
the other tells wlens to use one.

## What auto-renders

Inside an entry's spec, every key besides `description` is rendered
based on its type. You don't have to subclass to get a clean layout for
new keys.

| Spec value type             | Rendered as                                           |
| --------------------------- | ----------------------------------------------------- |
| `str`                       | `**Label:** value` on its own line.                   |
| `int` / `float` / `bool`    | `**Label:** value` (booleans render as `yes` / `no`). |
| `dict`                      | `**Label:**` + bullet list of `` `key` — value ``.    |
| `list`                      | `**Label:**` + one bullet per item.                   |
| `None` / `""` / `{}` / `[]` | nothing (skipped).                                    |

`Label` is the key with underscores replaced by spaces and the first
letter capitalised (`feature_owner` → "Feature owner").

So an entry like:

```yaml
beta-dashboard:
  description: Enables the redesigned dashboard.
  owner: dashboard-team
  rollout: 25%
  attributes:
    cohort: Which user cohort the flag is gated on.
  related_tables:
    - public.users
    - public.dashboard_views
```

renders as:

```markdown
### `beta-dashboard`

Enables the redesigned dashboard.

**Owner:** dashboard-team

**Rollout:** 25%

**Attributes:**
- `cohort` — Which user cohort the flag is gated on.

**Related tables:**
- public.users
- public.dashboard_views
```

Reach for Option 2 (subclass) only when you need something the
auto-render can't do: per-entry SQL, markdown links, conditional
rendering, cross-entry views.

## Option 1 — zero code

For catalogs that fit the standard render shape, declare the kind
entirely in `wlens.yml`. No Python required.

```yaml
# wlens.yml
entities:
  - kind: feature_flags
    title: Feature flags
    source: flags.yml
    table: public.feature_flags
```

```yaml
# flags.yml
beta-dashboard:
  description: Enables the redesigned dashboard.
  attributes:
    owner: dashboard-team
    rollout: 25%

experimental-export:
  description: Lets the user export their workspace as a zip.
  attributes:
    owner: platform-team
```

This renders into `public.feature_flags.md` between the Columns section
and Sample rows:

```markdown
## Feature flags

### `beta-dashboard`

Enables the redesigned dashboard.

**Attributes:**
- `owner` — dashboard-team
- `rollout` — 25%

### `experimental-export`

Lets the user export their workspace as a zip.

**Attributes:**
- `owner` — platform-team
```

## Option 2 — one Python file

When you want a catalog-specific intro line or per-entry extras that
the auto-render can't produce — a runnable SQL query, a markdown link
to a runbook, conditional formatting — drop a small Python file next to
`wlens.yml` and subclass `TableCatalog`.

```python
# wlens_catalogs.py
from dataclasses import dataclass
from wlens.entities import TableCatalog


@dataclass
class IncidentsCatalog(TableCatalog):
    kind: str = "incidents"
    title: str = "Incidents"

    def entry_extras(self, name, spec):
        runbook = spec.get("runbook")
        return ["", f"[Runbook]({runbook})", ""] if runbook else []
```

Reference the file from `wlens.yml`:

```yaml
# wlens.yml
plugins:
  - ./wlens_catalogs.py

entities:
  - kind: incidents
    source: incidents.yml
    table: public.incidents
```

The class auto-registers when the plugin file is imported.

### Render order

Knowing where each piece of output lands makes it obvious which hook
to override:

```text
## {self.title}                            ← from `title` attribute
{self.intro()}                             ← optional lead-in
                                           (one blank line follows)
### `{name}`                               ← per entry, sorted by name
{spec["description"]}                      ← if present (special-cased)
**Key:** value                             ← auto-rendered, every other
**Other key:**                             ← spec key, in YAML order
- bullets...
{self.entry_extras(name, spec)}            ← optional per-entry extras
```

That's the entire render contract. Anything you can't get from the
title, the auto-rendered spec keys, or the two hook outputs requires
overriding `render()` directly — but you almost never need to.

### Hook reference

| Hook                                              | When                  | Return                                                            |
| ------------------------------------------------- | --------------------- | ----------------------------------------------------------------- |
| `intro(self) -> list[str]`                        | once per catalog      | lines below the `## title` header                                 |
| `entry_extras(self, name, spec) -> list[str]`     | once per entry        | lines after the auto-rendered keys                                |
| `from_config(cls, entry, source_path)`            | once at load          | a populated `TableCatalog` instance                               |

`from_config` is the loader hook — override it when your kind has extra
`wlens.yml` config keys (anything beyond `kind`, `source`, `table`,
`title`). The arguments:

- `entry` — an `EntityConfig` dataclass. The recognised keys live on
  `entry.kind`, `entry.source`, `entry.table`. Everything else from
  the YAML entry is in `entry.extra: dict[str, Any]`.
- `source_path` — the resolved absolute `Path` of the YAML file you
  named in `source:`. Read it with `yaml.safe_load`.

See [`examples/events.py`](../examples/events.py) for a worked
override that pulls `column`, `data_column`, `core_columns`, and
`head_limit` from `entry.extra`.

### Minimum viable plugin

A working subclass is three lines of body — the auto-render handles
the rest:

```python
# wlens_catalogs.py
from dataclasses import dataclass
from wlens.entities import TableCatalog


@dataclass
class MyCatalog(TableCatalog):
    kind: str = "my_kind"
    title: str = "My catalog"
```

Add `intro()` only when you need a lead-in or a cross-entry view (a
comparison table, a count summary). Add `entry_extras()` only for
per-entry rendering the auto-render can't produce (markdown links,
SQL blocks, conditional callouts). Add `from_config()` only when you
introduce new config keys in `wlens.yml`.

### Worked examples to copy

Two worked examples ship as read-only references — copy whichever is
closer to what you need:

- [`examples/plans.py`](../examples/plans.py) — a `plans` dimension
  catalog (subscription tiers with price, seats, features, limits).
  Mid-complexity: overrides `intro` (a cross-entry comparison table)
  and `entry_extras` (conditional "Deprecated" callout + pricing-page
  link). No `from_config`. **Start here.**
- [`examples/events.py`](../examples/events.py) — an analytics-events
  catalog with grep-friendly SQL examples per event. Overrides
  `intro`, `entry_extras` *and* `from_config`, because it accepts extra
  `wlens.yml` config keys (`column`, `data_column`, `core_columns`,
  `head_limit`). The full-power example.

Neither is registered out of the box. Drop a copy into your repo (or
ask Claude Code to: *"copy `examples/events.py` into my repo as
`wlens_catalogs.py` and wire it up in `wlens.yml`"*) and reference it
from `plugins:`.

## Pitfalls

A handful of things that have bitten plugin authors and tend to trip
up LLM agents writing plugins for the first time:

- **`@dataclass` is required.** wlens reads `kind` and `title` defaults
  via dataclass machinery. A plain class won't auto-register.
- **`kind` and `title` must be dataclass fields.** Use
  `kind: str = "my_kind"` (with the type annotation), not
  `kind = "my_kind"` (no annotation, dataclass ignores it).
- **`from __future__ import annotations` works** alongside `@dataclass`
  in plugin files — wlens registers each loaded plugin in
  `sys.modules` so the dataclass introspection works on lazy
  annotations. (Don't be tempted to skip the future-annotations import
  to "fix" anything; it's already supported.)
- **Two plugins claiming the same `kind`** — last one loaded wins,
  silently. Plugins load in the order they appear under `plugins:`.
- **YAML data files are loaded with `yaml.safe_load`.** No Python tags
  or object construction — stick to scalars, lists, and dicts.
- **The `description` key is special-cased.** It renders as a
  paragraph above the auto-rendered keys, not as `**Description:** …`.
  Every other key goes through the auto-render rules.

## Source-of-truth pointers

If you (or an agent) need to confirm anything in this doc against the
actual implementation, the relevant code is small:

- [`src/wlens/entities/loader.py`](../src/wlens/entities/loader.py) —
  `TableCatalog`, the auto-render, `_REGISTRY`, plugin loading.
- [`src/wlens/render/markdown.py`](../src/wlens/render/markdown.py) —
  where a catalog's `render()` output is inlined into a table's
  markdown.
- [`src/wlens/config.py`](../src/wlens/config.py) — `EntityConfig`
  shape and how `wlens.yml` keys are routed into `extra`.

