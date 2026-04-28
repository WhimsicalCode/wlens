"""`wlens clean` — removes every file wlens installed or generated."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from wlens.cli import main


def _wlens_layout(tmp_path: Path) -> None:
    """Create a representative post-use wlens install at `tmp_path`."""
    (tmp_path / "wlens.yml").write_text(
        textwrap.dedent("""
            adapter:
              kind: dbt
              project_dir: .
            output:
              dir: .claude/schema
        """).lstrip()
    )
    # `wlens init` plants the skill into two discovery paths: .claude/ for
    # Claude Code and .agents/ for the open standard (Gemini CLI, Codex, …).
    for top in (".claude", ".agents"):
        skill_dir = tmp_path / top / "skills" / "wlens"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: wlens\n---\n")
    schema = tmp_path / ".claude" / "schema"
    schema.mkdir(parents=True)
    (schema / "_index.md").write_text("# index\n")
    (schema / "prod.dim_user.md").write_text("# user\n")
    cache = tmp_path / ".wlens-cache" / "sql"
    cache.mkdir(parents=True)
    (cache / "abc123.json").write_text("{}")
    share = tmp_path / "wlens-share"
    share.mkdir()
    (share / ".mcp.json").write_text("{}")
    (share / "claude_desktop_config.json").write_text("{}")


def test_clean_removes_all_wlens_files(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _wlens_layout(tmp_path)

    assert main(["clean", "--yes"]) == 0

    assert not (tmp_path / "wlens.yml").exists()
    assert not (tmp_path / ".claude" / "skills" / "wlens").exists()
    assert not (tmp_path / ".agents" / "skills" / "wlens").exists()
    assert not (tmp_path / ".claude" / "schema").exists()
    assert not (tmp_path / ".wlens-cache").exists()
    assert not (tmp_path / "wlens-share").exists()
    # Empty parents are tidied across both skill hosts.
    assert not (tmp_path / ".claude" / "skills").exists()
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".agents" / "skills").exists()
    assert not (tmp_path / ".agents").exists()


def test_clean_preserves_unrelated_agents_content(tmp_path: Path, monkeypatch):
    """The same defence-in-depth that protects .claude/skills also protects
    .agents/skills: a foreign skill in the parent dir must survive."""
    monkeypatch.chdir(tmp_path)
    _wlens_layout(tmp_path)

    # Plant an unrelated skill under .agents/skills/.
    other_agents = tmp_path / ".agents" / "skills" / "team-skill"
    other_agents.mkdir(parents=True)
    (other_agents / "SKILL.md").write_text("team\n")

    assert main(["clean", "--yes"]) == 0

    # Foreign skill survives…
    assert other_agents.exists()
    # …and so do its non-empty parents.
    assert (tmp_path / ".agents" / "skills").exists()
    assert (tmp_path / ".agents").exists()


def test_clean_preserves_unrelated_claude_content(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _wlens_layout(tmp_path)

    # Another skill sharing .claude/skills/ — must not be touched.
    other = tmp_path / ".claude" / "skills" / "my-other-skill"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("other\n")

    assert main(["clean", "--yes"]) == 0

    assert other.exists()
    assert (other / "SKILL.md").read_text() == "other\n"
    # .claude/skills/ stays because it isn't empty.
    assert (tmp_path / ".claude" / "skills").exists()
    assert (tmp_path / ".claude").exists()


def test_clean_dry_run_touches_nothing(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _wlens_layout(tmp_path)

    assert main(["clean", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "will be removed" in out
    assert "dry-run" in out

    # Everything still there.
    assert (tmp_path / "wlens.yml").exists()
    assert (tmp_path / ".claude" / "schema" / "_index.md").exists()
    assert (tmp_path / ".wlens-cache").exists()


def test_clean_with_no_artefacts_is_a_noop(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["clean", "--yes"]) == 0
    assert "Nothing to clean" in capsys.readouterr().out


def test_clean_aborts_without_confirmation(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _wlens_layout(tmp_path)

    # Simulate user answering "n".
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    assert main(["clean"]) == 1
    assert (tmp_path / "wlens.yml").exists()
    assert "Aborted" in capsys.readouterr().out


def test_clean_accepts_yes_confirmation(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _wlens_layout(tmp_path)

    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

    assert main(["clean"]) == 0
    assert not (tmp_path / "wlens.yml").exists()


def test_clean_works_without_wlens_yml(tmp_path: Path, monkeypatch):
    """Half-deleted install: wlens.yml is gone but schema/cache remain."""
    monkeypatch.chdir(tmp_path)
    _wlens_layout(tmp_path)
    (tmp_path / "wlens.yml").unlink()

    assert main(["clean", "--yes"]) == 0

    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".wlens-cache").exists()


def test_clean_respects_custom_output_dir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wlens.yml").write_text(
        textwrap.dedent("""
            adapter:
              kind: dbt
            output:
              dir: docs/warehouse
        """).lstrip()
    )
    custom = tmp_path / "docs" / "warehouse"
    custom.mkdir(parents=True)
    (custom / "_index.md").write_text("# x\n")

    assert main(["clean", "--yes"]) == 0
    assert not custom.exists()
    assert not (tmp_path / "wlens.yml").exists()


def test_clean_handles_output_dir_inside_wlens_dir(tmp_path: Path, monkeypatch):
    """Default layout: output.dir = wlens/schema — must not double-delete it
    after wlens/ is already removed."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wlens.yml").write_text(
        textwrap.dedent("""
            adapter:
              kind: dbt
            output:
              dir: wlens/schema
        """).lstrip()
    )
    schema = tmp_path / "wlens" / "schema"
    schema.mkdir(parents=True)
    (schema / "_index.md").write_text("# x\n")
    (tmp_path / "wlens" / ".gitignore").write_text("cache/\nshare/\n")

    # Must not raise FileNotFoundError mid-delete.
    assert main(["clean", "--yes"]) == 0
    assert not (tmp_path / "wlens").exists()
    assert not (tmp_path / "wlens.yml").exists()
