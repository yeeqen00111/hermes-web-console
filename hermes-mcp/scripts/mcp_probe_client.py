"""Drive hermes-mcp exactly like an MCP client (Hermes) would over stdio.

Proves the tool surface / protocol layer offline: initialize -> list_tools ->
call a few tools on the local sample store. Runs against the local
db_connections.db (was seeded by scripts/make_sample_db.py).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PY = sys.executable  # the python running this script (conda hermes-mcp)


def _text(result) -> str:
    """Extract text content from a CallToolResult."""
    parts = []
    for block in getattr(result, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    if not parts and getattr(result, "structuredContent", None):
        return json.dumps(result.structuredContent, ensure_ascii=False)
    return "\n".join(parts)


async def main():
    params = StdioServerParameters(
        command=PY,
        args=["-m", "hermes_mcp.server"],
        env={"HERMESMCP_DB_KEY": "devkey123"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("== tools ==", names)

            print("\n== call: get_schema_map(sample) ==")
            r = await session.call_tool("get_schema_map", {"connection": "sample"})
            print(_text(r))

            print("\n== call: query(sample, aggregate) ==")
            r = await session.call_tool(
                "query",
                {"connection": "sample",
                 "sql": "SELECT c.country, SUM(o.quantity) AS qty FROM orders o "
                        "JOIN customers c ON c.id=o.customer_id GROUP BY c.country"},
            )
            print(_text(r))

            print("\n== call: query(sample, forbidden write) -> expect isError ==")
            r = await session.call_tool("query", {"connection": "sample", "sql": "DELETE FROM orders"})
            print("isError=", getattr(r, "is_error", None), "|", _text(r))

            print("\n== call: ask(sample, NL question -> stub? real needs key) ==")
            # No HERMESMCP_LLM_* set -> ask should return a clean ConfigError, not crash.
            r = await session.call_tool("ask", {"connection": "sample", "question": "本月订单量"})
            print(_text(r))


if __name__ == "__main__":
    asyncio.run(main())