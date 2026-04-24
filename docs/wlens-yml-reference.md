# `wlens.yml` reference

Every wlens command reads `wlens.yml`. It lives at your repo root
(alongside `pyproject.toml`, `dbt_project.yml`, etc.). This page
documents every key; the starter `wlens.yml` that `wlens init` writes
only shows the load-bearing ones.

## Top level

```yaml
adapter:   { ... }   # how to read your transformation framework's artifacts
executor:  { ... }   # how to connect to the warehouse
output:    { ... }   # optional — where generated markdown goes
entities:  [ ... ]   # optional — custom entity types (events, flags, etc.)
```

## `adapter`

Tells wlens which transformation framework you use and where its artifacts
live.

| Key | Default | Notes |
|---|---|---|
| `kind` | `dbt` | v0.1 supports `dbt`. `sqlmesh` is on the roadmap. |
| `project_dir` | `.` | Path to the dbt project root (where `dbt_project.yml` lives), **relative to `wlens.yml`**. Auto-detected by `wlens init`. |
| `include_prefixes` | `[]` | If non-empty, only entities whose name starts with one of these prefixes are emitted. Empty list ⇒ everything. |
| `exclude_prefixes` | `[]` | Entities whose name starts with one of these are skipped. Evaluated before `include_prefixes`. |
| `default_schema` | `prod` | **Advanced.** Fallback schema name when the dbt manifest doesn't populate `node.schema`. Rarely used — manifest resolves this in practice. Not in the starter template. |

```yaml
adapter:
  kind: dbt
  project_dir: transform
  include_prefixes: [dim_, fct_]
  exclude_prefixes: [stg_]
```

## `executor`

Warehouse connection for `wlens query` and (optionally) sample-row
fetching during `wlens generate`. Leave `kind` blank / unset to disable
queries entirely — you'll still get schema docs.

| Key | Default | Notes |
|---|---|---|
| `kind` | unset | `duckdb`, `postgres`, or `redshift`. |
| `path` | — | File-based engines only (`duckdb`). Path to the DB file, or `:memory:`. Relative paths resolve against `wlens.yml`. Auto-detected by `wlens init`. |
| `host` | — | Server engines only. Hostname or `host:port`. |
| `port` | `5439` (redshift), `5432` (postgres) | Server engines only. |
| `database` | — | Server engines only. |
| `user` | — | Server engines only. |
| `password` | — | Server engines only. |

**Environment-variable expansion.** Any string value of the form
`${SOME_NAME}` is expanded from the process environment when `wlens.yml`
is loaded. Missing variables become empty strings. This keeps secrets out
of the committed file.

### DuckDB

```yaml
executor:
  kind: duckdb
  path: warehouse.duckdb      # or ":memory:"
```

### Postgres

```yaml
executor:
  kind: postgres
  host:     ${WLENS_DB_HOST}
  port:     5432
  database: ${WLENS_DB_NAME}
  user:     ${WLENS_DB_USER}
  password: ${WLENS_DB_PASSWORD}
```

### Redshift

```yaml
executor:
  kind: redshift
  host:     ${WLENS_DB_HOST}
  port:     5439
  database: ${WLENS_DB_NAME}
  user:     ${WLENS_DB_USER}
  password: ${WLENS_DB_PASSWORD}
```

## `output`

Controls markdown generation. All keys are optional — defaults are sane
and the starter template doesn't include this block.

| Key | Default | Notes |
|---|---|---|
| `dir` | `wlens/schema` | Output directory, **relative to `wlens.yml`**. Change if you want docs somewhere else (e.g. `docs/warehouse/`). |
| `include_sample_rows` | `true` | When true, `wlens generate` fetches `sample_size` rows per entity. Needs the executor. |
| `sample_size` | `5` | Rows per entity. |

```yaml
output:
  dir: wlens/schema
  include_sample_rows: true
  sample_size: 5
```

## `entities`

Optional list of custom entity types. See
[`custom-entities.md`](custom-entities.md) for the full reference.

```yaml
entities:
  - kind: events
    source: tools/events.yml
    inline_into: public.user_track_event
```

## Minimal example (what `wlens init` writes)

```yaml
adapter:
  kind: dbt
  project_dir: .

executor:
  kind: duckdb
  path: warehouse.duckdb
```

That's the whole starter file (plus a commented-out postgres/redshift
block). Everything else uses defaults.

## No-executor example

If you only want the markdown docs and never plan to run queries, omit
`executor.kind` and turn off sample rows:

```yaml
adapter:
  kind: dbt
  project_dir: .

output:
  include_sample_rows: false
```
