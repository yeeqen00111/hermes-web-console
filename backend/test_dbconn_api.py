# -*- coding: utf-8 -*-
"""智能问数 · 数据源接口测试（**离线**：MCP 用替身，不连任何数据库、不连真 MCP）。

替身 `_FakeMcp` 的行为**照抄实测**（2026-10-20 探针，见 backend/CLAUDE.md）：
  - `register_connection` 是 **upsert**，且**什么都不校验**（所以完整性校验只能由我们做）
  - `password` 三态：省略 = 保持 ｜ `""` = 清除 ｜ 有值 = 存
  - `remove_connection` 删不存在的返回 `{ok: false}`（**不是**错误）
  - `list_connections` 给 `describe_connection` 的形状：**无 id / 时间戳**，密码只给布尔

覆盖重点：
  - **密码永不回显**（明文、密文都不出现在任何响应里）
  - 三态语义（与 Hermes 渠道的 env 语义**不同**）
  - 类型决定字段（sqlite 只要路径）+ 完整性校验（MCP 不做，报错要可读）
  - 404 / 409 由我们判（MCP 只会 upsert、只会 ok:false）
  - MCP 连不上 → 502（不是 500）
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from mcp_client import McpError

_form = {
    "name": "订单库", "type": "mysql", "host": "10.0.0.5", "port": 3306,
    "database": "shop", "username": "reporter", "max_rows": 200,
}


class _FakeMcp:
    """hermes-mcp 的离线替身（行为照抄实测）。"""

    def __init__(self):
        self.rows = {}

    async def __call__(self, tool, arguments=None, **kw):
        args = dict(arguments or {})
        if tool == "list_connections":
            return {"connections": [self._describe(r) for r in self.rows.values()]}
        if tool == "register_connection":
            name = args["name"]
            old = self.rows.get(name, {})
            row = {"name": name}
            for k in ("type", "host", "port", "database", "username", "max_rows"):
                row[k] = args.get(k)
            row["read_only"] = args.get("read_only", True)
            # 密码三态：省略=保持 / ""=清除 / 有值=存
            if "password" not in args:
                row["password"] = old.get("password", "")
            else:
                row["password"] = args["password"]
            self.rows[name] = row
            return {"ok": True, "registered": name}
        if tool == "remove_connection":
            name = args["name"]
            existed = name in self.rows
            self.rows.pop(name, None)
            return {"ok": existed, "removed": name}
        raise AssertionError(f"测试未预期的工具：{tool}")

    @staticmethod
    def _describe(r):
        """`describe_connection` 的形状：无 id / 时间戳。"""
        return {
            "name": r["name"], "type": r.get("type"), "host": r.get("host"),
            "port": r.get("port"), "database": r.get("database"),
            "username": r.get("username"),
            "read_only": bool(r.get("read_only", True)),
            "max_rows": r.get("max_rows"),
            "has_password": bool(r.get("password")),
            "status": None, "last_error": None,
        }


class DbConnApiTests(unittest.TestCase):
    def setUp(self):
        self.mcp = _FakeMcp()
        for p in (patch("dbconn.call_tool", self.mcp), patch("config.APP_AUTH", False)):
            p.start()
            self.addCleanup(p.stop)

        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    # ── helpers ──

    def _post(self, **over):
        body = dict(_form)
        body.update(over)
        return self.client.post("/api/db-connections", json=body)

    def _put(self, name="订单库", **over):
        body = {k: v for k, v in _form.items() if k != "name"}
        body.update(over)
        return self.client.put(f"/api/db-connections/{name}", json=body)

    def _list(self):
        return self.client.get("/api/db-connections").json()["connections"]

    # ── 读 ──

    def test_empty_state_returns_empty_list(self):
        r = self.client.get("/api/db-connections")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"connections": [], "count": 0})

    def test_list_shape_has_no_id_or_timestamps(self):
        """MCP 不回传 id/created_at/updated_at → 对外契约里就没有它们。"""
        self._post(password="p")
        out = self._list()[0]
        for gone in ("id", "created_at", "updated_at"):
            self.assertNotIn(gone, out)
        self.assertEqual(out["name"], "订单库")
        self.assertNotIn("password", out)
        self.assertTrue(out["has_password"])

    def test_types_catalog_is_served(self):
        """类型目录：前端渲染表单、本层做校验，共用这一份。"""
        r = self.client.get("/api/db-connections/types")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        ids = [t["id"] for t in body["types"]]
        self.assertIn("mysql", ids)
        self.assertIn("sqlite", ids)
        self.assertNotIn("api", ids, "api 类型本轮不做：读接口不回传它的字段，编辑不了")
        self.assertEqual(body["field_labels"]["host"], "主机")
        mysql = next(t for t in body["types"] if t["id"] == "mysql")
        sqlite = next(t for t in body["types"] if t["id"] == "sqlite")
        self.assertEqual(mysql["default_port"], 3306)
        self.assertIsNone(sqlite["default_port"])

    # ── 新增 ──

    def test_create_then_list(self):
        r = self._post(password="p@ss")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "name": "订单库"})
        out = self._list()[0]
        self.assertEqual(out["name"], "订单库")
        self.assertEqual(out["port"], 3306)
        self.assertTrue(out["read_only"])
        self.assertEqual(out["max_rows"], 200)
        self.assertTrue(out["has_password"])

    def test_create_duplicate_is_409(self):
        self._post(password="p")
        r = self._post(password="p")
        self.assertEqual(r.status_code, 409)
        self.assertIn("已存在", r.json()["detail"])
        self.assertEqual(len(self._list()), 1)

    def test_create_requires_password_for_relational(self):
        r = self.client.post("/api/db-connections", json=dict(_form))   # 不带 password
        self.assertEqual(r.status_code, 400)
        self.assertIn("密码", r.json()["detail"])
        self.assertEqual(self._list(), [])

    def test_sqlite_needs_no_password_and_clears_other_fields(self):
        """sqlite 只要路径；host/port/username 一律清空（免得残留上一次填的值）。"""
        r = self._post(name="本地文件库", type="sqlite", database="D:/data/x.db",
                       host="ignored", port=1234, username="nobody")
        self.assertEqual(r.status_code, 200)
        out = self._list()[0]
        self.assertEqual(out["type"], "sqlite")
        self.assertEqual(out["database"], "D:/data/x.db")
        self.assertEqual(out["host"], "")
        self.assertIsNone(out["port"])
        self.assertEqual(out["username"], "")
        self.assertFalse(out["has_password"])

    # ── 修改 ──

    def test_update_missing_is_404(self):
        r = self._put(name="不存在的库", password="p")
        self.assertEqual(r.status_code, 404)

    def test_update_keeps_name_and_changes_fields(self):
        self._post(password="p")
        r = self._put(database="shop2", max_rows=50)
        self.assertEqual(r.status_code, 200)
        out = self._list()[0]
        self.assertEqual(out["name"], "订单库")
        self.assertEqual(out["database"], "shop2")
        self.assertEqual(out["max_rows"], 50)

    def test_read_only_is_forced_true(self):
        """前端传 false 也没用——本层只写只读配置，不提供改写开关。"""
        self._post(password="p", read_only=False)
        self.assertTrue(self._list()[0]["read_only"])

    # ── 删除 ──

    def test_delete_then_list_is_empty(self):
        self._post(password="p")
        r = self.client.delete("/api/db-connections/订单库")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "name": "订单库"})
        self.assertEqual(self._list(), [])

    def test_delete_missing_is_404(self):
        """MCP 删不存在只回 ok:false（不是错误）——404 是我们翻的。"""
        r = self.client.delete("/api/db-connections/不存在的库")
        self.assertEqual(r.status_code, 404)

    # ── 密码 ──

    def test_password_three_states(self):
        self._post(password="first")
        self.assertEqual(self.mcp.rows["订单库"]["password"], "first")

        self._put(database="shop2")                       # 不发 password → 保持
        self.assertEqual(self.mcp.rows["订单库"]["password"], "first")

        self._put(password="second")                      # 非空 → 换新
        self.assertEqual(self.mcp.rows["订单库"]["password"], "second")

        self._put(password="")                            # 空串 → 清除
        self.assertEqual(self.mcp.rows["订单库"]["password"], "")
        self.assertFalse(self._list()[0]["has_password"])

    def test_never_echoes_password_anywhere(self):
        sentinel = "p@ssw0rd-明文哨兵"
        r = self._post(password=sentinel)
        self.assertNotIn(sentinel, r.text)
        self.assertNotIn(sentinel, self.client.get("/api/db-connections").text)
        self.assertNotIn(sentinel, self.client.get("/api/db-connections/types").text)

    # ── 校验（MCP 不做，所以是这里唯一能给可读报错的地方）──

    def test_rejects_bad_input(self):
        cases = [
            ({"name": ""}, "名称"),
            ({"name": "a" * 65}, "名称"),
            ({"name": "bad name!"}, "名称"),
            ({"type": "api"}, "不支持"),          # 本轮不做
            ({"type": "nosuchtype"}, "不支持"),   # MCP 会照收，我们必须拦
            ({"type": ""}, "不支持"),
            ({"host": ""}, "主机"),
            ({"host": "10.0.0.5/../x"}, "主机"),
            ({"port": 0}, "端口"),
            ({"port": 70000}, "端口"),
            ({"database": ""}, "库名"),
            ({"username": ""}, "账号"),
            ({"max_rows": 0}, "行数"),
            ({"max_rows": 1001}, "行数"),
        ]
        for over, expect in cases:
            with self.subTest(over=over):
                r = self._post(password="p", **over)
                self.assertEqual(r.status_code, 400, f"{over} 应被拒")
                self.assertIn(expect, r.json()["detail"])
        self.assertEqual(self._list(), [], "被拒的请求不该留下任何记录")

    # ── 过渡期兼容别名已移除 ──

    def test_upsert_alias_is_gone(self):
        """`PUT /api/db-connections`（不带 {name}）是前端切换期的临时别名，2026-10-20 已删。

        钉住它：新增走 POST、修改走 PUT /{name}，别再让它悄悄长回来——
        "无 {name} 的 upsert" 和 "带 {name} 的 404 语义" 同时存在会让契约变得含糊。
        """
        r = self.client.put("/api/db-connections", json=dict(_form, password="p"))
        self.assertIn(r.status_code, (404, 405))
        self.assertEqual(self._list(), [])

    # ── 依赖不可用 ──

    def test_mcp_unreachable_is_502(self):
        async def boom(tool, arguments=None, **kw):
            raise McpError("连不上问数服务（http://x:9123/mcp）：refused")

        with patch("dbconn.call_tool", boom):
            for method, path in (("get", "/api/db-connections"),
                                 ("post", "/api/db-connections"),
                                 ("delete", "/api/db-connections/订单库")):
                with self.subTest(path=path):
                    fn = getattr(self.client, method)
                    r = fn(path, json=dict(_form, password="p")) if method == "post" else fn(path)
                    self.assertEqual(r.status_code, 502)
                    self.assertIn("连不上问数服务", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
