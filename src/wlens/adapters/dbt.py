"""dbt adapter — reads `manifest.json` + compiled SQL into Entities.

Entity selection is driven by `adapter.include_prefixes` and
`adapter.exclude_prefixes` in `wlens.yml`:

    adapter:
      kind: dbt
      project_dir: ../transform
      include_prefixes: [dim_, fct_]     # only dbt models whose name starts with these
      exclude_prefixes: []

Empty `include_prefixes` means "all models". Sources are emitted when their
name matches `include_prefixes` (or when the list is empty).

The compiled-SQL file lives at
`<project_dir>/target/compiled/<project_name>/models/<node.path>`.
If the compiled file isn't present, the adapter falls back to `raw_code`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from .base import Adapter, Column, Entity, Parent

logger = logging.getLogger(__name__)

MANIFEST_REL = Path("target") / "manifest.json"


@dataclass
class DbtAdapter(Adapter):
    config: Config

    def list_entities(self) -> list[Entity]:
        manifest = self._load_manifest()
        project_name = manifest.get("metadata", {}).get("project_name", "")
        compiled_base = self._project_dir() / "target" / "compiled" / project_name / "models"
        test_index = _build_test_index(manifest)

        entities: list[Entity] = []
        for node_id, node in manifest.get("nodes", {}).items():
            if not self._include_model(node):
                continue
            entities.append(self._model_entity(manifest, node_id, node, compiled_base, test_index))

        for node_id, node in manifest.get("sources", {}).items():
            if not self._include_source(node):
                continue
            entities.append(self._source_entity(node_id, node, test_index))

        entities.sort(key=lambda e: (e.kind, e.schema_name, e.table_name))
        return entities

    # ────────────────────────────────────────────────────────────────────

    def _project_dir(self) -> Path:
        return (self.config.repo_root / self.config.adapter.project_dir).resolve()

    def _manifest_path(self) -> Path:
        return self._project_dir() / MANIFEST_REL

    def _load_manifest(self) -> dict:
        path = self._manifest_path()
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found. Run `dbt compile` in your dbt project first."
            )
        return json.loads(path.read_text())

    def _include_model(self, node: dict) -> bool:
        if node.get("resource_type") != "model":
            return False
        return self._matches_prefixes(node.get("name", ""))

    def _include_source(self, node: dict) -> bool:
        return self._matches_prefixes(node.get("name", ""))

    def _matches_prefixes(self, name: str) -> bool:
        include = self.config.adapter.include_prefixes
        exclude = self.config.adapter.exclude_prefixes
        if any(name.startswith(p) for p in exclude):
            return False
        if not include:
            return True
        return any(name.startswith(p) for p in include)

    def _model_entity(
        self,
        manifest: dict,
        node_id: str,
        node: dict,
        compiled_base: Path,
        test_index: dict[tuple[str, str | None], list[str]],
    ) -> Entity:
        schema_name = node.get("schema") or self.config.adapter.default_schema
        return Entity(
            kind="model",
            schema_name=schema_name,
            table_name=node["name"],
            description=node.get("description", "") or "",
            columns=_columns_from_node(node_id, node, test_index),
            parents=_parents_from_manifest(manifest, node),
            compiled_sql=_compiled_sql(node, compiled_base),
        )

    def _source_entity(
        self,
        node_id: str,
        node: dict,
        test_index: dict[tuple[str, str | None], list[str]],
    ) -> Entity:
        schema_name = node.get("schema") or "public"
        return Entity(
            kind="source",
            schema_name=schema_name,
            table_name=node["name"],
            description=node.get("description", "") or "",
            columns=_columns_from_node(node_id, node, test_index),
            parents=[],
            compiled_sql=None,
        )


def _columns_from_node(
    node_id: str,
    node: dict,
    test_index: dict[tuple[str, str | None], list[str]],
) -> dict[str, Column]:
    columns: dict[str, Column] = {}

    # Legacy inline shape — some adapters / hand-rolled fixtures put tests
    # directly on the column. We still read them if present for back-compat.
    for name, col in (node.get("columns") or {}).items():
        tests: list[str] = []
        for t in col.get("data_tests") or col.get("tests") or []:
            if isinstance(t, str):
                tests.append(t)
            elif isinstance(t, dict) and t:
                tests.append(_format_inline_test(t))

        # Real manifests (dbt 1.x+) store tests as separate nodes.
        tests.extend(test_index.get((node_id, name), []))

        columns[name] = Column(
            name=name,
            data_type=col.get("data_type"),
            description=(col.get("description") or "").strip(),
            tests=tests,
            meta=col.get("meta") or {},
        )
    return columns


# ─── Test extraction from manifest test nodes ──────────────────────────────


_TEST_KWARG_SKIP = frozenset({"column_name", "model"})


def _build_test_index(manifest: dict) -> dict[tuple[str, str | None], list[str]]:
    """Walk `manifest.nodes` for `test` resource_types, build a lookup table.

    Key:   (attached_node_id, column_name_or_None)
    Value: list of human-readable test strings, e.g.
           - "unique"
           - "not_null"
           - "relationships(to=ref('customers'), field=customer_id)"
           - "accepted_values(values=['shipped','completed','pending'])"
    """
    idx: dict[tuple[str, str | None], list[str]] = {}
    sources_by_name = {
        f"{node['source_name']}.{node['name']}": node_id
        for node_id, node in manifest.get("sources", {}).items()
        if "source_name" in node and "name" in node
    }
    models_by_name = {
        node["name"]: node_id
        for node_id, node in manifest.get("nodes", {}).items()
        if node.get("resource_type") == "model" and "name" in node
    }

    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") != "test":
            continue
        attached = node.get("attached_node") or _resolve_attached_from_metadata(
            node, sources_by_name, models_by_name
        )
        if not attached:
            deps = (node.get("depends_on") or {}).get("nodes") or []
            attached = next(
                (d for d in deps if d.startswith(("model.", "source."))),
                None,
            )
        if not attached:
            continue
        idx.setdefault((attached, node.get("column_name")), []).append(_format_test(node))
    for tests in idx.values():
        tests.sort()
    return idx


_MODEL_TEMPLATE_SOURCE = re.compile(r"source\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)")
_MODEL_TEMPLATE_REF = re.compile(r"ref\(\s*'([^']+)'\s*\)")


def _resolve_attached_from_metadata(
    test_node: dict,
    sources_by_name: dict[str, str],
    models_by_name: dict[str, str],
) -> str | None:
    """For tests with `attached_node: None`, parse `test_metadata.kwargs.model`
    to find the node where the test is defined. The kwarg looks like
    `{{ get_where_subquery(source('acme', 'Loss_Payment')) }}` or
    `{{ get_where_subquery(ref('my_model')) }}`.
    """
    template = (test_node.get("test_metadata") or {}).get("kwargs", {}).get("model")
    if not isinstance(template, str):
        return None
    match = _MODEL_TEMPLATE_SOURCE.search(template)
    if match:
        return sources_by_name.get(f"{match.group(1)}.{match.group(2)}")
    match = _MODEL_TEMPLATE_REF.search(template)
    if match:
        return models_by_name.get(match.group(1))
    return None


def _format_test(test_node: dict) -> str:
    """Render one test node as a compact grep-friendly string."""
    meta = test_node.get("test_metadata") or {}
    name = meta.get("name") or test_node.get("name", "unknown")
    kwargs = {k: v for k, v in (meta.get("kwargs") or {}).items() if k not in _TEST_KWARG_SKIP}
    if not kwargs:
        return name
    parts = [f"{k}={_format_test_value(v)}" for k, v in kwargs.items()]
    return f"{name}({', '.join(parts)})"


def _format_test_value(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_test_value(v) for v in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _format_inline_test(test_dict: dict) -> str:
    """Format a legacy `{<name>: {kwarg: val, ...}}` inline test."""
    if not test_dict:
        return ""
    name = next(iter(test_dict.keys()))
    kwargs = test_dict.get(name) or {}
    if not isinstance(kwargs, dict) or not kwargs:
        return name
    kwargs = {k: v for k, v in kwargs.items() if k not in _TEST_KWARG_SKIP}
    if not kwargs:
        return name
    parts = [f"{k}={_format_test_value(v)}" for k, v in kwargs.items()]
    return f"{name}({', '.join(parts)})"


def _parents_from_manifest(manifest: dict, node: dict) -> list[Parent]:
    deps = node.get("depends_on", {}).get("nodes", [])
    out: list[Parent] = []
    for dep in deps:
        if dep.startswith("model."):
            parent = manifest.get("nodes", {}).get(dep)
            if parent:
                out.append(
                    Parent(name=parent["name"], description=parent.get("description", "") or "")
                )
        elif dep.startswith("source."):
            parent = manifest.get("sources", {}).get(dep)
            if parent:
                out.append(
                    Parent(
                        name=f"source:{parent['name']}",
                        description=parent.get("description", "") or "",
                    )
                )
    return out


def _compiled_sql(node: dict, compiled_base: Path) -> str | None:
    rel = node.get("path")
    if rel:
        compiled = compiled_base / rel
        if compiled.exists():
            return compiled.read_text().strip() or None
    raw = (node.get("raw_code") or "").strip()
    return raw or None
