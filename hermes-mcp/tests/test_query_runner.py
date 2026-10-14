import pytest
from sqlalchemy import create_engine, text

from hermes_mcp.errors import QueryError, ReadOnlyViolation
from hermes_mcp.query_runner import run_readonly


@pytest.fixture()
def engine():
    e = create_engine("sqlite://")
    with e.connect() as c:
        c.execute(text("CREATE TABLE items (id INTEGER, qty INTEGER)"))
        seed = [(1, 10), (2, 20), (3, 30), (4, 40), (5, 50)]
        for row in seed:
            c.execute(text("INSERT INTO items VALUES (:a,:b)"), {"a": row[0], "b": row[1]})
        c.commit()
    return e


def test_runs_select(engine):
    r = run_readonly(engine, "SELECT id, qty FROM items ORDER BY id")
    assert r["columns"] == ["id", "qty"]
    assert len(r["rows"]) == 5
    assert r["row_count"] == 5
    assert r["truncated"] is False


def test_truncates_at_max_rows(engine):
    r = run_readonly(engine, "SELECT * FROM items", max_rows=2)
    assert r["row_count"] == 2
    assert r["truncated"] is True


def test_rejects_write(engine):
    with pytest.raises(ReadOnlyViolation):
        run_readonly(engine, "DELETE FROM items")


def test_write_never_persists(engine):
    # an INSERT that legitimately does not pass validation must not change data
    with pytest.raises(ReadOnlyViolation):
        run_readonly(engine, "INSERT INTO items VALUES (99, 99)")
    with engine.connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM items")).scalar()
    assert n == 5


def test_rolls_back_after_readonly_execution(engine):
    """The runner never commits; data stays at original count across runs."""
    run_readonly(engine, "SELECT * FROM items")
    with engine.connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM items")).scalar()
    assert n == 5