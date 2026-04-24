"""FastMCP server — exposes wlens tools + resources + prompt over MCP.

The server is constructed around a `wlens.config.Config` instance:

- A single warehouse executor is kept open for the life of the process.
- `.claude/schema/` is read on demand for resource lookups.
- The bundled `templates/SKILL.md` is used for the `wlens_skill` prompt.

Tool: `execute_sql(query)` returns a **structured** result so future UI
adapters (deeplinks, charts) have a stable shape to bind against:

    {
      "sql": str,
      "columns": [{"name": str, "type": str | None}],
      "rows": [{col: val, ...}],
      "row_count": int,
      "cache_hit": bool,
      "elapsed_ms": int,
    }
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ResourceError

from ..config import Config
from ..executor import build_executor
from ..executor.base import Executor, ReadOnlyViolation
from . import logs

SERVER_NAME = "wlens"
RESOURCE_LIST_URI = "wlens://models"
RESOURCE_DETAIL_URI = "wlens://models/{name}"


@dataclass
class WlensMCPServer:
    """Holds the FastMCP instance + the long-lived executor.

    Call `.close()` during shutdown to drain the warehouse connection.
    """

    mcp: FastMCP
    executor: Executor | None
    config: Config

    def close(self) -> None:
        if self.executor is not None:
            self.executor.close()


def create_server(config: Config, *, allowed_hosts: list[str] | None = None) -> WlensMCPServer:
    """Build and return a configured FastMCP server."""
    try:
        executor: Executor | None = build_executor(config)
    except Exception as e:  # noqa: BLE001 — we want to surface any startup error clearly
        logs.event("executor_init_failed", error=repr(e))
        executor = None

    transport_security = _transport_security(allowed_hosts)
    mcp = FastMCP(
        name=SERVER_NAME,
        instructions=_skill_text(),
        host=config.executor.host or "0.0.0.0",
        port=8000,
        json_response=True,
        stateless_http=True,
        transport_security=transport_security,
    )

    _register_tools(mcp, config, executor)
    _register_resources(mcp, config)
    _register_prompts(mcp)

    return WlensMCPServer(mcp=mcp, executor=executor, config=config)


# ─── Tool ──────────────────────────────────────────────────────────────────


def _register_tools(mcp: FastMCP, config: Config, executor: Executor | None) -> None:
    @mcp.tool(
        name="list_models",
        description=(
            "List every dbt model / source wlens has generated docs for. Use this "
            "when you want the full catalog (works well for small/medium projects). "
            "For big projects, prefer `search_models(keyword)` which only returns "
            "matches. Each entry: {uri, name, description}. Pass `name` to "
            "`read_model` to fetch the full markdown docs for one of them."
        ),
    )
    def list_models() -> list[dict[str, str]]:
        return _list_entries(config)

    @mcp.tool(
        name="search_models",
        description=(
            "Keyword-search the documented tables. Use ONE specific noun from the "
            "question (e.g. 'cancellation', 'invoice', 'signup', 'mrr'). Case-"
            "insensitive substring match across every model's markdown. Returns "
            "[{uri, name, description, match_count, snippets}] for matching files, "
            "ordered alphabetically by name. Cheaper than `list_models` on large "
            "projects because it only returns hits. Pass `name` to `read_model` to "
            "fetch one entity's full docs."
        ),
    )
    def search_models(keyword: str, max_results: int = 20) -> list[dict[str, Any]]:
        return _search_model_docs(config, keyword, max_results=max_results)

    @mcp.tool(
        name="read_model",
        description=(
            "Return the full markdown documentation for a single dbt model or source. "
            "Use the `name` from `list_models` (e.g. 'main_marts.fct_invoice'). Docs "
            "include description, every column with type + tests (foreign keys via "
            "relationships(), enums via accepted_values()), parents, sample rows, and "
            "compiled SQL. Read this before writing SQL against the table."
        ),
    )
    def read_model(name: str) -> str:
        path = _schema_path(config, name)
        if not path.exists():
            raise ValueError(
                f"no wlens doc for {name!r}. Call `list_models` for the full catalog."
            )
        return path.read_text()

    @mcp.tool(
        name="execute_sql",
        description=(
            "Execute a read-only SQL query (SELECT / WITH ... SELECT) against the "
            "configured wlens warehouse. Returns a structured result. Mutations are "
            "rejected; the warehouse role should also be read-only."
        ),
    )
    def execute_sql(query: str) -> dict[str, Any]:
        if executor is None:
            raise RuntimeError(
                "No warehouse executor configured for this wlens deployment. "
                "Set `executor.kind` in wlens.yml."
            )
        started = time.perf_counter()
        try:
            headers, rows, cache_hit = executor.run(query)
        except ReadOnlyViolation as e:
            logs.event(
                "tool_call_rejected",
                tool="execute_sql",
                sql_hash=logs.hash_sql(query),
                reason=str(e),
            )
            raise
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        result: dict[str, Any] = {
            "sql": query,
            "columns": [{"name": h, "type": None} for h in headers],
            "rows": [dict(zip(headers, _serialise_row(row), strict=False)) for row in rows],
            "row_count": len(rows),
            "cache_hit": cache_hit,
            "elapsed_ms": elapsed_ms,
        }
        logs.event(
            "tool_call_ok",
            tool="execute_sql",
            sql_hash=logs.hash_sql(query),
            row_count=len(rows),
            cache_hit=cache_hit,
            elapsed_ms=elapsed_ms,
        )
        return result


def _serialise_row(row: tuple) -> list[Any]:
    return [_json_safe(v) for v in row]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    # Dates, decimals, uuids, etc. — fall back to str.
    return str(value)


# ─── Resources ─────────────────────────────────────────────────────────────


def _register_resources(mcp: FastMCP, config: Config) -> None:
    # Single catalog resource: returns JSON with {uri, name, description} for
    # every generated doc. The LLM reads this once to get the full table of
    # contents, then fetches individual models via `wlens://models/<name>`.
    #
    # We deliberately do NOT register each model as its own concrete resource:
    # for realistic projects (40+ models) that bloats `resources/list`, makes
    # the Claude Desktop resource picker unusable, and re-broadcasts the same
    # info the catalog already contains. One entry in, 40+ out = less noise,
    # same information.
    @mcp.resource(
        uri=RESOURCE_LIST_URI,
        name="models",
        description=(
            "Catalog of every dbt model / source wlens has generated docs for. "
            "Returns JSON of {uri, name, description}; read a model's URI to "
            "fetch its full markdown docs."
        ),
        mime_type="application/json",
    )
    def list_models() -> str:
        return json.dumps(_list_entries(config), indent=2)

    # Templated per-model resource: `resources/read("wlens://models/<name>")`
    # returns the full markdown for that entity. Templates are advertised on
    # the separate `resources/templates/list` endpoint — the LLM learns about
    # them from the server instructions + the catalog's URIs, not from the
    # client's resource picker UI.
    @mcp.resource(
        uri=RESOURCE_DETAIL_URI,
        name="model",
        description="Full markdown docs for a single dbt model or source.",
        mime_type="text/markdown",
    )
    def read_model(name: str) -> str:
        path = _schema_path(config, name)
        if not path.exists():
            raise ResourceError(f"no wlens doc for {name!r}")
        return path.read_text()


def _list_entries(config: Config) -> list[dict[str, str]]:
    schema_dir = config.output_dir
    if not schema_dir.exists():
        return []
    entries: list[dict[str, str]] = []
    for md in sorted(schema_dir.glob("*.md")):
        if md.name == "_index.md":
            continue
        name = md.stem
        entries.append(
            {
                "uri": f"wlens://models/{name}",
                "name": name,
                "description": _first_line_of(md),
            }
        )
    return entries


def _first_line_of(path: Path) -> str:
    """Return the first non-empty non-header line of a markdown file as a summary."""
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            return line[:200]
    return ""


def _schema_path(config: Config, name: str) -> Path:
    """Resolve `wlens://models/{name}` → `.claude/schema/{name}.md`, safely."""
    # Defend against path traversal: strip anything with slashes or `..`.
    if "/" in name or "\\" in name or ".." in name:
        raise ResourceError(f"invalid model name: {name!r}")
    return config.output_dir / f"{name}.md"


# ─── Search ────────────────────────────────────────────────────────────────


_SEARCH_MAX_SNIPPETS_PER_FILE = 5
_SEARCH_SNIPPET_MAX_LEN = 200


def _search_model_docs(
    config: Config,
    keyword: str,
    *,
    max_results: int,
) -> list[dict[str, Any]]:
    """Server-side grep over `config.output_dir/*.md`.

    Case-insensitive substring match. Substring (not word-boundary) is the
    right call for dbt docs where snake_case means `invoice` needs to hit
    `fct_invoice_line`, `invoice_id`, etc. — word-boundary would miss all
    of those. Plurals / stems (`cancel` → `cancellation`) come along for
    free too. False-positive noise (`plan` → `planning`) is rare in
    real warehouses and the LLM filters those after reading descriptions.

    Returns matching files as `{uri, name, description, match_count, snippets}`
    ordered by filename. `snippets` is up to _SEARCH_MAX_SNIPPETS_PER_FILE
    whitespace-trimmed lines that contain the keyword. Empty keyword raises.
    """
    keyword = (keyword or "").strip()
    if not keyword:
        raise ValueError("`keyword` must be a non-empty string.")

    needle = keyword.lower()
    schema_dir = config.output_dir
    if not schema_dir.exists():
        return []

    out: list[dict[str, Any]] = []
    for md in sorted(schema_dir.glob("*.md")):
        if md.name == "_index.md":
            continue
        try:
            text = md.read_text()
        except OSError:
            continue
        matching = [line.strip() for line in text.splitlines() if needle in line.lower()]
        if not matching:
            continue
        snippets = [
            (s if len(s) <= _SEARCH_SNIPPET_MAX_LEN else s[:_SEARCH_SNIPPET_MAX_LEN] + "…")
            for s in matching[:_SEARCH_MAX_SNIPPETS_PER_FILE]
        ]
        name = md.stem
        out.append(
            {
                "uri": f"wlens://models/{name}",
                "name": name,
                "description": _first_line_of(md),
                "match_count": len(matching),
                "snippets": snippets,
            }
        )
        if len(out) >= max_results:
            break
    return out


# ─── Prompt ────────────────────────────────────────────────────────────────


def _register_prompts(mcp: FastMCP) -> None:
    skill_text = _skill_text()

    @mcp.prompt(
        name="wlens_skill",
        description="The wlens skill — teaches the agent the grep→read→query pattern.",
    )
    def wlens_skill() -> str:
        return skill_text


def _skill_text() -> str:
    return resources.files("wlens.templates").joinpath("SKILL.md").read_text(encoding="utf-8")


# ─── Transport security ────────────────────────────────────────────────────


def _transport_security(allowed_hosts: list[str] | None):
    """Return a TransportSecuritySettings or None.

    When hosting behind a reverse proxy / tunnel we need to allow the proxy's
    Host header. Passing an explicit allowlist (or ["*"] for fully-open)
    replaces the built-in localhost-only default.
    """
    if allowed_hosts is None:
        return None
    from mcp.server.transport_security import TransportSecuritySettings

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=allowed_hosts != ["*"],
        allowed_hosts=allowed_hosts if allowed_hosts != ["*"] else ["*"],
        allowed_origins=["*"] if allowed_hosts == ["*"] else [f"https://{h}" for h in allowed_hosts],
    )
