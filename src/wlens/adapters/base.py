"""Common types + the Adapter ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Column:
    name: str
    data_type: str | None = None
    description: str = ""
    tests: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Parent:
    name: str
    description: str = ""


@dataclass
class Entity:
    kind: str                         # "model" | "source"
    schema_name: str
    table_name: str
    description: str = ""
    columns: dict[str, Column] = field(default_factory=dict)
    parents: list[Parent] = field(default_factory=list)
    compiled_sql: str | None = None

    @property
    def slug(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    @property
    def filename(self) -> str:
        return f"{self.slug}.md"


class Adapter(ABC):
    """Base class for a transformation-framework adapter."""

    @abstractmethod
    def list_entities(self) -> list[Entity]:
        """Return every model / source to emit documentation for."""
