"""Load and validate `wlens.yml`.

Config shape:

    adapter:
      kind: dbt
      project_dir: ../transform
      # Optional additional keys per adapter (e.g. filter rules).
      include_prefixes: []           # only emit entities whose name starts with one of these
      exclude_prefixes: []           # skip entities whose name starts with one of these
      default_schema: prod           # fallback schema name for dbt models

    executor:
      kind: redshift                 # redshift | postgres | null
      host: ${WLENS_DB_HOST}
      port: 5439
      database: ${WLENS_DB_NAME}
      user: ${WLENS_DB_USER}
      password: ${WLENS_DB_PASSWORD}

    output:
      dir: .claude/schema
      include_sample_rows: true
      sample_size: 5

    plugins:
      - ./wlens_catalogs.py            # optional: user-defined TableCatalog subclasses

    entities:
      - kind: feature_flags             # auto-rendered (zero-code Option 1)
        title: Feature flags
        source: path/to/flags.yml
        table: public.feature_flags

Environment-variable references `${VAR}` are expanded at load time.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_FILENAME = "wlens.yml"
DEFAULT_OUTPUT_DIR = "wlens/schema"
DEFAULT_SAMPLE_SIZE = 5
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


@dataclass
class AdapterConfig:
    kind: str = "dbt"
    project_dir: str = "."
    include_prefixes: list[str] = field(default_factory=list)
    exclude_prefixes: list[str] = field(default_factory=list)
    default_schema: str = "prod"


@dataclass
class ExecutorConfig:
    kind: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    user: str | None = None
    password: str | None = None
    # File path (or `:memory:`) for file-based engines like DuckDB.
    path: str | None = None


@dataclass
class OutputConfig:
    dir: str = DEFAULT_OUTPUT_DIR
    include_sample_rows: bool = True
    sample_size: int = DEFAULT_SAMPLE_SIZE
    # Extra value-level obfuscation rules appended to the built-in defaults
    # (email, UUID, URL, IP, phone). Each item: {pattern, replacement}.
    obfuscate: list[dict] = field(default_factory=list)


@dataclass
class EntityConfig:
    kind: str
    source: str
    table: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    adapter: AdapterConfig
    executor: ExecutorConfig
    output: OutputConfig
    entities: list[EntityConfig]
    repo_root: Path
    plugins: list[str] = field(default_factory=list)

    @property
    def output_dir(self) -> Path:
        return (self.repo_root / self.output.dir).resolve()


def find_config(start: Path | None = None) -> Path:
    """Walk up from `start` looking for a wlens.yml."""
    start = (start or Path.cwd()).resolve()
    for parent in (start, *start.parents):
        candidate = parent / DEFAULT_CONFIG_FILENAME
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"No {DEFAULT_CONFIG_FILENAME} found in {start} or any parent directory. "
        "Run `wlens init` to create one."
    )


def load_config(path: Path | None = None) -> Config:
    """Load and validate wlens.yml, expanding ${ENV_VAR} references."""
    cfg_path = Path(path) if path else find_config()
    raw = yaml.safe_load(cfg_path.read_text()) or {}
    expanded = _expand_env(raw)
    return _build_config(expanded, cfg_path.parent)


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return ENV_VAR_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _build_config(raw: dict, repo_root: Path) -> Config:
    adapter_raw = raw.get("adapter") or {}
    executor_raw = raw.get("executor") or {}
    output_raw = raw.get("output") or {}
    entities_raw = raw.get("entities") or []

    adapter = AdapterConfig(
        kind=adapter_raw.get("kind", "dbt"),
        project_dir=adapter_raw.get("project_dir", "."),
        include_prefixes=list(adapter_raw.get("include_prefixes") or []),
        exclude_prefixes=list(adapter_raw.get("exclude_prefixes") or []),
        default_schema=adapter_raw.get("default_schema", "prod"),
    )
    port_value = executor_raw.get("port")
    executor = ExecutorConfig(
        kind=executor_raw.get("kind"),
        host=_str_or_none(executor_raw.get("host")),
        port=int(port_value) if port_value not in (None, "") else None,
        database=_str_or_none(executor_raw.get("database")),
        user=_str_or_none(executor_raw.get("user")),
        password=_str_or_none(executor_raw.get("password")),
        path=_str_or_none(executor_raw.get("path")),
    )
    output = OutputConfig(
        dir=output_raw.get("dir", DEFAULT_OUTPUT_DIR),
        include_sample_rows=bool(output_raw.get("include_sample_rows", True)),
        sample_size=int(output_raw.get("sample_size", DEFAULT_SAMPLE_SIZE)),
        obfuscate=list(output_raw.get("obfuscate") or []),
    )

    entities: list[EntityConfig] = []
    for item in entities_raw:
        if not isinstance(item, dict) or "kind" not in item or "source" not in item:
            raise ValueError(
                f"Each entry under `entities` must have `kind` and `source`. Got: {item!r}"
            )
        entities.append(
            EntityConfig(
                kind=item["kind"],
                source=item["source"],
                table=item.get("table"),
                extra={k: v for k, v in item.items() if k not in {"kind", "source", "table"}},
            )
        )

    plugins = [str(p) for p in (raw.get("plugins") or [])]

    return Config(
        adapter=adapter,
        executor=executor,
        output=output,
        entities=entities,
        repo_root=repo_root,
        plugins=plugins,
    )


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value)
    return s if s != "" else None
