import pytest
from sqlalchemy import create_engine, text

from hermes_mcp.introspection import describe_table, get_schema_map, list_schemas


@pytest.fixture()
def engine():
    e = create_engine("sqlite://")
    with e.connect() as c:
        c.execute(text("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, country TEXT)"))
        c.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id))"))
        c.commit()
    return e


def test_schema_map_lists_tables_and_columns(engine):
    m = get_schema_map(engine)
    assert set(m["tables"]) == {"customers", "orders"}
    cols = {c["name"] for c in m["tables"]["customers"]}
    assert {"id", "name", "country"} <= cols
    assert m["table_count"] == 2


def test_describe_table_columns_and_pk(engine):
    d = describe_table(engine, "customers")
    assert {c["name"] for c in d["columns"]} == {"id", "name", "country"}
    assert d["primary_key"] == ["id"]


def test_list_schemas(engine):
    s = list_schemas(engine)
    assert isinstance(s, list) and len(s) >= 1