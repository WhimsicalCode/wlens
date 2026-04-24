---
name: wlens
description: Query a data warehouse documented by wlens. Use when the user asks about analytics, metrics, usage, revenue, adoption, or anything answerable by SQL against dbt-managed tables.
---

# wlens — warehouse exploration skill

## Current date

Before writing any date-relative SQL ("last month", "since Q2", "YTD"),
check today's actual date — your training cutoff is almost certainly wrong.
Use `date +%Y-%m-%d` on the host, or `CURRENT_DATE` inside the query.

## The three moves

Every warehouse question follows the same shape:

1. **Discover** — find which tables could answer the question.
2. **Read** — open the full docs for the most promising one.
3. **Query** — write a read-only SELECT and execute it.

Do not skip steps 1 and 2. Column names, enum values, foreign keys, and
join hints all live in the docs. Guessing is the most common failure mode.

## How to discover + read

Pick the mode that matches the tools you have. In either mode, the
underlying docs and rules are identical — only the access primitives differ.

### Filesystem mode (Claude Code, Cursor, Continue, Codex, any shell-capable agent)

Every dbt model and source has a per-table markdown file under
`wlens/schema/`. `wlens/schema/_index.md` lists them all alphabetically with
one-sentence summaries.

```
grep(pattern="<one-specific-keyword>", path="wlens/schema/", files_only=True)
read_file("wlens/schema/<whichever>.md")
```

Use **one** keyword — the noun of the thing being measured ("cancellation",
"signup", "mrr"). Don't regex-alternate (`a|b|c`); if the first grep
returns too many files, narrow the keyword, don't scatter more greps.

### MCP mode (Claude Desktop, or any MCP client connected to a hosted wlens)

Four **tools** do the same job as grep + read:

- **`search_models(keyword)`** — keyword-grep across every table's docs.
  Use this first when the question has a clear noun ("cancellation",
  "invoice", "mrr", "signup"). Returns only matching tables with
  snippets. Best for big projects (many models).
- **`list_models()`** — returns the full catalog. Use this when no
  single keyword fits, or you want a bird's-eye view. For small/medium
  projects this is fine to call every time.
- **`read_model(name)`** — returns the full markdown for one entity,
  e.g. `read_model("main_marts.fct_invoice")`. Call this after picking
  a name from search/list.
- **`execute_sql(query)`** — runs the final SQL (see below).

Some MCP clients (Cursor, Continue) also expose the same content as
**resources** (`wlens://models` catalog + `wlens://models/<name>` per
entity). If your client supports `resources/read`, use those instead of
the tools — same data, slightly lighter protocol hop. Claude Desktop
currently only surfaces tools to the agent, so the tools are the path
there.

Start with `search_models` when you have a keyword; fall back to
`list_models` when you don't. Either way, pick the model(s) whose names
or descriptions most obviously match the question, read those, then move
on to the query step.

## What's in each markdown file

Every entity file follows the same layout. Rely on it:

- **Description** — prose about what the table represents and at what grain.
- **Columns** — one `### <column_name>` H3 section per column. Each has:
  - `Type: <sql_type>`
  - a rich description (often inlines enum values, gotchas, join hints)
  - `Tests: ...` when present. **Read these carefully** — this is where
    foreign keys (`relationships(to=ref('X'), field=Y)`) and enums
    (`accepted_values(values=[a, b, c])`) are declared. They remove most
    of the guesswork about joins and valid values.
- **Parents** — upstream tables/sources this entity depends on.
- **Sample rows** — up to 5 real rows, column-first (one bullet per
  column, pipe-separated values). Shows typical shapes, null patterns,
  actual enum distributions.
- **Compiled SQL** — rendered Jinja for the model (models only). Read
  this when you need to understand exactly what the model computes.

## Running SQL

All queries are **read-only** — wlens hard-rejects anything that isn't a
single `SELECT` or `WITH … SELECT`. Don't try DDL/DML; it will fail with
a clear error. Results cache for 24h (cache key includes today's date so
`CURRENT_DATE`-relative queries refresh daily).

### Filesystem mode

```bash
wlens query "SELECT count(*) FROM <schema>.<table>"
```

Multi-line via heredoc:

```bash
wlens query <<'SQL'
select date_trunc('month', <date_col>) as month,
       count(*) as n
  from <schema>.<table>
 where <date_col> >= current_date - interval '6 months'
 group by 1
 order by 1
SQL
```

### MCP mode

Call the `execute_sql` tool with `{"query": "SELECT ..."}`. The tool
returns a structured payload:

```
{ sql, columns, rows, row_count, cache_hit, elapsed_ms }
```

## Rules for staying efficient

- **One discover step, then read.** If the first list/grep is too noisy,
  narrow the keyword — don't issue more discovery calls.
- **Prefer mart tables** (curated, aggregated facts + dimensions) over
  raw sources unless the question specifically needs row-level event detail.
  The `_index.md` / `wlens://models` listing usually separates them by
  naming convention.
- **Stop when you have a confident answer.** Don't re-check with
  alternative columns — it wastes turns and can mislead.
- **Never guess.** Column names, enum values, join keys — all in the
  docs. If you don't see it, read another file.

## Writing the SQL

- Readable table aliases (full names or descriptive short names, NOT
  single letters or initialisms).
- Newlines, max ~120 chars per line.
- Don't duplicate filter logic across CTEs — factor into a base CTE.
- Let the column tests guide joins: a column whose tests include
  `relationships(to=ref('X'), field=Y)` is a ready-made foreign key; use
  that relationship rather than inventing one.
- Respect accepted-values tests: if a column has
  `accepted_values(values=[...])`, those are the only legal filter values.
- If the project has a convention for excluding internal/employee/test
  accounts (look in the relevant user or workspace dimension's
  description), follow it.

For any table, column, enum value, relationship, or test type not covered
by knowledge you already have, **discover first** — do not guess.
