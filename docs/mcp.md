# `wlens mcp` — Model Context Protocol server

`wlens mcp` starts an MCP server that exposes wlens over HTTP. Teammates
connect from any MCP-speaking client to query the warehouse without each
installing wlens locally.

Two modes, one binary:

- **Production**: `wlens mcp` — you host it on your infra.
- **Demo**: `wlens mcp --dangerously-share` — temporary ngrok tunnel +
  drop-in config files for every major MCP client. Not for daily use.

## What the server exposes

### Tools (the primary interface — what most clients surface to the agent)

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
3. Drop-in files are written under `wlens/share/` (see next section).
4. A banner prints the URL + token + paths + per-client install hints.
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

## What `wlens mcp --dangerously-share` writes

Five drop-in files land under `wlens/share/`. wlens never writes outside
the project repo — references to `~/.gemini/...`, `~/.codex/...`,
`~/Library/Application Support/Claude/...`, etc. are paste targets,
not files wlens touches.

| File | Format | For |
|---|---|---|
| `.mcp.json` | JSON, `mcpServers` with `url` + `type: "http"` | Claude Code (drop into project root). **Universal donor**: same shape works for Cursor, GitHub Copilot in VS Code, Cline, Zed at their own paths. |
| `claude_desktop_config.json` | JSON, `mcpServers` with `command` + `args` | Claude Desktop (stdio via `wlens mcp-proxy` — Claude Desktop doesn't speak HTTP MCP). |
| `wlens.mcpb` | Zip (Python bundle, MCPB v0.3) | Claude Desktop standalone install. Vendors `mcp` + `httpx` + `anyio` so the recipient needs nothing beyond Claude Desktop. Skipped if `uv` and `pip` are both unavailable at build time. |
| `gemini_settings.json` | JSON, `mcpServers` with `httpUrl` | Gemini CLI / Antigravity (different field name from Claude Code). |
| `codex_config.toml` | TOML, `[mcp_servers.<name>]` | Codex CLI (TOML, not JSON). |

## Per-client setup

Each client picks one of the files above (or pastes from one). The
demo banner prints the exact CLI commands when applicable.

### Claude Code

Move `wlens/share/.mcp.json` into a project root and Claude Code picks
it up on next launch. Or run:

```bash
claude mcp add --transport http --scope project wlens \
  https://abc.ngrok-free.app/mcp \
  --header "Authorization: Bearer ..."
```

File shape (Claude Code speaks HTTP MCP natively):

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

### Claude Desktop

**Preferred:** double-click `wlens/share/wlens.mcpb`. Claude Desktop
extracts the bundle and registers it as an extension — Python proxy is
vendored inside, URL and bearer token are baked into the manifest.

**Manual fallback:** paste `wlens/share/claude_desktop_config.json` into
`~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or merge the `wlens` entry into an existing file with other
MCPs. Restart Claude Desktop.

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

### Gemini CLI

Run:

```bash
gemini mcp add --transport http wlens \
  https://abc.ngrok-free.app/mcp \
  --header "Authorization: Bearer ..."
```

Or merge `wlens/share/gemini_settings.json` into `~/.gemini/settings.json`
(global) or `.gemini/settings.json` (project). Field name is `httpUrl`,
not `url`:

```json
{
  "mcpServers": {
    "wlens": {
      "httpUrl": "https://abc.ngrok-free.app/mcp",
      "headers": { "Authorization": "Bearer ..." }
    }
  }
}
```

### Antigravity (Google's IDE)

Settings → MCP Servers → Manage MCP Servers → View raw config → paste
the contents of `wlens/share/gemini_settings.json`. Same Gemini-shape
JSON.

### Codex CLI (OpenAI)

Merge the `[mcp_servers.wlens]` block from `wlens/share/codex_config.toml`
into `~/.codex/config.toml`:

```toml
[mcp_servers.wlens]
url = "https://abc.ngrok-free.app/mcp"
http_headers = { Authorization = "Bearer ..." }
```

Codex doesn't have a per-project MCP config (everything lives in
`~/.codex/config.toml`).

### ChatGPT (web / desktop, Apps)

ChatGPT installs MCP servers via UI, not a config file:

1. Settings → Connectors → Developer Mode (Plus/Pro/Team/Enterprise/Edu only).
2. **Add custom MCP** (or **Add a custom Apps connector**).
3. URL: `https://abc.ngrok-free.app/mcp`
4. Authentication: Bearer token, paste the token from the share banner.

Custom MCP Apps live behind Developer Mode; free ChatGPT does not
support them.

### Cursor

Copy `wlens/share/.mcp.json` to one of:

- `.cursor/mcp.json` (project-scoped — only this project)
- `~/.cursor/mcp.json` (global — all your Cursor workspaces)

Same shape as the Claude Code file works unchanged.

### GitHub Copilot in VS Code

Copy `wlens/share/.mcp.json` to `.vscode/mcp.json` in your project
(or merge into your VS Code user `settings.json` under
`chat.mcp.servers`). The Claude Code shape is accepted unchanged.

### Windsurf

Paste the contents of `wlens/share/.mcp.json` into
`~/.codeium/windsurf/mcp_config.json`, merging the `wlens` entry into
the `mcpServers` map.

### Cline (VS Code extension)

Open Cline's MCP settings (in the extension panel) → Edit MCP Settings
JSON → paste the `mcpServers.wlens` entry from `wlens/share/.mcp.json`.

### Zed

Open `~/.config/zed/settings.json` and paste the `mcpServers.wlens`
entry under the `assistant.mcp_servers` map (Zed's exact key may evolve
— check Zed's docs if it doesn't pick up).

### Continue

Continue uses YAML (`.continue/config.yaml`), not JSON. Hand-translate
from `.mcp.json`:

```yaml
mcpServers:
  - name: wlens
    type: http
    url: https://abc.ngrok-free.app/mcp
    headers:
      Authorization: "Bearer ..."
```

### Pi.dev

Pi has no native MCP. Two options:

- **MCP via adapter:** install
  [`pi-mcp-adapter`](https://github.com/nicobailon/pi-mcp-adapter)
  and reuse the `wlens/share/.mcp.json` shape — the adapter reads
  standard MCP files automatically.
- **Filesystem mode (no adapter):** Pi has shell + read tools, so it
  works with the bundled SKILL.md. Pi scans **user-level**
  `~/.agents/skills/` only. To make wlens discoverable across every
  project you open Pi in, run a one-time symlink (`wlens` itself
  never writes outside the repo):

  ```bash
  ln -s "$(pwd)/.agents/skills/wlens" ~/.agents/skills/wlens
  ```

## Filesystem mode (no MCP)

Any shell-capable agent works without an MCP server: the agent uses
its built-in `Grep` + `Read` against `wlens/schema/*.md`, then shells
out to `wlens query "SELECT …"`.

`wlens init` plants the SKILL.md into two project-level discovery
paths so any major agent finds it:

| Path | Picked up by |
|---|---|
| `.claude/skills/wlens/SKILL.md` | Claude Code (which doesn't scan `.agents/skills/`) |
| `.agents/skills/wlens/SKILL.md` | Gemini CLI, Antigravity, Codex CLI (walks up from cwd to repo root), Cursor, GitHub Copilot in VS Code, anything else following the [Agent Skills open standard](https://agentskills.io) |

The Agent Skills format went open-standard in December 2025, so the
list of tools that read `.agents/skills/` is growing. Both files are
byte-identical — wlens writes one template to two destinations. (Note:
Gemini CLI scans `.agents/skills/` directly; it does **not** scan
`.gemini/skills/`, contrary to some older third-party docs.)

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
