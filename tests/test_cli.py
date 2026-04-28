"""CLI smoke tests — init, help, and early-error paths."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wlens.cli import main


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "wlens" in captured.out.lower()


def test_init_writes_starter_files(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = main(["init"])
    assert rc == 0
    assert (tmp_path / "wlens.yml").exists()
    assert (tmp_path / ".claude" / "skills" / "wlens" / "SKILL.md").exists()

    # SKILL.md should have the required frontmatter so clients recognise it.
    skill = (tmp_path / ".claude" / "skills" / "wlens" / "SKILL.md").read_text()
    assert skill.startswith("---\nname: wlens\n")
    # SKILL.md points the LLM at the new wlens/schema/ home, not the old
    # .claude/schema/ path.
    assert "wlens/schema/" in skill
    assert ".claude/schema/" not in skill


def test_init_writes_skill_to_two_locations(tmp_path: Path, monkeypatch):
    """One template, two discovery paths: Claude Code (`.claude/skills/`) and
    the open standard `.agents/skills/` — which Gemini CLI, Codex CLI, Cursor,
    and GitHub Copilot in VS Code all scan."""
    monkeypatch.chdir(tmp_path)
    main(["init"])

    claude_skill = tmp_path / ".claude" / "skills" / "wlens" / "SKILL.md"
    agents_skill = tmp_path / ".agents" / "skills" / "wlens" / "SKILL.md"

    assert claude_skill.exists()
    assert agents_skill.exists()
    # Gemini CLI scans .agents/skills/ only, NOT .gemini/skills/.
    assert not (tmp_path / ".gemini").exists()

    # Both files are byte-identical: same template, two destinations.
    body = claude_skill.read_text()
    assert agents_skill.read_text() == body


def test_init_writes_wlens_gitignore(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    gi = tmp_path / "wlens" / ".gitignore"
    assert gi.exists()
    body = gi.read_text()
    # cache/ and share/ must be ignored — schema/ must NOT be (it's the point).
    assert "cache/" in body
    assert "share/" in body
    assert "schema/" not in body


def test_init_defaults_project_dir_to_dot_when_no_dbt_project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    cfg = (tmp_path / "wlens.yml").read_text()
    # Template default is `.` when nothing is detected.
    assert "project_dir: ." in cfg
    assert "project_dir: transform" not in cfg


def test_init_autodetects_dbt_project_at_root(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dbt_project.yml").write_text("name: demo\n")
    main(["init"])
    cfg = (tmp_path / "wlens.yml").read_text()
    assert "project_dir: ." in cfg


def test_init_autodetects_dbt_project_in_subdir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "transform").mkdir()
    (tmp_path / "transform" / "dbt_project.yml").write_text("name: demo\n")
    main(["init"])
    cfg = (tmp_path / "wlens.yml").read_text()
    assert "project_dir: transform" in cfg


def test_init_autodetect_prefers_root_over_subdir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dbt_project.yml").write_text("name: root\n")
    (tmp_path / "transform").mkdir()
    (tmp_path / "transform" / "dbt_project.yml").write_text("name: sub\n")
    main(["init"])
    cfg = (tmp_path / "wlens.yml").read_text()
    # Root wins — depth 0 beats depth 1.
    assert "project_dir: ." in cfg
    assert "project_dir: transform" not in cfg


def test_init_finds_nested_dbt_project(tmp_path: Path, monkeypatch):
    # Two levels deep — rglob-style scan must find it.
    monkeypatch.chdir(tmp_path)
    nested = tmp_path / "data" / "warehouse"
    nested.mkdir(parents=True)
    (nested / "dbt_project.yml").write_text("name: demo\n")
    main(["init"])
    cfg = (tmp_path / "wlens.yml").read_text()
    assert "project_dir: data/warehouse" in cfg


def test_init_skips_noise_dirs(tmp_path: Path, monkeypatch):
    # Plant a dbt_project.yml inside a pruned dir AND one in a real subdir.
    # The real one must be picked; the noise one must be ignored.
    monkeypatch.chdir(tmp_path)
    for noise in (".venv", "node_modules", "target", "dbt_packages"):
        d = tmp_path / noise / "inner"
        d.mkdir(parents=True)
        (d / "dbt_project.yml").write_text("name: bogus\n")
    real = tmp_path / "dbt"
    real.mkdir()
    (real / "dbt_project.yml").write_text("name: real\n")

    main(["init"])
    cfg = (tmp_path / "wlens.yml").read_text()
    assert "project_dir: dbt" in cfg
    assert ".venv" not in cfg
    assert "node_modules" not in cfg


def test_init_respects_depth_cap(tmp_path: Path, monkeypatch):
    # 6-level-deep dbt project should NOT be found (cap is 4).
    monkeypatch.chdir(tmp_path)
    deep = tmp_path / "a" / "b" / "c" / "d" / "e" / "f"
    deep.mkdir(parents=True)
    (deep / "dbt_project.yml").write_text("name: too-deep\n")

    main(["init"])
    cfg = (tmp_path / "wlens.yml").read_text()
    # Falls back to the template default `.` because nothing was found within the cap.
    assert "project_dir: ." in cfg
    assert "a/b/c/d/e/f" not in cfg


def test_init_defaults_executor_to_duckdb(tmp_path: Path, monkeypatch):
    """With no .duckdb in sight the template default (duckdb + placeholder path) ships."""
    monkeypatch.chdir(tmp_path)
    main(["init"])
    cfg = (tmp_path / "wlens.yml").read_text()
    # OSS default is duckdb — redshift is only an example in the commented block.
    first_kind = cfg.split("kind:", 2)
    # `kind: dbt` is first (under adapter:), `kind: duckdb` is second (under executor:)
    assert "kind: dbt" in cfg
    assert "kind: duckdb" in cfg
    assert "path: warehouse.duckdb" in cfg


def test_init_autodetects_duckdb_file_at_root(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "jaffle_shop.duckdb").write_bytes(b"")
    main(["init"])
    cfg = (tmp_path / "wlens.yml").read_text()
    assert "kind: duckdb" in cfg
    assert "path: jaffle_shop.duckdb" in cfg
    # The template placeholder has been overwritten.
    assert "path: warehouse.duckdb" not in cfg


def test_init_autodetects_duckdb_in_subdir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "warehouse.duckdb").write_bytes(b"")
    main(["init"])
    cfg = (tmp_path / "wlens.yml").read_text()
    assert "path: db/warehouse.duckdb" in cfg


def test_init_duckdb_autodetect_skips_noise_dirs(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Plant a .duckdb inside dbt_packages / .venv — must be ignored.
    (tmp_path / "dbt_packages" / "pkg").mkdir(parents=True)
    (tmp_path / "dbt_packages" / "pkg" / "fixture.duckdb").write_bytes(b"")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "oops.duckdb").write_bytes(b"")
    # Plant a real one at root.
    (tmp_path / "prod.duckdb").write_bytes(b"")

    main(["init"])
    cfg = (tmp_path / "wlens.yml").read_text()
    assert "path: prod.duckdb" in cfg
    assert "fixture.duckdb" not in cfg
    assert "oops.duckdb" not in cfg


def test_init_is_idempotent_without_force(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    cfg_path = tmp_path / "wlens.yml"
    cfg_path.write_text("# user-edited\n")
    # Second run without --force must not clobber.
    main(["init"])
    assert cfg_path.read_text() == "# user-edited\n"


def test_init_force_overwrites(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    cfg_path = tmp_path / "wlens.yml"
    cfg_path.write_text("# user-edited\n")
    main(["init", "--force"])
    assert cfg_path.read_text() != "# user-edited\n"
    assert "adapter:" in cfg_path.read_text()


def test_generate_with_missing_manifest_fails_loud(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # A bare init — no dbt project, no manifest.
    main(["init"])
    with pytest.raises(FileNotFoundError):
        main(["generate"])
