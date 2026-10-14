"""hermes-mcp: read-only multi-database MCP server for Hermes Ask-your-data.

Delivers read-only access to one or more databases (MySQL / PostgreSQL /
SQL Server / Oracle / SQLite-local) as MCP tools consumed by the Hermes agent:
users ask in natural language; Hermes (or this server via `ask`) turns it into
SQL; this server enforces read-only and returns data.

Public, safe entry points:
    hermes_mcp.store.ConnectionStore      -- shared SQLite connection registry
    hermes_mcp.security.validate_readonly -- syntax whitelist / guard
    hermes_mcp.query_runner.run_readonly  -- ROLLBACK-scoped SELECT runner
    hermes_mcp.server.app                 -- FastMCP application
"""

__version__ = "0.1.0"