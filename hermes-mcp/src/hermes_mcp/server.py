"""MCPServer (mcp SDK 2.x) exposing read-only database tools to Hermes.

M0 tool surface:
    ping              -- liveness probe
    list_connections  -- registered connections (no secrets)
    register_connection -- add/update a connection (password optional, encrypted)
    remove_connection -- drop a connection
    list_schemas      -- schemas on a connection
    get_schema_map    -- table/column map (NL->SQL context)
    describe_table    -- single table detail
    query             -- run a read-only SELECT (validated + ROLLBACK)
    ask               -- stub; NL->SQL generation (M5)

Errors: HermesMCPError exceptions propagate to the caller as tool errors with
a clean ("code", "message") dict (connection resolver / security / store).
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from hermes_mcp import connections, introspection, query_runner
from hermes_mcp import sqlgen as _sqlgen
from hermes_mcp.config import Settings, load_settings
from hermes_mcp.errors import HermesMCPError
from hermes_mcp.store import ConnectionStore

app = MCPServer("hermes-mcp", version="0.1.0")

_settings: Settings | None = None
_store: ConnectionStore | None = None


def _state():
    global _settings, _store
    if _settings is None:
        _settings = load_settings()
    if _store is None:
        _store = ConnectionStore(_settings.resolved_db_config_path, _settings.enc_key)
    return _settings, _store


def _engine(name: str):
    _, store = _state()
    rec = store.get_record(name)
    if rec is None:
        # 联调便捷回落：调用方传错/漏传连接名时，若注册表恰好只有一个连接，
        # 就自动用它（避免对方仍用已删掉的 sample 造成 unknown connection）。
        records = store.list()
        if len(records) == 1:
            rec = store.get_record(records[0]["name"])
        else:
            from hermes_mcp.errors import ConnectionResolveError
            raise ConnectionResolveError(f"unknown connection: {name!r}")
        from warnings import warn
        warn(f"connection {name!r} not found; fell back to sole connection {rec['name']!r}")
    return connections.get_engine(rec), rec


# ---------- tools ----------


@app.tool(description="Liveness probe for the hermes-mcp server.")
def ping() -> dict:
    return {"ok": True, "service": "hermes-mcp"}


@app.tool(description="List registered database connections (no secrets; includes has_password and status).")
def list_connections() -> dict:
    _, store = _state()
    return {"connections": store.list()}


@app.tool(description=(
    "Register or update a database connection. type in {sqlite,mysql,postgresql,"
    "sqlserver,oracle}. Omit password to keep the existing value, pass empty to "
    "clear it, pass non-empty to store encrypted (requires HERMESMCP_DB_KEY)."))
def register_connection(
    name: str,
    type: str,
    database: str,
    password: str | None = None,
    host: str = "",
    port: int | None = None,
    username: str = "",
    read_only: bool = True,
    max_rows: int | None = 200,
) -> dict:
    _, store = _state()
    store.upsert(
        name, type_=type, database=database, password=password,
        host=host, port=port, username=username,
        read_only=read_only, max_rows=max_rows,
    )
    return {"ok": True, "registered": name}


@app.tool(description="Remove a database connection by name.")
def remove_connection(name: str) -> dict:
    _, store = _state()
    ok = store.delete(name)
    return {"ok": ok, "removed": name}


@app.tool(description="List the schemas/databases visible on a connection (read-only).")
def list_schemas(connection: str) -> dict:
    engine, _ = _engine(connection)
    return {"connection": connection, "schemas": introspection.list_schemas(engine)}


@app.tool(description="Return a table->columns map for a connection/schema (context for NL->SQL).")
def get_schema_map(connection: str, schema: str | None = None, max_tables: int = 100) -> dict:
    engine, _ = _engine(connection)
    return introspection.get_schema_map(engine, schema=schema, max_tables=max_tables)


@app.tool(description="Describe a single table: columns, primary key, foreign keys.")
def describe_table(connection: str, table: str, schema: str | None = None) -> dict:
    engine, _ = _engine(connection)
    return introspection.describe_table(engine, table, schema=schema)


@app.tool(description=(
    "Run a read-only SELECT against a connection. Only single read-only "
    "statements (SELECT/WITH/SHOW/EXPLAIN/DESCRIBE) are allowed; results are "
    "capped at max_rows; the transaction is always rolled back so nothing is "
    "written. Returns {columns, rows, truncated, row_count, sql, elapsed_ms}."))
def query(connection: str, sql: str, max_rows: int | None = None) -> dict:
    engine, rec = _engine(connection)
    cap = max_rows or rec.get("max_rows") or 200
    return query_runner.run_readonly(engine, sql, max_rows=cap)


@app.tool(description=(
    "Ask a natural-language data question about a connection; the server "
    "generates read-only SQL (schema + configured model), runs it, and returns "
    "{answer, sql, columns, rows, truncated, row_count}. Requires "
    "HERMESMCP_LLM_BASE_URL/HERMESMCP_LLM_API_KEY to generate SQL."))
def ask(connection: str, question: str) -> dict:
    settings, store = _state()
    rec = store.get_record(connection)
    if rec is None:
        from hermes_mcp.errors import ConnectionResolveError
        raise ConnectionResolveError(f"unknown connection: {connection!r}")
    return _sqlgen.ask_from_connection(rec, question, settings)


def main():
    settings = load_settings()
    transport = settings.transport or "stdio"
    if transport == "http":
        app.run(transport="streamable-http")
    else:
        app.run()  # stdio


if __name__ == "__main__":
    main()