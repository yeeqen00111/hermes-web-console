"""Multi-dialect connection factory.

Builds a SQLAlchemy engine from a stored connection record and hands back a
read-only connection (never COMMITs; PostgreSQL additionally opens each session
marked read-only). SELECT-only account remains the operator's responsibility.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import URL, create_engine

from hermes_mcp.errors import ConnectionResolveError

DIALECTS = {"sqlite", "mysql", "postgresql", "sqlserver", "oracle"}
_REQUIRED = {"name", "type", "database"}

_DRIVERS = {
    "mysql": "mysql+pymysql",
    "postgresql": "postgresql+psycopg",
    "sqlserver": "mssql+pyodbc",
    "oracle": "oracle+oracledb",
}


def _build_url(cfg: dict):
    kind = cfg.get("type", "").lower()
    if kind not in DIALECTS:
        raise ConnectionResolveError(f"unsupported database type: {kind!r}")

    if kind == "sqlite":
        p = Path(cfg.get("database", ""))
        return f"sqlite:///{p.resolve().as_posix()}"

    driver = _DRIVERS[kind]
    query = {}
    if kind == "sqlserver":
        query["driver"] = "ODBC Driver 17 for SQL Server"
    return URL.create(
        drivername=driver,
        username=cfg.get("username") or None,
        password=cfg.get("password") or None,
        host=cfg.get("host") or None,
        port=int(cfg["port"]) if cfg.get("port") else None,
        database=cfg.get("database") or None,
        query=query or None,
    )


def get_engine(cfg: dict):
    """Build a SQLAlchemy engine for a connection record."""
    for k in _REQUIRED:
        if cfg.get(k) in (None, ""):
            raise ConnectionResolveError(f"connection record missing '{k}'")
    url = _build_url(cfg)
    connect_args = {}
    engine_kwargs = {"future": True}
    if cfg.get("type", "").lower() == "postgresql":
        # open every session read-only at the transaction level
        connect_args["options"] = "-c default_transaction_read_only=on"
    if connect_args:
        engine_kwargs["connect_args"] = connect_args
    return create_engine(url, **engine_kwargs)


def describe_connection(cfg: dict) -> dict:
    """Safe, non-secret summary for list/display."""
    return {
        "name": cfg.get("name"),
        "type": cfg.get("type"),
        "host": cfg.get("host"),
        "port": cfg.get("port"),
        "database": cfg.get("database"),
        "username": cfg.get("username"),
        "read_only": bool(cfg.get("read_only", True)),
        "max_rows": cfg.get("max_rows"),
    }