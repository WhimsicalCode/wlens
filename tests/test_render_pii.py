"""PII detection and yml tagging."""

from __future__ import annotations

import textwrap
from pathlib import Path

from ruamel.yaml import YAML

from wlens.render.pii import (
    column_is_pii,
    column_name_looks_like_pii,
    pii_column_names,
    scan_and_tag,
)


def test_obvious_pii_names_flagged():
    for name in ("email", "first_name", "last_name", "phone", "ip_address", "password"):
        assert column_name_looks_like_pii(name), name


def test_skip_prefixes_prevent_false_positives():
    # Aggregates / booleans that mention PII concepts are NOT flagged.
    for name in ("is_verified_email", "has_phone", "count_emails", "num_emails"):
        assert not column_name_looks_like_pii(name), name


def test_email_domain_is_not_pii():
    # Deliberately unflagged — see the comment in render/pii.py.
    assert not column_name_looks_like_pii("email_domain")


def test_meta_flag_overrides_name():
    # Non-PII-looking name but meta says otherwise → treated as PII.
    assert column_is_pii("favourite_colour", {"pii": True})
    # PII-looking name with no meta → still PII (safety net).
    assert column_is_pii("email", {})
    # Plain non-PII column stays non-PII.
    assert not column_is_pii("widget_id", {"pii": False})


def test_pii_column_names_accepts_column_like_objects():
    from wlens.adapters.base import Column

    columns = {
        "email": Column(name="email", meta={}),
        "user_id": Column(name="user_id", meta={}),
        "flagged": Column(name="flagged", meta={"pii": True}),
    }
    assert pii_column_names(columns) == {"email", "flagged"}


def test_scan_and_tag_writes_meta_pii(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    yml_path = models / "_x__models.yml"
    yml_path.write_text(textwrap.dedent("""
        models:
          - name: dim_user
            columns:
              - name: user_id
              - name: email
              - name: first_name
                meta:
                  owner: data-team
    """).lstrip())

    scan_and_tag(models, dry_run=False, repo_root=tmp_path)

    reloaded = YAML().load(yml_path.read_text())
    cols = {c["name"]: c for c in reloaded["models"][0]["columns"]}
    assert cols["email"]["meta"]["pii"] is True
    # Existing meta on first_name is merged, not overwritten.
    assert cols["first_name"]["meta"]["pii"] is True
    assert cols["first_name"]["meta"]["owner"] == "data-team"
    # Non-PII column untouched.
    assert "meta" not in cols["user_id"]


def test_scan_and_tag_is_idempotent(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    yml_path = models / "_x__models.yml"
    yml_path.write_text(textwrap.dedent("""
        models:
          - name: dim_user
            columns:
              - name: email
                meta:
                  pii: true
    """).lstrip())

    before = yml_path.read_text()
    scan_and_tag(models, dry_run=False, repo_root=tmp_path)
    after = yml_path.read_text()
    assert before == after
