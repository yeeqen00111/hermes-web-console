import pytest

from hermes_mcp.errors import ConfigError
from hermes_mcp.store import ConnectionStore

KEY = "dev-secret-key-12345"


@pytest.fixture()
def store(tmp_path):
    return ConnectionStore(str(tmp_path / "conn.db"), KEY)


def test_upsert_and_list_no_secrets(store):
    store.upsert("mysql1", type_="mysql", database="shop", host="h",
                 username="u", password="pw123")
    listed = store.list()
    assert len(listed) == 1
    assert listed[0]["name"] == "mysql1"
    assert "password" not in listed[0]
    assert listed[0]["has_password"] is True


def test_password_is_encrypted(store):
    store.upsert("my", type_="mysql", database="d", password="secret")
    import sqlite3
    with sqlite3.connect(store._path) as c:
        cipher = c.execute("SELECT password_cipher FROM db_connections WHERE name='my'").fetchone()[0]
    assert cipher and cipher != "secret"


def test_get_record_decrypts(store):
    store.upsert("my", type_="mysql", database="d", password="secret")
    assert store.get_record("my")["password"] == "secret"


def test_password_none_keeps_existing(store):
    store.upsert("my", type_="mysql", database="d", password="secret")
    store.upsert("my", type_="mysql", database="other", password=None)
    assert store.get_record("my")["password"] == "secret"
    assert store.get_record("my")["database"] == "other"


def test_password_empty_clears(store):
    store.upsert("my", type_="mysql", database="d", password="secret")
    store.upsert("my", type_="mysql", database="d", password="")
    assert store.get_record("my")["password"] == ""


def test_no_key_refuses_password(store):
    no_key = ConnectionStore(store._path, "")  # empty key
    with pytest.raises(ConfigError):
        no_key.upsert("x", type_="mysql", database="d", password="pw")


def test_delete(store):
    store.upsert("my", type_="mysql", database="d")
    assert store.delete("my") is True
    assert store.get_record("my") is None


def test_sqlite_saltless_ok(store):
    store.upsert("sample", type_="sqlite", database="/tmp/x.db", password=None)
    assert store.list()[0]["name"] == "sample"