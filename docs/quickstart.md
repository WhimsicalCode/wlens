# Quickstart

Get `wlens` running in an existing dbt project in under ten minutes.

## 1. Install

```bash
uv tool install wlens          # recommended
# or
pip install wlens
```

## 2. Initialise

Run `wlens init` at the root of your repo (the directory that holds
`dbt_project.yml`, or its parent if dbt lives in a subdirectory).

```bash
cd ~/your-dbt-project
wlens init
```

This writes three files:

- `wlens.yml` at the repo root
- `.claude/skills/wlens/SKILL.md` (Claude Code convention location)
- `wlens/.gitignore` (auto-ignores `wlens/cache/` and `wlens/share/`)

`wlens init` **auto-detects** two things:

- `dbt_project.yml` — searched in cwd and common subdirs (`transform/`,
  `dbt/`, `warehouse/`, `analytics/`), plus 4 levels of BFS scan. The
  first match wins; its relative path is written to `adapter.project_dir`.
- `*.duckdb` files in the repo. First match found becomes
  `executor.path`. If none found, the template default is
  `warehouse.duckdb` (a placeholder to edit).

For Redshift / Postgres, open `wlens.yml` and replace the `duckdb` block
with the commented-out `postgres` / `redshift` block shown below it.

## 3. Configure (only if not on DuckDB)

Fresh `wlens.yml` looks like:

```yaml
adapter:
  kind: dbt
  project_dir: .

executor:
  kind: duckdb
  path: warehouse.duckdb
  # For postgres / redshift, replace the two lines above with:
  #   kind: postgres           # or redshift
  #   host:     ${WLENS_DB_HOST}
  #   port:     5432           # 5439 for redshift
  #   database: ${WLENS_DB_NAME}
  #   user:     ${WLENS_DB_USER}
  #   password: ${WLENS_DB_PASSWORD}
```

Env-var substitution (`${SOME_VAR}`) happens at load time, so secrets
stay out of the committed file. Set them however you prefer (`.env`,
direnv, your platform's secret manager).

## 4. Compile dbt

wlens reads `<project_dir>/target/manifest.json`, so make sure it's fresh.

```bash
dbt compile
```

## 5. Generate markdown

```bash
wlens generate
```

You'll see one file per dbt model + source under `wlens/schema/`:

```
wlens/schema/
  _index.md
  main_marts.dim_user.md
  main_marts.fct_order.md
  ...
```

If your executor is configured and reachable, each file includes five
sample rows. Offline / no credentials? Pass `--skip-samples`.

## 6. Ask your AI agent something

Open Claude Code, Cursor, Continue, or Codex and ask a question. The
bundled `SKILL.md` teaches the agent:

1. Grep `wlens/schema/` for a keyword from the question.
2. Read the matching `<schema>.<table>.md`.
3. Run `wlens query "SELECT ..."` to fetch real data.

That's it. No SDK, no MCP server, no schema spec required.

## Running queries

```bash
wlens query "SELECT count(*) FROM main_marts.dim_user"
```

Multi-line via heredoc (the agent uses this form):

```bash
wlens query <<'SQL'
select date_trunc('month', created_at) as month, count(*) as signups
  from main_marts.dim_user
 where created_at >= current_date - interval '6 months'
 group by 1
 order by 1
SQL
```

Results are cached under `wlens/cache/sql/` for 24 hours. The cache key
includes today's date so `CURRENT_DATE`-relative queries refresh daily.
Bypass with `--no-cache`.

## Limiting scope

By default `wlens generate` emits every dbt model and source. For large
projects you'll usually want to restrict to mart-layer tables — add these
lines to `wlens.yml`:

```yaml
adapter:
  kind: dbt
  project_dir: .
  include_prefixes: [dim_, fct_]   # only these prefixes
  exclude_prefixes: [stg_]         # or skip these
```

## Sharing wlens with a teammate

Once solo-mode works, expose the same functionality over MCP so teammates
can use it from Claude Desktop without installing wlens locally:

```bash
wlens mcp --dangerously-share
```

This opens an ngrok tunnel, auto-generates a bearer token, and writes
three drop-in files under `wlens/share/`:

- `wlens.mcpb` — double-click into Claude Desktop (self-contained bundle).
- `claude_desktop_config.json` — paste into Claude Desktop's config JSON.
- `.mcp.json` — drop at any project root for Claude Code (native HTTP).

Full reference: [`mcp.md`](mcp.md).

## PII handling

Sample rows under `wlens/schema/` go through two redaction layers:

1. Any column flagged `meta: pii: true` in your dbt yml renders as `<pii>`.
2. A built-in regex safety net catches common PII column names (`email`,
   `first_name`, `phone`, `ip_address`, …) even without the flag.

Run `wlens tag-pii` to backfill the explicit flags into your yml files.
Preview first with `--dry-run`.

## Cleaning up

Remove every file wlens has installed or generated in this repo:

```bash
wlens clean              # prompts before deleting
wlens clean --dry-run    # preview
wlens clean --yes        # skip the prompt
```

This nukes `wlens.yml`, the whole `wlens/` directory (schema, cache,
share, .gitignore), and `.claude/skills/wlens/`. Unrelated content
under `.claude/skills/` (other skills) is preserved.
