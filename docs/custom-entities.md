# Custom entity types

Most schema docs come straight from dbt. But many projects have *other*
kinds of information that the agent also needs: an analytics event
catalog, a feature-flag list, a customer-attribute glossary. wlens lets
you declare these as **custom entities** in `wlens.yml`; they're rendered
into the markdown alongside regular dbt models.

## The built-in: `events`

An `events` entity takes a YAML file describing analytics events and
inlines every event as an `### <event-name>` section in a target dbt
source's markdown. This is the reference pattern the library ships with.

### Config

```yaml
entities:
  - kind: events
    source: tools/events.yml               # path relative to wlens.yml
    inline_into: public.events             # which dbt source gets the events appended

    # Optional fine-tuning (defaults shown):
    source_table: public.events            # what appears in the example query
    event_column: event                    # the column that holds the event name
    data_column: data                      # the SUPER / JSON column holding attributes
    core_columns: [id, created, event]     # columns always in the example query
    head_limit: 5
```

### Expected `events.yml` shape

```yaml
widget-created:
  description: A user creates a new widget.
  attributes:
    color: Chosen widget colour.
    source: Where the create dialog was opened from.

widget-deleted:
  description: A user deletes a widget.
  attributes:
    reason: Why the user said they deleted it.
```

Each top-level key is an event name. Each event has a `description` and
an `attributes` map (attribute name → human description).

### Rendered output

Inside `public.events.md`, a section appears between the Columns
and Sample rows:

````markdown
## Events

The `event` column below is a filter on the event name. Attributes live
in the `data` column and are accessed as `data."attr"::text`.

### `widget-created`

A user creates a new widget.

**Attributes:**
- `color` — Chosen widget colour.
- `source` — Where the create dialog was opened from.

**Example query:**

```sql
select
    id,
    created,
    event,
    data."color"::text as "color",
    data."source"::text as "source"
from public.events
where event = 'widget-created'
limit 5
```

### `widget-deleted`
...
````

The example query is grep-friendly — an LLM searching for an event name
lands directly on a runnable query with the right casts and column order.

## Adding a new custom-entity kind

The built-in `events` kind is defined in
[`src/wlens/entities/loader.py`](../src/wlens/entities/loader.py). To
add another kind, subclass `CustomEntity`:

```python
# wlens/entities/feature_flags.py
from dataclasses import dataclass
from .loader import CustomEntity

@dataclass
class FeatureFlagsCatalog(CustomEntity):
    kind: str = "feature_flags"
    flags: dict = None
    inline_into: str | None = None

    def render(self) -> list[str]:
        lines = ["## Feature flags", ""]
        for name, spec in sorted(self.flags.items()):
            lines.append(f"### `{name}`")
            if spec.get("description"):
                lines.append("")
                lines.append(spec["description"])
            lines.append("")
        return lines
```

Then wire it into `_build_entity()` in `loader.py` so `wlens.yml` can
reference `kind: feature_flags`.

In v0.2 this will be a plugin surface so you won't have to fork the
library; for v0.1, a small PR or a monkey-patch in your own repo's
startup code does the job.
