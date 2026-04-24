"""FastMCP tool + resources + prompt behaviour."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import duckdb
import pytest

from wlens.config import load_config
from wlens.mcp.server import create_server


def _setup(tmp_path: Path, dbt_project: Path) -> Path:
    # Tiny DuckDB with one table so execute_sql has something to hit.
    db = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE widget (id INTEGER, color VARCHAR)")
    con.execute("INSERT INTO widget VALUES (1, 'blue'), (2, 'red')")
    con.close()

    cfg_path = tmp_path / "wlens.yml"
    cfg_path.write_text(
        textwrap.dedent(f"""
            adapter:
              kind: dbt
              project_dir: {dbt_project.relative_to(tmp_path)}
              include_prefixes: [dim_, fct_]
            executor:
              kind: duckdb
              path: "t.duckdb"
            output:
              dir: .claude/schema
              include_sample_rows: false
        """).lstrip()
    )

    # Seed one markdown file so the resource has something to list.
    schema = tmp_path / ".claude" / "schema"
    schema.mkdir(parents=True)
    (schema / "prod.dim_widget.md").write_text(
        "# `prod.dim_widget` (model)\n\n"
        "Dimension of every widget.\n"
    )
    return cfg_path


@pytest.mark.asyncio
async def test_execute_sql_returns_structured_result(tmp_path, dbt_project):
    cfg = load_config(_setup(tmp_path, dbt_project))
    server = create_server(cfg)
    try:
        result = await server.mcp.call_tool("execute_sql", {"query": "SELECT color FROM widget ORDER BY id"})
        # FastMCP returns (content, structured) when json_response=True.
        structured = result[1] if isinstance(result, tuple) else result
        assert structured["sql"] == "SELECT color FROM widget ORDER BY id"
        assert structured["row_count"] == 2
        assert structured["columns"] == [{"name": "color", "type": None}]
        assert structured["rows"] == [{"color": "blue"}, {"color": "red"}]
        assert structured["cache_hit"] is False
        assert structured["elapsed_ms"] >= 0
    finally:
        server.close()


@pytest.mark.asyncio
async def test_execute_sql_rejects_mutations(tmp_path, dbt_project):
    from wlens.executor.base import ReadOnlyViolation

    cfg = load_config(_setup(tmp_path, dbt_project))
    server = create_server(cfg)
    try:
        with pytest.raises(Exception) as exc:  # FastMCP wraps the raise
            await server.mcp.call_tool("execute_sql", {"query": "DROP TABLE widget"})
        assert "DROP" in str(exc.value) or "SELECT" in str(exc.value)
    finally:
        server.close()


@pytest.mark.asyncio
async def test_list_models_enumerates_schema_dir(tmp_path, dbt_project):
    cfg = load_config(_setup(tmp_path, dbt_project))
    server = create_server(cfg)
    try:
        contents = await server.mcp.read_resource("wlens://models")
        # FastMCP returns a list of ReadResourceContents.
        payload = _first_text(contents)
        entries = json.loads(payload)
        assert len(entries) == 1
        assert entries[0]["name"] == "prod.dim_widget"
        assert entries[0]["uri"] == "wlens://models/prod.dim_widget"
        assert "Dimension" in entries[0]["description"]
    finally:
        server.close()


@pytest.mark.asyncio
async def test_read_model_returns_markdown(tmp_path, dbt_project):
    cfg = load_config(_setup(tmp_path, dbt_project))
    server = create_server(cfg)
    try:
        contents = await server.mcp.read_resource("wlens://models/prod.dim_widget")
        text = _first_text(contents)
        assert text.startswith("# `prod.dim_widget`")
    finally:
        server.close()


@pytest.mark.asyncio
async def test_only_catalog_resource_is_listed(tmp_path, dbt_project):
    """Keep resources/list short — just the catalog. Per-model resources are
    reachable via the template but not flooded into the client picker."""
    cfg = load_config(_setup(tmp_path, dbt_project))
    server = create_server(cfg)
    try:
        resources = await server.mcp.list_resources()
        uris = {str(r.uri) for r in resources}
        assert uris == {"wlens://models"}
    finally:
        server.close()


@pytest.mark.asyncio
async def test_discovery_is_available_as_tools(tmp_path, dbt_project):
    """Claude Desktop today surfaces tools (not resources) to the LLM. Expose
    search_models / list_models / read_model as tools so the agent can
    actually call them."""
    cfg = load_config(_setup(tmp_path, dbt_project))
    server = create_server(cfg)
    try:
        tool_names = {t.name for t in await server.mcp.list_tools()}
        assert tool_names == {"search_models", "list_models", "read_model", "execute_sql"}
    finally:
        server.close()


@pytest.mark.asyncio
async def test_search_models_finds_matches_with_snippets(tmp_path, dbt_project):
    """search_models should return only matching files + context snippets."""
    cfg = load_config(_setup(tmp_path, dbt_project))
    # Add a second schema file so we can verify filtering works.
    (cfg.output_dir / "prod.fct_cancellation.md").write_text(
        "# `prod.fct_cancellation` (model)\n\n"
        "One row per cancellation survey response.\n\n"
        "### reason\n- Type: `varchar`\n- Tests: accepted_values(values=[price, missing_feature])\n"
    )

    result = await server.mcp.call_tool("search_models", {"keyword": "cancellation"}) if False else None
    # Above line reserved — call it properly:
    server = create_server(cfg)
    try:
        result = await server.mcp.call_tool("search_models", {"keyword": "cancellation"})
        hits = result[1] if isinstance(result, tuple) else result
        if isinstance(hits, dict) and "result" in hits:
            hits = hits["result"]
        names = {h["name"] for h in hits}
        assert "prod.fct_cancellation" in names
        # dim_widget doesn't mention cancellation and should NOT appear.
        assert "prod.dim_widget" not in names
        # Each hit carries snippets.
        hit = next(h for h in hits if h["name"] == "prod.fct_cancellation")
        assert hit["match_count"] >= 1
        assert any("cancellation" in s.lower() for s in hit["snippets"])
        assert hit["uri"] == "wlens://models/prod.fct_cancellation"
    finally:
        server.close()


@pytest.mark.asyncio
async def test_search_models_is_case_insensitive(tmp_path, dbt_project):
    cfg = load_config(_setup(tmp_path, dbt_project))
    server = create_server(cfg)
    try:
        # "Dimension" appears in the dim_widget description (capital D).
        result = await server.mcp.call_tool("search_models", {"keyword": "DIMENSION"})
        hits = result[1] if isinstance(result, tuple) else result
        if isinstance(hits, dict) and "result" in hits:
            hits = hits["result"]
        assert any(h["name"] == "prod.dim_widget" for h in hits)
    finally:
        server.close()


@pytest.mark.asyncio
async def test_search_models_empty_keyword_errors(tmp_path, dbt_project):
    cfg = load_config(_setup(tmp_path, dbt_project))
    server = create_server(cfg)
    try:
        with pytest.raises(Exception):
            await server.mcp.call_tool("search_models", {"keyword": ""})
    finally:
        server.close()


@pytest.mark.asyncio
async def test_search_models_skips_index_file(tmp_path, dbt_project):
    cfg = load_config(_setup(tmp_path, dbt_project))
    # Force _index.md to contain the keyword; it must still not appear as a hit.
    (cfg.output_dir / "_index.md").write_text("widget directory — should not hit\n")
    server = create_server(cfg)
    try:
        result = await server.mcp.call_tool("search_models", {"keyword": "widget"})
        hits = result[1] if isinstance(result, tuple) else result
        if isinstance(hits, dict) and "result" in hits:
            hits = hits["result"]
        names = {h["name"] for h in hits}
        assert "_index" not in names
    finally:
        server.close()


@pytest.mark.asyncio
async def test_search_models_matches_snake_case_fragments(tmp_path, dbt_project):
    """The whole point of substring matching: `invoice` must hit snake_case
    identifiers like `fct_invoice_line` and `invoice_id` — word-boundary
    regex would miss all of these because `_` counts as a word char."""
    cfg = load_config(_setup(tmp_path, dbt_project))
    (cfg.output_dir / "prod.fct_invoice_line.md").write_text(
        "# `prod.fct_invoice_line` (model)\n\nOne row per purchased line.\n"
        "\n### invoice_id\n- Type: `bigint`\n"
    )

    server = create_server(cfg)
    try:
        result = await server.mcp.call_tool("search_models", {"keyword": "invoice"})
        hits = result[1] if isinstance(result, tuple) else result
        if isinstance(hits, dict) and "result" in hits:
            hits = hits["result"]
        names = {h["name"] for h in hits}
        assert "prod.fct_invoice_line" in names
    finally:
        server.close()


@pytest.mark.asyncio
async def test_search_models_matches_stems_and_plurals(tmp_path, dbt_project):
    """Substring means `cancel` naturally matches `cancellation`, `cancels`,
    `cancelled` — no grammar logic required. This is a feature, not a bug."""
    cfg = load_config(_setup(tmp_path, dbt_project))
    (cfg.output_dir / "prod.fct_cancellation.md").write_text(
        "# `prod.fct_cancellation` (model)\n\nOne row per cancellation event.\n"
    )
    server = create_server(cfg)
    try:
        result = await server.mcp.call_tool("search_models", {"keyword": "cancel"})
        hits = result[1] if isinstance(result, tuple) else result
        if isinstance(hits, dict) and "result" in hits:
            hits = hits["result"]
        names = {h["name"] for h in hits}
        assert "prod.fct_cancellation" in names
    finally:
        server.close()


@pytest.mark.asyncio
async def test_list_models_tool_returns_catalog(tmp_path, dbt_project):
    cfg = load_config(_setup(tmp_path, dbt_project))
    server = create_server(cfg)
    try:
        result = await server.mcp.call_tool("list_models", {})
        entries = result[1] if isinstance(result, tuple) else result
        # FastMCP with json_response=True wraps list returns under a "result" key.
        if isinstance(entries, dict) and "result" in entries:
            entries = entries["result"]
        assert isinstance(entries, list)
        names = {e["name"] for e in entries}
        assert "prod.dim_widget" in names
    finally:
        server.close()


@pytest.mark.asyncio
async def test_read_model_tool_returns_markdown(tmp_path, dbt_project):
    cfg = load_config(_setup(tmp_path, dbt_project))
    server = create_server(cfg)
    try:
        result = await server.mcp.call_tool("read_model", {"name": "prod.dim_widget"})
        content = result[0] if isinstance(result, tuple) else result
        text = _first_text(content)
        assert text.startswith("# `prod.dim_widget`")
    finally:
        server.close()


@pytest.mark.asyncio
async def test_read_model_tool_rejects_unknown(tmp_path, dbt_project):
    cfg = load_config(_setup(tmp_path, dbt_project))
    server = create_server(cfg)
    try:
        with pytest.raises(Exception):
            await server.mcp.call_tool("read_model", {"name": "nope"})
    finally:
        server.close()


@pytest.mark.asyncio
async def test_read_model_unknown_errors(tmp_path, dbt_project):
    cfg = load_config(_setup(tmp_path, dbt_project))
    server = create_server(cfg)
    try:
        with pytest.raises(Exception):
            await server.mcp.read_resource("wlens://models/nope")
    finally:
        server.close()


@pytest.mark.asyncio
async def test_read_model_rejects_path_traversal(tmp_path, dbt_project):
    cfg = load_config(_setup(tmp_path, dbt_project))
    server = create_server(cfg)
    try:
        with pytest.raises(Exception):
            # Even via the template, ../ escapes must be rejected.
            await server.mcp.read_resource("wlens://models/..%2F..%2Fetc%2Fpasswd")
    finally:
        server.close()


@pytest.mark.asyncio
async def test_prompt_exposes_skill_text(tmp_path, dbt_project):
    cfg = load_config(_setup(tmp_path, dbt_project))
    server = create_server(cfg)
    try:
        result = await server.mcp.get_prompt("wlens_skill", {})
        # Prompt result is a GetPromptResult with messages; the content text
        # should reference the 3-move pattern.
        first_message = result.messages[0]
        text = getattr(first_message.content, "text", str(first_message.content))
        assert "grep" in text.lower() or "grep" in text
    finally:
        server.close()


def _first_text(contents) -> str:
    """Extract a text payload from an mcp.read_resource result."""
    if hasattr(contents, "contents"):
        contents = contents.contents
    if isinstance(contents, list) and contents:
        c = contents[0]
        if hasattr(c, "content"):
            return c.content
        if hasattr(c, "text"):
            return c.text
    if isinstance(contents, str):
        return contents
    raise AssertionError(f"unexpected resource payload: {contents!r}")
