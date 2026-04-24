"""Shared test fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture()
def dbt_project(tmp_path: Path) -> Path:
    """Materialise a tiny dbt project with manifest.json at target/manifest.json."""
    project = tmp_path / "dbt_project"
    target = project / "target"
    target.mkdir(parents=True)
    shutil.copy(FIXTURES / "manifest.tiny.json", target / "manifest.json")
    return project
