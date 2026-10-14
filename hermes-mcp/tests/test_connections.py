import pytest
from sqlalchemy import text

from hermes_mcp.connections import _build_url, describe_connection, get_engine
from hermes_mcp.errors import ConnectionResolveError


def test_sqlite_url():
    url = _build_url({"type": "sqlite", "database": "data/sample.db"})
    assert url.startswith("sqlite:///")


def test_mysql_url():
    u = _build_url({"type": "mysql", "host": "h", "username": "u", "password": "pw", "database": "d"})
    assert u.drivername == "mysql+pymysql"
    assert u.password == "pw"


def test_mssql_query_has_odbc_driver():
    u = _build_url({"type": "sqlserver", "host": "h", "database": "d"})
    assert u.query.get("driver") == "ODBC Driver 17 for SQL Server"


def test_build_url_rejects_unknown_type():
    with pytest.raises(ConnectionResolveError):
        _build_url({"type": "mongodb", "database": "x"})


def test_engine_requires_fields():
    with pytest.raises(ConnectionResolveError):
        get_engine({"name": "x", "type": "sqlite"})


def test_sqlite_engine_round_trip(tmp_path):
    db = tmp_path / "t.db"
    with db.open("w"):
        pass
    e = get_engine({"name": "t", "type": "sqlite", "database": str(db)})
    with e.connect() as c:
        assert c.execute(text("SELECT 1")).scalar() == 1


def test_describe_connection_strips_secrets():
    d = describe_connection({"name": "n", "type": "mysql", "host": "h", "username": "u",
                             "password": "SECRET", "database": "d"})
    assert "password" not in d
    assert d["username"] == "u"