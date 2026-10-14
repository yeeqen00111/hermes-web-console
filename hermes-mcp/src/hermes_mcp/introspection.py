"""Schema discovery tools (list_schemas / get_schema_map / describe_table).

Uses the dialect-agnostic SQLAlchemy Inspector so the same code works across
MySQL / PostgreSQL / SQL Server / Oracle / SQLite.
"""
from __future__ import annotations

from sqlalchemy import inspect

from hermes_mcp.errors import IntrospectionError


def list_schemas(engine) -> list[str]:
    try:
        return list(inspect(engine).get_schema_names())
    except Exception as e:
        raise IntrospectionError(f"cannot list schemas on this connection") from e


def get_schema_map(engine, schema: str | None = None, max_tables: int = 100) -> dict:
    """Return {schema: {table: [columns...]}} feeding the NL->SQL context."""
    insp = inspect(engine)
    try:
        names = insp.get_table_names(schema=schema) if schema else insp.get_table_names()
    except Exception as e:
        raise IntrospectionError(f"cannot read tables") from e
    if max_tables and len(names) > max_tables:
        names = names[:max_tables]
    tables: dict = {}
    for t in names:
        cols = []
        try:
            for c in insp.get_columns(t, schema=schema):
                cols.append({
                    "name": c["name"],
                    "type": str(c.get("type", "")),
                    "nullable": bool(c.get("nullable", True)),
                })
        except Exception:
            pass
        tables[t] = cols
    return {"schema": schema or "", "tables": tables, "table_count": len(tables)}


def describe_table(engine, table: str, schema: str | None = None) -> dict:
    insp = inspect(engine)
    columns = [
        {
            "name": c["name"],
            "type": str(c.get("type", "")),
            "nullable": bool(c.get("nullable", True)),
        }
        for c in insp.get_columns(table, schema=schema)
    ]
    pk = None
    try:
        pk = insp.get_pk_constraint(table, schema=schema).get("constrained_columns")
    except Exception:
        pass
    fks = []
    try:
        for fk in insp.get_foreign_keys(table, schema=schema):
            fks.append({
                "columns": fk.get("constrained_columns", []),
                "referred_table": fk.get("referred_table"),
                "referred_columns": fk.get("referred_columns", []),
            })
    except Exception:
        pass
    return {"table": table, "schema": schema or "", "columns": columns, "primary_key": pk, "foreign_keys": fks}