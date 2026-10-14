"""Drive hermes-mcp over HTTP (streamable-http) exactly like a remote MCP client.

    python scripts/http_probe_client.py [url]
    default url: http://127.0.0.1:9123/mcp
"""
from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def _text(result) -> str:
    parts = []
    for block in getattr(result, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    if not parts and getattr(result, "structuredContent", None):
        return json.dumps(result.structuredContent, ensure_ascii=False)
    return "\n".join(parts)


async def main(url):
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("== tools ==", sorted(t.name for t in tools.tools))

            r = await session.call_tool(
                "query",
                {"connection": "sample",
                 "sql": "SELECT c.country, SUM(o.quantity) AS qty FROM orders o "
                        "JOIN customers c ON c.id=o.customer_id GROUP BY c.country"},
            )
            print("== query ==", _text(r))

            r = await session.call_tool("query", {"connection": "sample", "sql": "DELETE FROM orders"})
            print("== forbidden write (expect error) ==", getattr(r, "is_error", None), _text(r))

            # 问数冒烟：对 mysql_local（本机 MySQL 联调库）跑一条真实 ask。
            # 需要服务进程带 HERESMCP_DB_KEY + HERESMCP_LLM_BASE_URL/_API_KEY/_MODEL。
            r = await session.call_tool(
                "ask",
                {"connection": "mysql_local", "question": "武汉的客户一共有多少位？"},
            )
            print("== ask (mysql_local, expect generated SQL + answer) ==")
            if getattr(r, "is_error", None):
                print("  ERROR:", _text(r)[:600])
            else:
                print("  result:", _text(r))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9123/mcp"))