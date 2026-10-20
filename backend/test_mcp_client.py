# -*- coding: utf-8 -*-
"""mcp_client 单元测试（离线，不连任何服务）。

覆盖阶段 0 实测出来的两种失败形态和「JSON 塞在 text 里」这个约定：
  - 正常：把 text 里的 JSON 解成 dict
  - 形态① ``is_error=True`` → 抛 McpError（正文只有通用文案，没有细节）
  - 形态② ``is_error=False`` + ``ok=false`` → **正常返回**，由调用方判断
  - 契约漂移：text 不是 JSON / 不是对象 → 抛 McpError（不静默吞）
  - 传输层：超时、连接异常 → 抛 McpError
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import mcp_client
from mcp_client import McpError, call_tool


def _result(text: str, is_error: bool = False):
    """仿 CallToolResult：content 是文本块列表，字段名是 snake_case。"""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        structured_content=None,
        is_error=is_error,
    )


def _patch_call(fn):
    """把 _call_once 换成假的（它是 async 的）。"""
    return patch.object(mcp_client, "_call_once", fn)


class McpClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # IsolatedAsyncioTestCase 默认开 debug 模式，会为每个回调打 "Executing <Task...> took"
        # 的慢回调告警，把测试输出淹掉。这些测试本来就快，把阈值抬上去换干净输出。
        import asyncio
        asyncio.get_running_loop().slow_callback_duration = 5.0

    async def test_parses_json_in_text(self):
        async def fake(tool, args):
            self.assertEqual(tool, "list_connections")
            self.assertEqual(args, {})
            return _result('{"connections": [{"name": "a"}], "count": 1}')

        with _patch_call(fake):
            data = await call_tool("list_connections")
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["connections"][0]["name"], "a")

    async def test_empty_body_is_empty_dict(self):
        async def fake(tool, args):
            return _result("")

        with _patch_call(fake):
            self.assertEqual(await call_tool("ping"), {})

    async def test_tool_error_raises(self):
        """形态①：is_error=True → 抛错（且带上通用文案，虽然没什么用）。"""
        async def fake(tool, args):
            return _result("Error executing tool query", is_error=True)

        with _patch_call(fake):
            with self.assertRaises(McpError) as ctx:
                await call_tool("query", {"connection": "x", "sql": "SELECT 1"})
        self.assertIn("query", str(ctx.exception))

    async def test_ok_false_is_returned_not_raised(self):
        """形态②：ok=false 是业务结论，不是异常——必须原样返回给调用方。"""
        async def fake(tool, args):
            return _result('{"ok": false, "removed": "no_such"}')

        with _patch_call(fake):
            data = await call_tool("remove_connection", {"name": "no_such"})
        self.assertIs(data["ok"], False)

    async def test_non_json_body_raises(self):
        async def fake(tool, args):
            return _result("this is not json")

        with _patch_call(fake):
            with self.assertRaises(McpError) as ctx:
                await call_tool("list_connections")
        self.assertIn("不是 JSON", str(ctx.exception))

    async def test_json_array_body_raises(self):
        """返回是数组而不是对象——契约漂移，不能当 dict 用。"""
        async def fake(tool, args):
            return _result("[1, 2, 3]")

        with _patch_call(fake):
            with self.assertRaises(McpError) as ctx:
                await call_tool("list_connections")
        self.assertIn("不是对象", str(ctx.exception))

    async def test_transport_error_raises(self):
        async def fake(tool, args):
            raise ConnectionRefusedError("connection refused")

        with _patch_call(fake):
            with self.assertRaises(McpError) as ctx:
                await call_tool("list_connections")
        self.assertIn("连不上问数服务", str(ctx.exception))

    async def test_timeout_raises(self):
        import asyncio

        async def fake(tool, args):
            await asyncio.sleep(5)

        with _patch_call(fake):
            with self.assertRaises(McpError) as ctx:
                await call_tool("list_connections", timeout=0.05)
        self.assertIn("超时", str(ctx.exception))

    async def test_arguments_default_to_empty(self):
        seen = {}

        async def fake(tool, args):
            seen.update(args)
            return _result("{}")

        with _patch_call(fake):
            await call_tool("ping")
        self.assertEqual(seen, {})


if __name__ == "__main__":
    unittest.main()
