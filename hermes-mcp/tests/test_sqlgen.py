import pytest
from sqlalchemy import create_engine, text

from hermes_mcp.config import load_settings
from hermes_mcp.errors import ConfigError, QueryError
from hermes_mcp.sqlgen import (
    OpenAICompatBackend,
    StubBackend,
    _clean_model_sql,
    ask,
    build_schema_prompt,
    resolve_model,
)


@pytest.fixture()
def engine():
    e = create_engine("sqlite://")
    with e.connect() as c:
        c.execute(text("CREATE TABLE customers (id INTEGER, name TEXT, country TEXT)"))
        c.execute(text("CREATE TABLE orders (id INTEGER, customer_id INTEGER, product_id INTEGER, quantity INTEGER)"))
        for r in [(1, "Alice", "CN"), (2, "Bob", "US")]:
            c.execute(text("INSERT INTO customers VALUES (:a,:b,:c)"), {"a": r[0], "b": r[1], "c": r[2]})
        for r in [(1, 1, 1, 2), (2, 2, 2, 1), (3, 1, 3, 4)]:
            c.execute(text("INSERT INTO orders VALUES (:a,:b,:c,:d)"),
                      {"a": r[0], "b": r[1], "c": r[2], "d": r[3]})
        c.commit()
    return e


RECORD = {"name": "t", "type": "sqlite", "database": ":memory:", "max_rows": 200}
SQL = ("SELECT c.country, SUM(o.quantity) AS qty FROM orders o "
       "JOIN customers c ON c.id=o.customer_id GROUP BY c.country")


def test_ask_end_to_end_with_stub(engine):
    settings = load_settings({})
    backend = StubBackend(SQL, answer="CN 6, US 1")
    r = ask(RECORD, "每国订单量", settings, engine=engine, backend=backend)
    assert r["ok"] is True
    assert r["answer"] == "CN 6, US 1"
    assert r["sql"].lower().startswith("select")
    rows = {row[0]: row[1] for row in r["rows"]}
    assert rows == {"CN": 6, "US": 1}  # Alice 2+4, Bob 1


def test_ask_rejects_generated_write_sql(engine):
    settings = load_settings({})
    backend = StubBackend(sql="DELETE FROM orders")
    with pytest.raises(QueryError):
        ask(RECORD, "x", settings, engine=engine, backend=backend)


def test_ask_handles_no_sql(engine):
    settings = load_settings({})
    backend = StubBackend(sql="NO_SQL: no such data")
    r = ask(RECORD, "火星销量", settings, engine=engine, backend=backend)
    assert r["ok"] is False
    assert "no such" in r["reason"]


def test_build_schema_prompt_has_tables():
    p = build_schema_prompt({"tables": {"orders": [{"name": "id"}], "customers": []}})
    assert "orders" in p and "customers" in p


def test_resolve_model_fallback():
    settings = load_settings({})  # no hermes_config_path, no llm_model
    assert resolve_model(settings) == "glm-5.3-flash"


def test_openai_backend_requires_key():
    with pytest.raises(ConfigError):
        OpenAICompatBackend("", "", "")


def test_clean_model_sql_strips_fences_and_semicolon():
    out = _clean_model_sql("```sql\nSELECT 1;\n```")
    assert out == "SELECT 1"
    assert _clean_model_sql("select 1;") == "select 1"