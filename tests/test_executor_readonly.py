"""Read-only guard — must accept SELECTs and reject everything else."""

from __future__ import annotations

import pytest

from wlens.executor.base import ReadOnlyViolation, assert_read_only


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select count(*) from prod.dim_widget",
        "select 1;",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "  -- a comment\n  SELECT 1\n",
        "/* header */ select 1 /* trailing */",
    ],
)
def test_accepts_selects(sql):
    assert_read_only(sql)  # should not raise


@pytest.mark.parametrize(
    "sql, reason",
    [
        ("", "empty SQL"),
        ("   ", "empty SQL"),
        ("DROP TABLE users", "DROP"),
        ("INSERT INTO x VALUES (1)", "INSERT"),
        ("UPDATE x SET a = 1", "UPDATE"),
        ("DELETE FROM x", "DELETE"),
        ("TRUNCATE TABLE x", "TRUNCATE"),
        ("COPY t FROM 's3://foo'", "COPY"),
        ("GRANT ALL ON x TO public", "GRANT"),
        ("CREATE TABLE foo AS SELECT 1", "CREATE"),
        ("ALTER TABLE x ADD COLUMN y int", "ALTER"),
        ("VACUUM ANALYZE", "VACUUM"),
        ("SELECT 1; DROP TABLE x", "multiple statements"),
        ("SELECT 1; SELECT 2", "multiple statements"),
    ],
)
def test_rejects_mutations_and_multi_statements(sql, reason):
    with pytest.raises(ReadOnlyViolation):
        assert_read_only(sql)


def test_rejects_injection_via_cte():
    # Belt-and-suspenders: DELETE hidden inside an otherwise-valid WITH query.
    with pytest.raises(ReadOnlyViolation):
        assert_read_only("WITH x AS (SELECT 1) DELETE FROM t USING x WHERE t.id = x.id")


def test_rejects_set_and_transaction_control():
    for sql in ("SET timezone = 'UTC'", "BEGIN", "COMMIT", "ROLLBACK"):
        with pytest.raises(ReadOnlyViolation):
            assert_read_only(sql)
