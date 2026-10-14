# hermes-mcp

Read-only, multi-database **MCP server** that lets Hermes (or users in the Hermes
dialog) ask questions about your databases in natural language ("Ask-your-data").

*Users ask in natural language ➜ Hermes turns intent into a call to these tools
(or the server's `ask` generates SQL) ➜ this server validates, executes read-only,
and returns data + the SQL used.*

- **Read-only by design** — syntax whitelist, single-statement guard, transaction
  always ROLLBACK (never COMMIT), row caps + timeouts, encrypted-at-rest passwords,
  and a recommended `SELECT-only` DB account.
- **Multi-dialect** — MySQL / PostgreSQL / SQL Server / Oracle (+ local SQLite
  for offline dev) behind one SQLAlchemy layer.
- **Shared connection registry** — connections live in one SQLite store
  (`db_connections.db`) shared with the management web-console.

## Status (M0, offline)

Skeleton + read-only core are done and tested **against a local SQLite sample DB**,
no real database or Hermes wiring yet.

| Tool | Status |
|---|---|
| `ping` | ✅ |
| `list_connections` / `register_connection` / `remove_connection` | ✅ |
| `query` (read-only, capped, ROLLBACK) | ✅ |
| `get_schema_map` / `describe_table` / `list_schemas` | ✅ |
| `ask` (NL→SQL generation) | ⏳ M5 |

## Setup (conda)

```bash
conda create -n hermes-mcp python=3.10 -y
conda activate hermes-mcp
pip install -e ".[test]"
```

## Build the offline sample DB + register it

```bash
python scripts/make_sample_db.py
# -> data/sample.db ; connection "sample" registered in db_connections.db
```

## Test

```bash
pytest
```

## Run the MCP server (stdio)

```bash
python -m hermes_mcp.server
# or HTTP later:  MCP_TRANSPORT=http python -m hermes_mcp.server
```

## Env

| Var | Purpose |
|---|---|
| `HERMESMCP_DB_KEY` | key to encrypt stored DB passwords (required to save a password) |
| `HERMESMCP_DB_CONFIG` | path to shared `db_connections.db` (default `<root>/db_connections.db`) |
| `HERMESMCP_SAMPLE_DB` | path to the offline sample DB (default `<root>/data/sample.db`) |
| `HERMESMCP_CONFIG_PATH` | optional path to Hermes `config.yaml` (for `ask` model, M5) |
| `HERMESMCP_LLM_*` | generated-SQL model endpoint (M5) |
| `MCP_TRANSPORT` | `stdio` (default) or `http` |

Design: see [docs/数据库智能问数-完整方案.md](docs/数据库智能问数-完整方案.md).