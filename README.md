# wlens

> Warehouse lens for AI agents.

`wlens` turns your dbt project into per-table markdown docs that any AI agent
can read, and ships a read-only SQL CLI they can run against your warehouse.
Works locally via a skill + shell command, or remotely via an MCP server.

Works with **Claude Code**, **Cursor**, **Continue**, **Codex**, **Claude
Desktop**, and any MCP-speaking client.

## Why

Most "semantic layers for AI" make you maintain a parallel schema spec (YAML
dimensions/measures) and ship a runtime that exposes a fixed menu of queries
to the LLM. That fights the way modern agents work.

wlens goes the other direction. It **exposes the schema as markdown** —
descriptions, column types, `relationships()` / `accepted_values()` tests,
parent models, sample rows, compiled SQL — one file per entity. The LLM
reads like it would any other codebase; when it needs data, it runs a
read-only SELECT.

- **Zero runtime to explore.** Markdown is committed to the repo. No
  warehouse credentials needed until the agent actually runs a query.
- **Zero parallel definitions.** Reads directly from dbt artifacts.
- **LLM-optimized format.** Per-column H3 headers for greppability, enum
  values listed inline (via `accepted_values` tests), foreign keys
  surfaced (via `relationships` tests), column-first sample rows,
  one-sentence index summaries.
- **Human-editable.** The markdown is the data. Team-authored notes
  preserved across regeneration under a marker.
- **Two deployment modes.** Solo (filesystem + CLI) or team (MCP server
  with bearer auth).

## Quickstart

```bash
# 1. Install
uv tool install wlens                    # or: pip install wlens

# 2. Initialise at the root of a dbt project
cd ~/your-dbt-project
wlens init

# 3. Compile the dbt project (needs a profile configured)
dbt compile

# 4. Generate the per-table markdown
wlens generate

# 5. Your AI agent can now explore wlens/schema/ and run queries
```

`wlens init` auto-detects `dbt_project.yml` (in cwd, then common subdirs
like `transform/`, `dbt/`) and any `*.duckdb` file, and wires the config
accordingly. For Redshift / Postgres you edit a few env var names.

After init, your project looks like:

```
your-repo/
  wlens.yml                          # config (root, like pyproject.toml)
  wlens/
    .gitignore                       # auto: ignores cache/ and share/
    schema/                          # generated per-table markdown (commit this)
      _index.md
      <schema>.<table>.md
    cache/                           # query cache (gitignored)
    share/                           # ngrok config files (gitignored)
  .claude/
    skills/
      wlens/
        SKILL.md                     # Claude Code skill (convention location)
```

## The three-move pattern

Every warehouse question follows the same shape, regardless of client:

1. **Discover** — find candidate tables.
2. **Read** — open one table's full docs.
3. **Query** — execute a read-only SELECT.

### In filesystem clients (Claude Code, Cursor, Continue, Codex)

The LLM uses its built-in `Grep` + `Read` against `wlens/schema/*.md`,
then shells out to `wlens query "SELECT …"`.

### In MCP clients (Claude Desktop, or any hosted MCP client)

The LLM calls four **tools** exposed by the wlens MCP server:

- `search_models(keyword)` — keyword-grep the docs, returns matches with snippets.
- `list_models()` — full catalog when no keyword fits.
- `read_model(name)` — full markdown for one entity.
- `execute_sql(query)` — run the SELECT.

Same pattern, different primitives.

## Architecture

```
dbt artifacts  →  wlens generate  →  wlens/schema/*.md  ─┬─► filesystem agent
     ▲                                                    │   (grep + read + wlens query)
 build-time                                               │
 (creds once)                                             └─► MCP server (wlens mcp)
                                                              → remote agents
                                                                (Claude Desktop, etc.)
                                                              → search_models / read_model
                                                                / execute_sql tools
                                                              → warehouse (read-only, on demand)
```

## `wlens query` — the CLI

A Bash-invocable SQL runner with a **hard read-only guard**. Parses your
SQL and rejects anything that isn't a single `SELECT` / `WITH … SELECT`.
Multi-statement queries are also rejected. Results come back as a
markdown table, cached under `wlens/cache/sql/` with a daily TTL
(`CURRENT_DATE`-relative queries refresh each day).

```bash
wlens query "SELECT count(*) FROM main_marts.fct_invoice"
```

Multi-line via heredoc:

```bash
wlens query <<'SQL'
select date_trunc('month', invoice_date) as month, sum(total) as revenue
  from main_marts.fct_invoice
 group by 1
 order by 1
SQL
```

## CLI reference

| Command | Purpose |
|---|---|
| `wlens init` | Write `wlens.yml` + `.claude/skills/wlens/SKILL.md` + `wlens/.gitignore`. Auto-detects dbt project and `.duckdb` files. |
| `wlens generate` | Read dbt's `target/manifest.json`, write per-table markdown into `wlens/schema/`. |
| `wlens query "SELECT ..."` | Run a read-only query. |
| `wlens tag-pii` | Scan dbt yml, add `meta: pii: true` to likely-PII columns. |
| `wlens mcp` | Start the MCP server for team / demo use. |
| `wlens mcp-proxy <url>` | Stdio↔HTTP bridge; used by Claude Desktop to reach a remote wlens. |
| `wlens clean` | Remove every file wlens installed in this repo. |

## Supported warehouses

| Warehouse  | v0.1 | v0.2 |
|------------|:----:|:----:|
| DuckDB     | ✅    |      |
| Postgres   | ✅    |      |
| Redshift   | ✅    |      |
| BigQuery   |      | ⏳    |
| Snowflake  |      | ⏳    |

## PII handling

Sample rows committed to the repo go through two redaction layers:

1. **Explicit.** Any dbt column with `meta: pii: true` renders as `<pii>`.
2. **Regex safety net.** Column names matching built-in PII patterns
   (`email`, `first_name`, `phone`, `ip_address`, etc.) are redacted
   even without the flag.

Run `wlens tag-pii` to backfill the explicit flags (`--dry-run` to preview).

## Table catalogs

Beyond dbt models and sources, wlens lets you describe per-table
catalogs of named row-instances — analytics events, feature flags,
customer attributes, subscription plans — declared in `wlens.yml` and
inlined into the right table's markdown.

The library ships only the base `TableCatalog` class and a plugin
loader; new kinds are added in your own repo (or generated by Claude
Code from the worked examples in [`examples/`](examples/)).

See [`docs/table-catalogs.md`](docs/table-catalogs.md).

## Distribution tiers

wlens ships one binary, three escalating modes:

### 1. Solo — `wlens init` + `wlens generate`

Install wlens, point it at a dbt project, use Claude Code / Cursor /
Continue / Codex with the bundled skill + `wlens query`. Works today.

### 2. Demo a teammate — `wlens mcp --dangerously-share`

```bash
wlens mcp --dangerously-share
```

Starts the wlens MCP server locally, opens an ngrok tunnel, auto-generates
a bearer token, and writes three drop-in files under `wlens/share/`:

- `wlens.mcpb` — double-click to install into Claude Desktop. mcp-remote
  and Python deps are pre-bundled; recipient needs nothing beyond Claude.
- `claude_desktop_config.json` — paste into Claude Desktop's config if
  you prefer editing by hand.
- `.mcp.json` — drop into a project root for Claude Code (native HTTP).

Dies when you Ctrl-C the process. The `--dangerously-` prefix is
deliberate: a public URL fronting a warehouse is not a production posture.

### 3. Team deployment — `wlens mcp` on your infra

```bash
export WLENS_AUTH_TOKEN=$(openssl rand -hex 32)
wlens mcp --port 8000 --allowed-host "*"
```

Team deployments are **your infra's responsibility** — Railway / Fly /
Cloud Run / VPS / k8s. wlens doesn't ship Terraform or Helm. What it
does ship: bearer auth, `/health`, `/refresh` for CI-driven doc updates,
structured logging, fail-closed startup rules.

**Before you deploy, check:**

- **Read-only warehouse role.** Create a DB user with `SELECT`-only
  grants. The in-app guard is defence in depth — the role is primary.
- **`WLENS_AUTH_TOKEN`.** Required. `wlens mcp` refuses to start on a
  non-local bind without it.
- **TLS at the platform layer.** Your platform terminates TLS; wlens
  binds plain HTTP behind it.
- **CI-driven docs refresh.** After dbt merges, have CI `POST /refresh`
  so docs stay current without redeploying.
- **Talk to your platform team.** Auth, secrets, network policy — their
  job, not wlens's.

See [`docs/mcp.md`](docs/mcp.md) for the full reference.

## Roadmap

- **v0.1** (current): dbt adapter; DuckDB + Postgres + Redshift
  executors; `wlens init / generate / query / tag-pii / clean`; MCP
  server (`wlens mcp`) with bearer auth, four tools, resources, prompts,
  `/refresh` endpoint; `--dangerously-share` with `.mcpb` bundle + drop-in
  config files; stdio↔HTTP proxy (`wlens mcp-proxy`).
- **v0.2**: sqlmesh adapter; BigQuery + Snowflake executors; Claude Code
  plugin; Claude.ai-remote OAuth support.

## License

MIT.
