# -*- coding: utf-8 -*-
"""智能问数 · 知识库 + SQL 模板接口测试（**离线**：MCP 用替身）。

替身行为照抄 hermes-mcp 的实测/源码：
  - `list(connection)`：传 connection → 返回**该数据源 + 全局 `*`**；不传 → 全部
  - `create_*` → `{ok: true, id}`
  - `update_*` 对不存在的 id **抛异常**（真 MCP 只回通用文案，所以本层必须先查再改）
  - `delete_*` 删不存在 → `{ok: false}`（**不是**错误）

重点覆盖「MCP 不做、必须由我们补」的三件事：
  1. 不存在的 id → 404（而不是把 MCP 的通用错误当 502 抛出去）
  2. SQL 模板必须是只读语句、`params` 必须是 JSON 对象 → 可读的 400
  3. 过滤语义：选某个数据源时，全局条目也要出现
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from mcp_client import McpError

_KNOW = {"term": "活跃客户", "definition": "近 90 天内至少下过 1 单且没有退款的客户",
         "connection": "mysql_local", "anchor": "customers", "note": ""}
_TPL = {"question": "武汉的客户一共有多少位？",
        "sql": "SELECT COUNT(*) FROM customers WHERE city = '武汉'",
        "name": "武汉客户数", "connection": "mysql_local", "params": "{}", "note": ""}


class _FakeAssets:
    """hermes-mcp 知识库/模板工具的离线替身。"""

    def __init__(self):
        self.entries, self.templates = {}, {}
        self.seq = {"knowledge": 0, "sql": 0}

    def _add(self, bucket, args, keys):
        self.seq[bucket] += 1
        i = self.seq[bucket]
        store = self.entries if bucket == "knowledge" else self.templates
        store[i] = {"id": i, **{k: args.get(k, "") for k in keys}}
        return {"ok": True, "id": i}

    @staticmethod
    def _filtered(store, connection):
        rows = list(store.values())
        if connection:
            rows = [r for r in rows if r["connection"] in (connection, "*")]
        return rows

    async def __call__(self, tool, arguments=None, **kw):
        a = dict(arguments or {})
        if tool == "list_knowledge_entries":
            return {"entries": self._filtered(self.entries, a.get("connection", ""))}
        if tool == "search_knowledge":
            conn = a.get("connection") or "*"
            q = a["query"]
            hits = [r for r in self.entries.values()
                    if r["connection"] in (conn, "*")
                    and (q in (r.get("term") or "") or q in (r.get("definition") or ""))]
            return {"hits": hits[: a.get("limit", 8)]}
        if tool == "create_knowledge_entry":
            return self._add("knowledge", a, ("connection", "term", "definition", "anchor", "note"))
        if tool == "update_knowledge_entry":
            row = self.entries[a["id"]]           # KeyError = 本层没先查，是 bug
            for k in ("connection", "term", "definition", "anchor", "note"):
                if k in a:
                    row[k] = a[k]
            return {"ok": True, "id": a["id"]}
        if tool == "delete_knowledge_entry":
            return {"ok": self.entries.pop(a["id"], None) is not None}

        if tool == "list_sql_templates":
            return {"templates": self._filtered(self.templates, a.get("connection", ""))}
        if tool == "create_sql_template":
            return self._add("sql", a, ("connection", "question", "sql", "name", "params", "note"))
        if tool == "update_sql_template":
            row = self.templates[a["id"]]
            for k in ("connection", "question", "sql", "name", "params", "note"):
                if k in a:
                    row[k] = a[k]
            return {"ok": True, "id": a["id"]}
        if tool == "delete_sql_template":
            return {"ok": self.templates.pop(a["id"], None) is not None}

        raise AssertionError(f"测试未预期的工具：{tool}")


class AssetsApiTests(unittest.TestCase):
    def setUp(self):
        self.mcp = _FakeAssets()
        for p in (patch("assets.call_tool", self.mcp), patch("config.APP_AUTH", False)):
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _know(self, **over):
        return self.client.post("/api/knowledge-entries", json={**_KNOW, **over})

    def _tpl(self, **over):
        return self.client.post("/api/sql-templates", json={**_TPL, **over})

    # ── 知识库：增删改查 ──

    def test_knowledge_empty(self):
        r = self.client.get("/api/knowledge-entries")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"entries": [], "count": 0})

    def test_knowledge_create_then_list(self):
        r = self._know()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["ok"], True)
        entries = self.client.get("/api/knowledge-entries").json()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["term"], "活跃客户")

    def test_knowledge_filter_includes_global(self):
        """选某个数据源时，**全局条目也要出现**（MCP 的 list 就是 connection IN (x, '*')）。"""
        self._know(connection="mysql_local", term="本库术语")
        self._know(connection="*", term="全局术语")
        self._know(connection="另一个库", term="别家的")

        only_this = self.client.get("/api/knowledge-entries?connection=mysql_local").json()["entries"]
        terms = sorted(e["term"] for e in only_this)
        self.assertEqual(terms, ["全局术语", "本库术语"], "全局必须一起返回，别家的不能出现")

        all_of_them = self.client.get("/api/knowledge-entries").json()["entries"]
        self.assertEqual(len(all_of_them), 3, "不传 connection = 全部")

    def test_knowledge_update(self):
        eid = self._know().json()["id"]
        r = self.client.put(f"/api/knowledge-entries/{eid}", json={**_KNOW, "definition": "改过的口径"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/knowledge-entries").json()["entries"][0]["definition"],
                         "改过的口径")

    def test_knowledge_update_missing_is_404(self):
        """MCP 对不存在的 id 抛异常（且不给原因）→ 本层先查，给出 404。"""
        r = self.client.put("/api/knowledge-entries/999", json=_KNOW)
        self.assertEqual(r.status_code, 404)
        self.assertIn("不存在", r.json()["detail"])

    def test_knowledge_delete_then_missing_404(self):
        eid = self._know().json()["id"]
        self.assertEqual(self.client.delete(f"/api/knowledge-entries/{eid}").status_code, 200)
        self.assertEqual(self.client.get("/api/knowledge-entries").json()["count"], 0)
        self.assertEqual(self.client.delete(f"/api/knowledge-entries/{eid}").status_code, 404)

    def test_knowledge_rejects_empty_fields(self):
        for over, expect in (({"term": ""}, "术语"), ({"definition": "   "}, "口径")):
            with self.subTest(over=over):
                r = self._know(**over)
                self.assertEqual(r.status_code, 400)
                self.assertIn(expect, r.json()["detail"])

    # ── 知识库：试一下 ──

    def test_knowledge_search(self):
        self._know(term="活跃客户", connection="mysql_local")
        self._know(term="有效订单", connection="mysql_local")
        r = self.client.get("/api/knowledge-entries/search?connection=mysql_local&query=活跃")
        self.assertEqual(r.status_code, 200)
        hits = r.json()["hits"]
        self.assertEqual([h["term"] for h in hits], ["活跃客户"])

    def test_knowledge_search_requires_query(self):
        r = self.client.get("/api/knowledge-entries/search?query=%20")
        self.assertEqual(r.status_code, 400)

    # ── SQL 模板 ──

    def test_template_empty_and_create(self):
        self.assertEqual(self.client.get("/api/sql-templates").json(),
                         {"templates": [], "count": 0})
        r = self._tpl()
        self.assertEqual(r.status_code, 200)
        rows = self.client.get("/api/sql-templates").json()["templates"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "武汉客户数")

    def test_template_filter_includes_global(self):
        self._tpl(connection="mysql_local", name="本库")
        self._tpl(connection="*", name="全局")
        names = sorted(t["name"] for t in
                       self.client.get("/api/sql-templates?connection=mysql_local").json()["templates"])
        self.assertEqual(names, ["全局", "本库"])

    def test_template_update_and_missing_404(self):
        tid = self._tpl().json()["id"]
        r = self.client.put(f"/api/sql-templates/{tid}", json={**_TPL, "name": "改名"})
        self.assertEqual(r.status_code, 200)
        r = self.client.put("/api/sql-templates/999", json=_TPL)
        self.assertEqual(r.status_code, 404)

    def test_template_delete_then_missing_404(self):
        tid = self._tpl().json()["id"]
        self.assertEqual(self.client.delete(f"/api/sql-templates/{tid}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/sql-templates/{tid}").status_code, 404)

    def test_template_rejects_non_readonly_sql(self):
        """MCP 的 validate_readonly 是权威，但它只回通用文案 → 本地先拦，给可读的 400。"""
        for sql in ("DELETE FROM orders", "update orders set x=1", "DROP TABLE orders"):
            with self.subTest(sql=sql):
                r = self._tpl(sql=sql)
                self.assertEqual(r.status_code, 400)
                self.assertIn("只读", r.json()["detail"])

    def test_template_accepts_cte(self):
        r = self._tpl(sql="WITH t AS (SELECT 1 AS a) SELECT * FROM t")
        self.assertEqual(r.status_code, 200)

    def test_template_rejects_bad_params(self):
        for params, expect in (("{oops", "JSON"), ("[1,2]", "JSON 对象")):
            with self.subTest(params=params):
                r = self._tpl(params=params)
                self.assertEqual(r.status_code, 400)
                self.assertIn(expect, r.json()["detail"])

    def test_template_rejects_empty_sql(self):
        r = self._tpl(sql="   ")
        self.assertEqual(r.status_code, 400)

    # ── 依赖不可用 ──

    def test_mcp_unreachable_is_502(self):
        async def boom(tool, arguments=None, **kw):
            raise McpError("连不上问数服务（http://x:9123/mcp）：refused")

        with patch("assets.call_tool", boom):
            for path in ("/api/knowledge-entries", "/api/sql-templates"):
                with self.subTest(path=path):
                    r = self.client.get(path)
                    self.assertEqual(r.status_code, 502)
                    self.assertIn("连不上问数服务", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
