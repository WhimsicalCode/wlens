# `wlens mcp` — Model Context Protocol server

`wlens mcp` starts an MCP server that exposes wlens over HTTP. Teammates
connect from Claude Desktop (or any MCP-speaking client) to query the
warehouse without each installing wlens locally.

Two modes, one binary:

- **Production**: `wlens mcp` — you host it on your infra.
- **Demo**: `wlens mcp --dangerously-share` — temporary ngrok tunnel +
  drop-in config files for Claude Desktop and Claude Code. Not for
  daily use.

## What the server exposes

### Tools (the primary interface — what Claude Desktop surfaces to the agent)

| Tool | Purpose |
|---|---|
| `search_models(keyword, max_results=20)` | Case-insensitive substring search across every model's markdown. Returns `[{uri, name, description, match_count, snippets}]`. Use this first. |
| `list_models()` | Full catalog of every documented entity as `[{uri, name, description}]`. Use when no specific keyword fits. |
| `read_model(name)` | Full markdown docs for one entity. Call after picking a name from search / list. |
| `execute_sql(query)` | Read-only SQL. Returns `{sql, columns, rows, row_count, cache_hit, elapsed_ms}`. Mutations rejected; warehouse role should also be read-only. |

### Resources (for clients that autonomously invoke them — Cursor, Continue)

| URI | Content |
|---|---|
| `wlens://models` | Same JSON catalog as `list_models()`. |
| `wlens://models/{name}` | Same markdown as `read_model(name)`. |

Claude Desktop today **only surfaces tools** to the agent — resources
are user-attachable via `@mention`, not agent-callable. The duplicated
tool surface exists specifically so Claude Desktop works. If your MCP
client handles resources well, it works too — same data either way.

### Prompt

| Name | Purpose |
|---|---|
| `wlens_skill` | Returns the bundled SKILL.md (the "three-move pattern" doc). Available via `/` slash menus in clients that surface prompts. |

### HTTP endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | `{"status": "ok", "executor": "...", "auth": "..."}`. No auth. |
| `POST /refresh` | Re-runs `wlens generate` in-process. Bearer auth required. CI-driven. |
| `POST /mcp` | MCP streamable-http endpoint. Bearer auth required. |

## Production mode

```bash
export WLENS_AUTH_TOKEN=$(openssl rand -hex 32)
export WLENS_DB_HOST=...           # per your wlens.yml executor block
export WLENS_DB_USER=...
export WLENS_DB_PASSWORD=...

wlens mcp --port 8000 --allowed-host "*"
```

### Flags

| Flag | Default | Notes |
|---|---|---|
| `--host` | `0.0.0.0` | Bind address. `0.0.0.0` works behind reverse proxies. |
| `--port` | `8000` | HTTP port. |
| `--transport` | `streamable-http` | `sse` is the older fallback. |
| `--no-auth` | off | Only allowed on a localhost bind. Fails closed on public binds. |
| `--allowed-host HOST` | localhost only | Repeatable. DNS-rebinding allowlist. Use `*` when behind a proxy that normalises Host. |

### Fail-closed rules

`wlens mcp` refuses to start if:

- You bind to a non-local host (anything other than `127.0.0.1` / `localhost` / `::1`) **AND**
- `WLENS_AUTH_TOKEN` is not set **AND**
- `--no-auth` is not passed.

This prevents accidentally publishing an unauthenticated SQL endpoint.

### Team-deployment checklist

- **Read-only warehouse role.** Create a DB user with `SELECT`-only grants.
  The in-app SQL guard is defence in depth — the warehouse role is primary.
- **`WLENS_AUTH_TOKEN`.** Required. Use `openssl rand -hex 32`. Store in
  your platform's secret manager; share with teammates via 1Password.
- **TLS at the platform layer.** Railway, Fly, Cloud Run, Render, etc.
  terminate TLS for you. wlens binds plain HTTP behind that.
- **CI-driven docs refresh.** After dbt merges, have CI call:
  ```bash
  curl -X POST -H "Authorization: Bearer $WLENS_AUTH_TOKEN" \
       https://wlens.example.com/refresh
  ```
- **Talk to your platform team.** Auth integration, secrets, network
  policy — these are your infra's problem, not wlens's.

wlens intentionally does not ship Terraform / Helm / Dockerfiles.

## Demo mode: `--dangerously-share`

```bash
wlens mcp --dangerously-share
```

What happens:

1. A random 32-character bearer token is generated in-process.
2. wlens opens a pyngrok HTTPS tunnel to `127.0.0.1:<port>`.
3. Three drop-in files are written under `wlens/share/`:
   - `wlens.mcpb` — self-contained Claude Desktop extension bundle
     (pre-bundled with the Python `mcp` SDK + `httpx`; recipient needs
     nothing but Claude Desktop).
   - `claude_desktop_config.json` — full Claude Desktop config JSON
     that uses your **locally-installed** `wlens` as an mcp-proxy
     (works if you have wlens on your machine).
   - `.mcp.json` — Claude Code native HTTP config (no proxy needed).
4. A banner prints the URL + token + paths.
5. When you Ctrl-C the process: tunnel closes, `wlens/share/` cleared,
   server exits.

The `--dangerously-` prefix is intentional. Anyone with the URL +
bearer can reach your warehouse for the lifetime of the process.
Don't share publicly; rotate if it leaks.

### ngrok auth

First-time use works anonymously (aggressive rate limits). For regular
demoing, sign up at [dashboard.ngrok.com](https://dashboard.ngrok.com),
grab the authtoken, and `export NGROK_AUTHTOKEN=...`. pyngrok picks it
up automatically.

## Connecting clients

### Claude Desktop — drag-and-drop `.mcpb`

Double-click `wlens/share/wlens.mcpb`. Claude Desktop extracts the bundle
and registers it as an extension — mcp-remote equivalent (Python) is
vendored inside, URL and bearer token are baked into the manifest.

### Claude Desktop — manual config

Paste the contents of `wlens/share/claude_desktop_config.json` into
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
or merge the `wlens` entry into an existing file with other MCPs.
Restart Claude Desktop.

The file looks like:

```json
{
  "mcpServers": {
    "wlens": {
      "command": "/Users/you/.local/bin/wlens",
      "args": ["mcp-proxy", "https://abc.ngrok-free.app/mcp"],
      "env": { "WLENS_AUTH_TOKEN": "..." }
    }
  }
}
```

Uses your installed `wlens` binary as a stdio↔HTTP bridge (the
`mcp-proxy` subcommand). No Node, no npx — pure Python.

### Claude Code — drop-in `.mcp.json`

Move `wlens/share/.mcp.json` into any project root. Claude Code reads
it automatically on next launch. File shape (Claude Code speaks HTTP MCP
natively):

```json
{
  "mcpServers": {
    "wlens": {
      "type": "http",
      "url": "https://abc.ngrok-free.app/mcp",
      "headers": { "Authorization": "Bearer ..." }
    }
  }
}
```

Or via CLI:

```bash
claude mcp add --transport http --scope project wlens \
  https://abc.ngrok-free.app/mcp \
  --header "Authorization: Bearer ..."
```

### Other MCP clients

Any MCP-speaking client (Cursor, Continue, etc.) can reach the same URL
as long as it supports bearer auth headers. For clients that speak
stdio only, run `wlens mcp-proxy <url>` locally as the bridge — it's
the same shim the Claude Desktop config uses.

## `wlens mcp-proxy` — the stdio bridge

```bash
WLENS_AUTH_TOKEN=<token> wlens mcp-proxy <remote_url>
```

Reads JSON-RPC on stdin, forwards to the remote wlens HTTP MCP server
with the bearer token from env, streams responses to stdout. Pure
Python, uses the `mcp` SDK. Normally spawned by Claude Desktop from the
drop-in config — you rarely call it directly.

## Logging

Every tool call and `/refresh` is logged to stdout in `key=value` format.
Set `WLENS_LOG_FORMAT=json` for JSON lines (log-aggregator friendly).

Tokens are never logged raw — only a 12-character SHA-256 fingerprint.
SQL is logged by hash + row count + elapsed time; raw query is suppressed
by default. If you need full-SQL audit logging for compliance, wrap
`wlens mcp` behind a proxy that records request bodies.
