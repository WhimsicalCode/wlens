"""`wlens` command-line entrypoint.

Subcommands:

    wlens init       — drop wlens.yml + skill files (.claude/, .agents/) into cwd
    wlens generate   — read dbt artifacts, write per-table markdown into wlens/schema/
    wlens query      — execute a read-only SQL query against the configured warehouse
    wlens tag-pii    — scan dbt yml files and add `meta: pii: true` to likely-PII columns
    wlens mcp         — start the wlens MCP server (team / demo modes)
    wlens mcp-proxy   — stdio↔HTTP proxy (used by Claude Desktop to reach remote wlens)
    wlens mcp-clients — generate per-client MCP config files for a deployed wlens server
    wlens clean       — remove every file wlens has installed or generated in this repo
"""

from __future__ import annotations

import argparse
import logging
import sys
from importlib import resources
from pathlib import Path

from .config import DEFAULT_CONFIG_FILENAME, DEFAULT_OUTPUT_DIR, find_config, load_config

logger = logging.getLogger(__name__)

WLENS_GITIGNORE_BODY = (
    "# wlens-managed — do not edit.\n"
    ".cache/*\n"
    "!.cache/samples/\n"
    "share/\n"
)
# Predecessor body (pre-`.cache/` rename). `wlens generate` rewrites the file
# in-place when it sees this exact content so existing installs migrate.
WLENS_GITIGNORE_LEGACY = "# wlens-managed — do not edit.\ncache/\nshare/\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wlens",
        description="Warehouse lens for AI agents.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Drop starter wlens.yml + SKILL.md into the current directory.")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing files.")

    p_gen = sub.add_parser("generate", help="Generate per-table markdown docs.")
    p_gen.add_argument("--config", help=f"Path to {DEFAULT_CONFIG_FILENAME} (default: walk up from cwd).")
    p_gen.add_argument("--output-dir", help="Override output directory from config.")
    p_gen.add_argument("--skip-samples", action="store_true", help="Don't fetch sample rows from the warehouse.")
    p_gen.add_argument(
        "--refresh-samples",
        nargs="?",
        const="*",
        default=None,
        metavar="SLUG[,SLUG…]",
        help=(
            "Force-refresh sample rows. Pass with no value to refresh every table, "
            "or with a comma-separated list of `schema.table` slugs to refresh just those."
        ),
    )

    p_query = sub.add_parser("query", help="Execute a read-only SQL query.")
    p_query.add_argument("sql", nargs="?", help="SQL to execute (or piped via stdin).")
    p_query.add_argument("--config", help=f"Path to {DEFAULT_CONFIG_FILENAME} (default: walk up from cwd).")
    p_query.add_argument("--no-cache", action="store_true", help="Skip disk cache.")

    p_tag = sub.add_parser("tag-pii", help="Add `meta: pii: true` to likely-PII columns in dbt yml files.")
    p_tag.add_argument("--config", help=f"Path to {DEFAULT_CONFIG_FILENAME} (default: walk up from cwd).")
    p_tag.add_argument("--dry-run", action="store_true", help="Print what would change without writing.")

    p_mcp = sub.add_parser("mcp", help="Start the wlens MCP server (team deployment / demo).")
    p_mcp.add_argument("--config", help=f"Path to {DEFAULT_CONFIG_FILENAME} (default: walk up from cwd).")
    p_mcp.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0).")
    p_mcp.add_argument("--port", type=int, default=8000, help="HTTP port (default: 8000).")
    p_mcp.add_argument(
        "--dangerously-share",
        action="store_true",
        help="Demo mode: auto-generate a token, open an ngrok tunnel, print a Claude Desktop snippet. Not for production.",
    )
    p_mcp.add_argument(
        "--transport",
        choices=["streamable-http", "sse"],
        default="streamable-http",
        help="MCP wire transport (default: streamable-http).",
    )
    p_mcp.add_argument(
        "--no-auth",
        action="store_true",
        help="Disable bearer-token auth. Only allowed on a localhost bind.",
    )
    p_mcp.add_argument(
        "--allowed-host",
        action="append",
        default=None,
        metavar="HOST",
        help="Add a Host header to the DNS-rebinding allowlist. Repeatable. Use `*` to disable the check entirely.",
    )

    p_proxy = sub.add_parser(
        "mcp-proxy",
        help="Stdio ↔ HTTP MCP proxy. Usually spawned by Claude Desktop from a drop-in config.",
    )
    p_proxy.add_argument("url", help="Remote wlens MCP URL, e.g. https://abc.ngrok-free.app/mcp")

    p_clients = sub.add_parser(
        "mcp-clients",
        help="Generate per-client MCP config files for a deployed wlens server.",
    )
    p_clients.add_argument(
        "--url",
        required=True,
        metavar="URL",
        help="Full MCP URL of your deployed wlens server, e.g. https://wlens.team.com/mcp.",
    )
    p_clients.add_argument(
        "--token",
        default=None,
        metavar="TOKEN",
        help="Bearer token. Defaults to the WLENS_AUTH_TOKEN env var.",
    )
    p_clients.add_argument(
        "--out",
        default=None,
        metavar="DIR",
        help="Output directory for the drop-in files (default: ./wlens/share/).",
    )

    p_clean = sub.add_parser(
        "clean",
        help="Remove every file wlens installed or generated (wlens.yml, .claude/skills/wlens/, schema dir, cache).",
    )
    p_clean.add_argument("--config", help=f"Path to {DEFAULT_CONFIG_FILENAME} (default: walk up from cwd).")
    p_clean.add_argument("--dry-run", action="store_true", help="Print what would be removed without deleting.")
    p_clean.add_argument("--yes", "-y", action="store_true", help="Skip the confirmation prompt.")

    args = parser.parse_args(argv)
    _configure_logging()

    if args.command == "init":
        return _cmd_init(force=args.force)
    if args.command == "generate":
        return _cmd_generate(
            config_path=Path(args.config) if args.config else None,
            output_override=args.output_dir,
            skip_samples=args.skip_samples,
            refresh_samples=args.refresh_samples,
        )
    if args.command == "query":
        return _cmd_query(
            sql=args.sql,
            config_path=Path(args.config) if args.config else None,
            use_cache=not args.no_cache,
        )
    if args.command == "tag-pii":
        return _cmd_tag_pii(
            config_path=Path(args.config) if args.config else None,
            dry_run=args.dry_run,
        )
    if args.command == "mcp":
        return _cmd_mcp(
            config_path=Path(args.config) if args.config else None,
            host=args.host,
            port=args.port,
            dangerously_share=args.dangerously_share,
            no_auth=args.no_auth,
            allowed_hosts=args.allowed_host,
        )
    if args.command == "mcp-proxy":
        from .mcp import proxy
        return proxy.main([args.url])
    if args.command == "mcp-clients":
        return _cmd_mcp_clients(
            url=args.url,
            token=args.token,
            out=Path(args.out) if args.out else None,
        )
    if args.command == "clean":
        return _cmd_clean(
            config_path=Path(args.config) if args.config else None,
            dry_run=args.dry_run,
            assume_yes=args.yes,
        )
    parser.print_help()
    return 2


# Directories we never descend into when scanning for dbt_project.yml.
# Besides the obvious noise (`.git`, `.venv`, `node_modules`) we also skip
# dbt's own output/package dirs so we don't pick up nested example projects.
_SKIP_DIRS = frozenset({
    "node_modules", "target", "dbt_packages", "__pycache__",
    "dist", "build", "venv", "env",
})

# Any directory starting with `.` is also skipped (.git, .venv, .claude, etc.).
_MAX_SCAN_DEPTH = 4


def _detect_dbt_project_dir(cwd: Path) -> str | None:
    """Scan cwd (and subdirectories) for `dbt_project.yml`.

    Rules:
    - Breadth-first, so shallower matches win.
    - Dotfile directories and a short blocklist (node_modules, target, …) are
      pruned so we don't walk into `.git`, `.venv`, dbt's own `target/`, etc.
    - If a directory contains `dbt_project.yml`, we do not descend into it
      (a dbt project nests its own fixtures; we want the outer one).
    - Depth is capped at _MAX_SCAN_DEPTH levels below cwd.
    - On ties at the same depth, picks the alphabetically first and prints
      a note so the user can override.
    """
    from collections import deque

    matches: list[tuple[int, Path]] = []
    queue: deque[tuple[Path, int]] = deque([(cwd, 0)])
    while queue:
        current, depth = queue.popleft()
        if (current / "dbt_project.yml").exists():
            matches.append((depth, current))
            continue  # don't descend further into a dbt project
        if depth >= _MAX_SCAN_DEPTH:
            continue
        try:
            children = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for child in sorted(children, key=lambda p: p.name):
            if not child.is_dir() or child.is_symlink():
                continue
            if child.name.startswith(".") or child.name in _SKIP_DIRS:
                continue
            queue.append((child, depth + 1))

    if not matches:
        return None

    matches.sort(key=lambda t: (t[0], str(t[1])))
    shallowest_depth = matches[0][0]
    tied = [m for m in matches if m[0] == shallowest_depth]
    best = tied[0][1]

    if len(tied) > 1:
        others = [str(m[1].relative_to(cwd)) for m in tied[1:]]
        logger.info(
            f"multiple dbt_project.yml files found at depth {shallowest_depth}: "
            f"chose {best.relative_to(cwd)!s}; also found {others}. "
            "Edit wlens.yml if that's wrong."
        )

    rel = best.relative_to(cwd)
    return "." if rel == Path(".") else str(rel)


class _CliFormatter(logging.Formatter):
    """Drop the level prefix for INFO; keep it (lowercased) for warnings/errors.

    Keeps user-facing output clean while letting warnings and errors stand out.
    """

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.levelno <= logging.INFO:
            return msg
        return f"{record.levelname.lower()}: {msg}"


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_CliFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)


def _cmd_init(*, force: bool) -> int:
    cwd = Path.cwd()
    detected_project_dir = _detect_dbt_project_dir(cwd)
    detected_duckdb = _detect_duckdb_file(cwd)

    # Two skill destinations cover every native Agent Skills host today:
    #   .claude/skills/      → Claude Code (doesn't scan .agents/)
    #   .agents/skills/      → open standard (agentskills.io) — Gemini CLI,
    #                          Codex CLI, Cursor, GitHub Copilot in VS Code,
    #                          and any future tool that adopts the standard.
    # Same template body, two write paths — agents discover wherever they look.
    targets = [
        (cwd / DEFAULT_CONFIG_FILENAME, "wlens.yml"),
        (cwd / ".claude" / "skills" / "wlens" / "SKILL.md", "SKILL.md"),
        (cwd / ".agents" / "skills" / "wlens" / "SKILL.md", "SKILL.md"),
    ]

    for dest, template_name in targets:
        if dest.exists() and not force:
            logger.info(f"skip: {dest.relative_to(cwd)} already exists (use --force to overwrite)")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = _read_template(template_name)
        if template_name == "wlens.yml":
            if detected_project_dir is not None and detected_project_dir != ".":
                content = _set_yaml_value(content, "project_dir", detected_project_dir)
            if detected_duckdb is not None:
                content = _set_yaml_value(content, "path", detected_duckdb)
        dest.write_text(content)
        logger.info(f"wrote {dest.relative_to(cwd)}")

    # Seed `wlens/.gitignore`. Schema markdown + sample cache are committed
    # (the latter pins sample rows for stable diffs); SQL query cache and
    # share/ stay out of the consumer's repo.
    gitignore = cwd / "wlens" / ".gitignore"
    if not gitignore.exists() or force:
        gitignore.parent.mkdir(parents=True, exist_ok=True)
        gitignore.write_text(WLENS_GITIGNORE_BODY)
        logger.info(f"wrote {gitignore.relative_to(cwd)}")

    logger.info("")
    if detected_project_dir is not None:
        logger.info(
            f"detected dbt_project.yml at {detected_project_dir!r} — wlens.yml was "
            f"written with adapter.project_dir: {detected_project_dir}"
        )
    else:
        logger.info(
            "no dbt_project.yml found — wlens.yml defaults to adapter.project_dir: '.'. "
            "Edit it if your dbt project lives elsewhere."
        )
    if detected_duckdb is not None:
        logger.info(
            f"detected DuckDB file at {detected_duckdb!r} — wlens.yml was written with "
            f"executor.kind: duckdb, executor.path: {detected_duckdb}"
        )
    else:
        logger.info(
            "no .duckdb file found — wlens.yml defaults to executor.kind: duckdb with a "
            "placeholder path. Switch to postgres/redshift or set a real DuckDB path."
        )
    logger.info("")
    logger.info("Next:")
    logger.info("  1. Review wlens.yml — confirm executor settings.")
    logger.info("  2. Compile your dbt project: (cd <project_dir> && dbt compile)")
    logger.info("  3. Run: wlens generate")
    return 0


def _detect_duckdb_file(cwd: Path) -> str | None:
    """Scan cwd (and subdirectories) for a `.duckdb` file.

    Uses the same pruning rules as _detect_dbt_project_dir so we don't walk into
    `.git`, `.venv`, `target/`, `dbt_packages/`, etc. Breadth-first, shallowest
    wins, alphabetical tiebreak with a note when there are multiple.
    """
    from collections import deque

    matches: list[tuple[int, Path]] = []
    queue: deque[tuple[Path, int]] = deque([(cwd, 0)])
    while queue:
        current, depth = queue.popleft()
        try:
            children = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for child in sorted(children, key=lambda p: p.name):
            if child.is_file() and child.suffix == ".duckdb":
                matches.append((depth, child))
            elif child.is_dir() and not child.is_symlink():
                if child.name.startswith(".") or child.name in _SKIP_DIRS:
                    continue
                if depth < _MAX_SCAN_DEPTH:
                    queue.append((child, depth + 1))

    if not matches:
        return None

    matches.sort(key=lambda t: (t[0], str(t[1])))
    shallowest_depth = matches[0][0]
    tied = [m for m in matches if m[0] == shallowest_depth]
    best = tied[0][1]

    if len(tied) > 1:
        others = [str(m[1].relative_to(cwd)) for m in tied[1:]]
        logger.info(
            f"multiple .duckdb files found at depth {shallowest_depth}: "
            f"chose {best.relative_to(cwd)!s}; also found {others}. "
            "Edit wlens.yml if that's wrong."
        )

    rel = best.relative_to(cwd)
    return str(rel)


def _set_yaml_value(yaml_content: str, key: str, value: str) -> str:
    """Rewrite the first occurrence of `<key>:` in a wlens.yml template.

    Preserves indentation. Only rewrites an *uncommented* line whose trimmed
    text starts with `<key>:`, so the commented example block further down in
    the template is left untouched.
    """
    prefix = f"{key}:"
    out_lines: list[str] = []
    replaced = False
    for line in yaml_content.splitlines(keepends=True):
        stripped = line.lstrip()
        if not replaced and not stripped.startswith("#") and stripped.startswith(prefix):
            indent = line[: len(line) - len(stripped)]
            line_end = "\n" if line.endswith("\n") else ""
            out_lines.append(f"{indent}{key}: {value}{line_end}")
            replaced = True
        else:
            out_lines.append(line)
    return "".join(out_lines)


def _migrate_wlens_gitignore(repo_root: Path) -> None:
    """Rewrite a pre-`.cache/` `wlens/.gitignore` to the new pattern in place.

    One-shot migration helper. Safe to delete (along with `WLENS_GITIGNORE_LEGACY`
    and the two test cases in tests/test_cli.py) once existing installs have all
    run `wlens generate` at least once after the rename — target 0.5+.
    """
    path = repo_root / "wlens" / ".gitignore"
    if not path.exists():
        return
    if path.read_text() == WLENS_GITIGNORE_LEGACY:
        path.write_text(WLENS_GITIGNORE_BODY)
        logger.info(f"updated {path.relative_to(repo_root)} for new cache layout")


def _cmd_generate(
    *,
    config_path: Path | None,
    output_override: str | None,
    skip_samples: bool,
    refresh_samples: str | None,
) -> int:
    from .adapters.dbt import DbtAdapter
    from .entities.loader import load_entities
    from .executor import build_executor
    from .render.markdown import render_and_write_all

    config = load_config(config_path)
    if output_override:
        config.output.dir = output_override

    _migrate_wlens_gitignore(config.repo_root)

    if config.adapter.kind != "dbt":
        raise NotImplementedError(
            f"Adapter kind {config.adapter.kind!r} not yet implemented. "
            "v0.1 supports: dbt."
        )

    adapter = DbtAdapter(config)
    entities = adapter.list_entities()

    custom_entities = load_entities(config)

    executor = None
    if config.output.include_sample_rows and not skip_samples:
        try:
            executor = build_executor(config)
        except Exception as e:
            logger.warning(f"could not build executor ({e!r}) — continuing without sample rows.")
            executor = None

    refresh_arg: bool | set[str] = False
    if refresh_samples == "*":
        refresh_arg = True
    elif refresh_samples:
        refresh_arg = {s.strip() for s in refresh_samples.split(",") if s.strip()}

    count = render_and_write_all(
        entities, custom_entities, executor, config, refresh_samples=refresh_arg
    )
    logger.info(f"wrote {count} entity files to {config.output.dir}")
    if executor is not None:
        executor.close()
    return 0


def _cmd_query(*, sql: str | None, config_path: Path | None, use_cache: bool) -> int:
    from .executor import build_executor, format_markdown_table
    from .executor.base import ReadOnlyViolation

    if sql is None and not sys.stdin.isatty():
        sql = sys.stdin.read().strip()
    if not sql:
        print("Usage: wlens query \"SELECT ...\"   (or pipe SQL via stdin)", file=sys.stderr)
        return 2

    config = load_config(config_path)
    try:
        executor = build_executor(config)
    except Exception as e:
        print(f"Error building executor: {e}", file=sys.stderr)
        return 1

    try:
        try:
            headers, rows, cache_hit = executor.run(sql, use_cache=use_cache)
        except ReadOnlyViolation as e:
            print(f"Error: read-only guard rejected the query — {e}", file=sys.stderr)
            return 1
        output = format_markdown_table(headers, rows)
        print(output)
        print(f"\n({len(rows)} rows{' — cached' if cache_hit else ''})")
    finally:
        executor.close()
    return 0


def _cmd_tag_pii(*, config_path: Path | None, dry_run: bool) -> int:
    from .render.pii import scan_and_tag

    config = load_config(config_path)
    project_dir = (config.repo_root / config.adapter.project_dir).resolve()
    models_dir = project_dir / "models"
    if not models_dir.exists():
        print(f"Error: expected a 'models' directory at {models_dir}", file=sys.stderr)
        return 1
    scan_and_tag(models_dir, dry_run=dry_run, repo_root=config.repo_root)
    return 0


def _cmd_clean(
    *,
    config_path: Path | None,
    dry_run: bool,
    assume_yes: bool,
) -> int:
    import shutil

    # Resolve paths. If wlens.yml is missing, fall back to defaults rooted at cwd
    # — clean should still work for a half-deleted install.
    cfg_path: Path | None = None
    repo_root: Path
    output_dir: Path
    try:
        cfg_path = Path(config_path) if config_path else find_config()
        config = load_config(cfg_path)
        repo_root = config.repo_root
        output_dir = config.output_dir
    except FileNotFoundError:
        repo_root = Path.cwd()
        output_dir = repo_root / DEFAULT_OUTPUT_DIR
        cfg_path = None

    # Everything wlens creates lives under `wlens/`, so nuke the whole dir in
    # one go. Skill files live under .claude/ (Claude Code) and .agents/ (the
    # open standard — Gemini CLI, Codex CLI, Cursor, …) — two well-known
    # directories outside `wlens/` that we own a single subdir of.
    targets: list[Path] = [
        repo_root / "wlens",
        repo_root / ".claude" / "skills" / "wlens",
        repo_root / ".agents" / "skills" / "wlens",
    ]
    # Back-compat: previous versions scattered files at the repo root. Clean
    # them up if they're still there.
    for legacy in (".wlens-cache", "wlens/cache", "wlens-share", ".claude/schema"):
        legacy_path = repo_root / legacy
        if legacy_path.exists():
            targets.append(legacy_path)
    if output_dir.exists():
        targets.append(output_dir)
    if cfg_path is not None:
        targets.append(cfg_path)

    # Collapse by ancestry: if wlens/ is in the list there's no point keeping
    # wlens/schema/ too — removing the parent will drop the child, and trying
    # to unlink the already-gone child raises FileNotFoundError.
    existing = _collapse_nested_paths([t for t in targets if t.exists()])
    if not existing:
        print("Nothing to clean — no wlens artefacts found.")
        return 0

    print("The following will be removed:")
    for t in existing:
        kind = "dir " if t.is_dir() else "file"
        try:
            shown = t.relative_to(repo_root)
        except ValueError:
            shown = t
        print(f"  {kind}  {shown}")

    if dry_run:
        print("\n(dry-run — no changes made)")
        return 0

    if not assume_yes:
        try:
            answer = input("\nProceed? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            print("Aborted.")
            return 1

    for t in existing:
        if not t.exists():
            # An ancestor earlier in the loop already removed us.
            continue
        if t.is_dir():
            shutil.rmtree(t)
        else:
            t.unlink()
        logger.info(f"removed {t}")

    # Tidy empty parent directories that were only there for wlens.
    empty_parents = [
        repo_root / ".claude" / "skills", repo_root / ".claude",
        repo_root / ".agents" / "skills", repo_root / ".agents",
    ]
    for parent in empty_parents:
        if parent.exists() and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            logger.info(f"removed empty {parent}")

    return 0


def _collapse_nested_paths(paths: list[Path]) -> list[Path]:
    """Drop any path that's inside another path already in the list.

    `wlens/` + `wlens/schema/` → `wlens/`. Preserves input order among the
    survivors. Resolves to absolute paths first so relative/absolute mixes
    don't hide an ancestry relationship.
    """
    resolved = [p.resolve() for p in paths]
    kept: list[Path] = []
    for original, absolute in zip(paths, resolved, strict=False):
        if any(other != absolute and other in absolute.parents for other in resolved):
            continue
        kept.append(original)
    return kept


def _cmd_mcp(
    *,
    config_path: Path | None,
    host: str,
    port: int,
    dangerously_share: bool,
    no_auth: bool,
    allowed_hosts: list[str] | None,
) -> int:
    config = load_config(config_path)

    if dangerously_share:
        from .mcp import share
        return share.run(config, port=port, allowed_hosts=allowed_hosts)

    from .mcp import app as mcp_app
    return mcp_app.run(
        config,
        host=host,
        port=port,
        dangerously_share=False,
        no_auth=no_auth,
        allowed_hosts=allowed_hosts,
    )


def _cmd_mcp_clients(*, url: str, token: str | None, out: Path | None) -> int:
    """Generate per-client MCP config files for a deployed wlens server.

    Wraps `write_share_files()` with a stable URL and token from a team
    deployment, so the same drop-ins that `--dangerously-share` produces can
    be handed to teammates pointing at your production server.
    """
    import os

    from .mcp.auth import AUTH_ENV_VAR
    from .mcp.share import write_share_files

    resolved_token = token or os.environ.get(AUTH_ENV_VAR)
    if not resolved_token:
        logger.error("no token provided. Pass --token or set %s.", AUTH_ENV_VAR)
        return 1

    target_dir = out if out is not None else Path.cwd() / "wlens" / "share"
    written = write_share_files(target_dir, mcp_url=url, token=resolved_token)

    logger.info("wrote %d client config(s) to %s/:", len(written), target_dir)
    for name, path in sorted(written.items()):
        logger.info("  %s: %s", name, path.name)
    logger.info("each file contains the bearer token; distribute carefully.")

    return 0


def _read_template(name: str) -> str:
    return resources.files("wlens.templates").joinpath(name).read_text(encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
