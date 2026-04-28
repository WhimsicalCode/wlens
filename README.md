# wlens

wlens turns your dbt project into per-table markdown that any AI agent
can read. It also ships a read-only SQL command line so the agent can
query the warehouse, and an MCP server so your teammates can ask their
own questions.

Works with any MCP client, including Claude Code, Cursor, Gemini CLI,
Codex, Claude Desktop, ChatGPT, Continue, and Pi.

## Quickstart

```bash
# 1. Install
uv tool install wlens                    # or: pip install wlens

# 2. Initialise from your repo root
cd ~/your-repo
wlens init

# 3. Compile the dbt project (needs a profile configured)
dbt compile

# 4. Generate the per-table markdown
wlens generate

# 5. Your AI agent can now explore wlens/schema/ and run queries
```

Your dbt project can sit at the repo root or in a subfolder like
`dbt/`, `transform/`, or `analytics/`. `wlens init` scans the current
directory and a few levels of subfolders for `dbt_project.yml` and
picks the shallowest match. It also picks up any `*.duckdb` file and
wires the config for you. For Redshift or Postgres, you edit a few env
var names.

After init, your project looks like this:

```
your-repo/
├── wlens.yml                       # config (root, like pyproject.toml)
├── wlens/
│   ├── .gitignore                  # auto: ignores cache/ and share/
│   ├── schema/                     # generated per-table markdown (commit this)
│   │   ├── _index.md
│   │   └── <schema>.<table>.md
│   ├── cache/                      # query cache (gitignored)
│   └── share/                      # MCP config drop-ins for teammates (gitignored)
├── .claude/skills/wlens/SKILL.md   # Claude Code
└── .agents/skills/wlens/SKILL.md   # Gemini CLI / Codex / Cursor / VS Code Copilot (open standard)
```

Two skill files with the same content. Claude Code reads
`.claude/skills/`. Every other major agent reads `.agents/skills/`,
which is the open standard at [agentskills.io](https://agentskills.io),
including Gemini CLI, Codex, Cursor, and GitHub Copilot in VS Code.

## How it works

wlens reads your dbt artifacts and writes one markdown file per table.
The agent reads those files when it needs to find a table or understand
a column. When it needs data, it runs a SELECT through the wlens command
line.

The markdown is committed to your repo. The agent can grep and read it
without warehouse credentials. Credentials are only needed when a query
runs.

### Why markdown

The agent already knows how to read code. Markdown lets it use the same
tools, grep and read, on your warehouse schema. Each column gets its own
header. Foreign keys and accepted values come from your dbt tests.
Notes you write by hand are kept when wlens regenerates the file.

## The three-move pattern

Every warehouse question follows the same shape, regardless of client.

1. **Discover.** Find candidate tables.
2. **Read.** Open one table's full docs.
3. **Query.** Run a read-only SELECT.

In filesystem clients like Claude Code, Gemini CLI, Cursor, Continue,
Codex, and Pi, the agent uses its built-in grep and read on
`wlens/schema/*.md`, then shells out to `wlens query "SELECT ..."`.

In MCP clients like Claude Desktop, Antigravity, ChatGPT, or any hosted
MCP client, the agent calls four tools exposed by the wlens MCP server.

- `search_models(keyword)`. Keyword-grep the docs and return matches with snippets.
- `list_models()`. Full catalog when no keyword fits.
- `read_model(name)`. Full markdown for one entity.
- `execute_sql(query)`. Run the SELECT.

Same pattern, different primitives.

## Extensibility

wlens covers dbt models and sources by default. It also lets you
describe table catalogs, which are per-table catalogs of named
row-instances like analytics events, feature flags, customer attributes,
or subscription plans.

The library teaches no domains. It ships the base `TableCatalog` class
and a plugin loader. New kinds live in your own repo. The worked
examples in [`examples/`](examples/) are sized to be cloned and adapted
by an AI agent.

See [`docs/table-catalogs.md`](docs/table-catalogs.md).

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

## `wlens query`

A SQL runner with a hard read-only guard. wlens parses your SQL and
rejects anything that is not a single `SELECT` or `WITH ... SELECT`.
Multi-statement queries are also rejected. Results come back as a
markdown table. Each query is cached under `wlens/cache/sql/` with a
daily TTL, so `CURRENT_DATE`-relative queries refresh each day.

```bash
wlens query "SELECT count(*) FROM main_marts.fct_invoice"
```

For multi-line queries, use a heredoc.

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
| `wlens init` | Write `wlens.yml`, the skill files for Claude Code and the open standard, and `wlens/.gitignore`. Auto-detects dbt project and `.duckdb` files. |
| `wlens generate` | Read dbt's `target/manifest.json` and write per-table markdown into `wlens/schema/`. |
| `wlens query "SELECT ..."` | Run a read-only query. |
| `wlens tag-pii` | Scan dbt yml and add `meta: pii: true` to columns that look like PII. |
| `wlens mcp` | Start the MCP server for team or demo use. |
| `wlens mcp-proxy <url>` | Stdio to HTTP bridge. Used by Claude Desktop to reach a remote wlens. |
| `wlens mcp-clients --url ... [--token ...]` | Generate per-client MCP config files for a deployed wlens server. |
| `wlens clean` | Remove every file wlens installed in this repo. |

## Supported warehouses

| Warehouse  | Status |
|------------|:------:|
| DuckDB     | v0.1 ✅ |
| Postgres   | v0.1 ✅ |
| Redshift   | v0.1 ✅ |
| BigQuery   | v0.2 ⏳ |
| Snowflake  | v0.2 ⏳ |

## PII handling

Sample rows committed to the repo go through two redaction layers.

1. **Explicit.** Any dbt column with `meta: pii: true` renders as `<pii>`.
2. **Regex safety net.** Column names that match built-in PII patterns
   like `email`, `first_name`, `phone`, or `ip_address` are redacted
   even without the flag.

Run `wlens tag-pii` to backfill the explicit flags. Use `--dry-run` to preview.

## Distribution tiers

wlens ships one binary with three modes.

### 1. Solo. `wlens init` plus `wlens generate`

Install wlens, point it at a dbt project, and use it from any agent
with the bundled skill and `wlens query`. Works today.

### 2. Demo to a teammate. `wlens mcp --dangerously-share`

```bash
wlens mcp --dangerously-share
```

Starts the wlens MCP server locally, opens an ngrok tunnel, generates a
bearer token, and writes drop-in config files into `wlens/share/`.

- `wlens.mcpb`. Double-click to install into Claude Desktop. Python
  deps are pre-bundled, so the recipient needs nothing beyond Claude
  Desktop.
- `claude_desktop_config.json`. Claude Desktop config for manual paste.
- `.mcp.json`. Claude Code config (drop into a project root). The same
  shape works at `.cursor/mcp.json`, `.vscode/mcp.json`, and elsewhere.
- `gemini_settings.json`. Gemini CLI and Antigravity. The field name is
  `httpUrl`.
- `codex_config.toml`. Codex CLI. TOML, merge into
  `~/.codex/config.toml`.

The banner prints the install command for each client. ChatGPT, Cursor,
VS Code Copilot, Windsurf, Continue, Cline, Zed, and Pi all reuse one of
these shapes. See [`docs/mcp.md`](docs/mcp.md) for the paste targets.

The server runs on your laptop and dies when you Ctrl-C it. The public
URL is an ngrok tunnel, so it changes every run and goes away when the
process does. The bearer token is shared, written into five config
files, and printed to your terminal. There is no revocation short of
killing the server, and no audit trail per teammate. The
`--dangerously-` prefix is deliberate. This is fine for showing a
teammate over coffee, not for sustained access to a warehouse.

### 3. Team deployment. `wlens mcp` on your infra

Team deployments run on your own infra, like Railway, Fly, Cloud Run, a
VPS, or k8s. wlens does not ship Terraform or Helm. What it does ship is
bearer auth, a `/health` endpoint, a `/refresh` endpoint for CI-driven
doc updates, structured logging, and fail-closed startup rules.

Before you deploy, check the following.

- **Read-only warehouse role.** Create a database user with
  `SELECT`-only grants. The in-app guard is defence in depth. The role
  is primary.
- **`WLENS_AUTH_TOKEN`.** Required. `wlens mcp` refuses to start on a
  non-local bind without it.
- **TLS at the platform layer.** Your platform terminates TLS. wlens
  binds plain HTTP behind it.
- **CI-driven docs refresh.** After dbt merges, have CI `POST /refresh`
  so docs stay current without redeploying.
- **Talk to your platform team.** Auth, secrets, and network policy are
  their job, not wlens's.

Once those are in place, run the server on whatever runs your
container:

```bash
export WLENS_AUTH_TOKEN=$(openssl rand -hex 32)
wlens mcp --port 8000 --allowed-host "*"
```

Then generate the per-client config files for teammates, pointing at
your server URL:

```bash
export WLENS_AUTH_TOKEN=<the same value your server reads>
wlens mcp-clients --url https://wlens.team.com/mcp
```

This writes `.mcp.json`, `claude_desktop_config.json`,
`gemini_settings.json`, `codex_config.toml`, and `wlens.mcpb` into
`wlens/share/`. Each file embeds the bearer token, so distribute them
through whatever channel you'd use for any other secret.

See [`docs/mcp.md`](docs/mcp.md) for the full reference.

## Roadmap

- **v0.1** (current). dbt adapter. DuckDB, Postgres, and Redshift
  executors. `wlens init / generate / query / tag-pii / clean`. MCP
  server (`wlens mcp`) with bearer auth, four tools, resources, prompts,
  and a `/refresh` endpoint. `--dangerously-share` with the `.mcpb`
  bundle and drop-in config files. Stdio to HTTP proxy
  (`wlens mcp-proxy`).

## License

MIT.
