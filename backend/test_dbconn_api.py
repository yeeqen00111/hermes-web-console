# -*- coding: utf-8 -*-
"""智能问数 · 数据库配置接口测试（离线，不连任何数据库）。

本模块只管写配置：**没有测连通端点，也不碰 status / last_error**。
覆盖重点：
  - **密码永不回显**（任何响应里都不许出现明文/密文）
  - 三态：不发=保持 / 空串=清除 / 非空=换新（与 Hermes 渠道的 env 语义**不同**）
  - 一期「只有一条」：改名必须换行，不能留孤儿行
  - 保存**不重置** status / last_error（那两列归问数端写）
"""
import base64
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import main

TEST_KEY = "unit-test-key-不写进任何仓库"
_form = {
    "name": "订单库", "type": "mysql", "host": "10.0.0.5", "port": 3306,
    "database": "shop", "username": "reporter", "max_rows": 200,
}


def _fernet_for(key: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode("utf-8")).digest()))


class DbConnApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "db_connections.db"
        self.addCleanup(self.tmp.cleanup)

        for p in (patch("dbconn.HERMESMCP_DB_PATH", self.db_path),
                  patch("dbconn.HERMESMCP_DB_KEY", TEST_KEY),
                  patch("config.APP_AUTH", False)):
            p.start()
            self.addCleanup(p.stop)

        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    # ── helpers ──

    def _rows(self):
        if not self.db_path.exists():
            return []          # 全被拒的请求不该留下文件（本层只在真要写时才建库）
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM db_connections ORDER BY id")]
        finally:
            conn.close()

    def _put(self, **over):
        body = dict(_form)
        body.update(over)
        return self.client.put("/api/db-connections", json=body)

    def _raw_cipher(self, name="订单库"):
        return next(r["password_cipher"] for r in self._rows() if r["name"] == name)

    # ── GET / PUT ──

    def test_empty_state_returns_empty_list(self):
        r = self.client.get("/api/db-connections")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"connections": [], "count": 0})

    def test_save_then_get_never_echoes_password(self):
        r = self._put(password="p@ssw0rd-明文哨兵")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("p@ssw0rd-明文哨兵", r.text)          # 明文不回显
        self.assertNotIn(self._raw_cipher(), r.text)          # 密文也不回显
        conn = r.json()["connection"]
        self.assertEqual(conn["name"], "订单库")
        self.assertEqual(conn["port"], 3306)
        self.assertEqual(conn["read_only"], True)
        self.assertEqual(conn["max_rows"], 200)
        self.assertEqual(conn["has_password"], True)

        listed = self.client.get("/api/db-connections").json()
        self.assertEqual(listed["count"], 1)
        self.assertNotIn("p@ssw0rd-明文哨兵", str(listed))

    def test_password_is_stored_as_fernet_token_with_matching_key(self):
        self._put(password="db-secret")
        # 与 hermes-mcp 同一套格式：用同样的 key 派生，必须解得开
        self.assertEqual(_fernet_for(TEST_KEY).decrypt(self._raw_cipher().encode()).decode(), "db-secret")

    def test_password_three_states(self):
        self._put(password="first")
        first = self._raw_cipher()

        self._put(database="shop")                       # 不发 password → 保持
        self.assertEqual(self._raw_cipher(), first)

        self._put(password="second")                     # 非空 → 换新
        second = self._raw_cipher()
        self.assertNotEqual(second, first)
        self.assertEqual(_fernet_for(TEST_KEY).decrypt(second.encode()).decode(), "second")

        self._put(password="")                           # 空串 → 清除
        self.assertEqual(self._raw_cipher(), "")
        self.assertFalse(self.client.get("/api/db-connections").json()["connections"][0]["has_password"])

    def test_first_save_requires_password(self):
        r = self.client.put("/api/db-connections", json=dict(_form))   # 不带 password
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(self._rows()), 0)

    def test_rename_replaces_the_row_instead_of_leaving_two(self):
        self._put(password="p")
        before = self._raw_cipher()
        self._put(name="仓库库")                          # 不发明文密码 → 沿用原密文
        rows = self._rows()
        self.assertEqual(len(rows), 1, "一期必须恒为一条：改名要换行，不能新增")
        self.assertEqual(rows[0]["name"], "仓库库")
        self.assertEqual(rows[0]["password_cipher"], before, "改名不该动密码")

    def test_only_one_row_even_after_many_saves(self):
        for i in range(4):
            self._put(password=f"p{i}", database=f"shop{i}")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["database"], "shop3")

    def test_save_does_not_touch_status_columns(self):
        """status / last_error 归问数端写，本层保存时不许清掉。"""
        self._put(password="p")
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE db_connections SET status='ok', last_error='上次的错误'")
        conn.commit()
        conn.close()

        self._put(password=None, database="shop2")
        row = self._rows()[0]
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["last_error"], "上次的错误")
        out = self.client.get("/api/db-connections").json()["connections"][0]
        self.assertEqual(out["status"], "ok")

    # ── 参数校验 ──

    def test_rejects_bad_input(self):
        cases = [
            ({"name": ""}, "名称"),
            ({"name": "a" * 65}, "名称"),
            ({"name": "bad name!"}, "名称"),
            ({"type": "sqlite"}, "不支持"),
            ({"type": ""}, "不支持"),
            ({"host": ""}, "主机"),
            ({"host": "10.0.0.5/../x"}, "主机"),
            ({"port": 0}, "端口"),
            ({"port": 70000}, "端口"),
            ({"database": ""}, "库名"),
            ({"username": ""}, "账号"),
            ({"max_rows": 5000}, "行数上限"),
            ({"max_rows": 0}, "行数上限"),
        ]
        for over, keyword in cases:
            with self.subTest(**over):
                r = self._put(password="p", **over)
                self.assertEqual(r.status_code, 400)
                self.assertIn(keyword, r.json()["detail"])
        self.assertEqual(len(self._rows()), 0)

    def test_defaults_port_and_max_rows(self):
        body = {k: v for k, v in _form.items() if k not in ("port", "max_rows")}
        body["password"] = "p"
        self.assertEqual(self.client.put("/api/db-connections", json=body).status_code, 200)
        row = self._rows()[0]
        self.assertEqual(row["port"], 3306)
        self.assertEqual(row["max_rows"], 200)

    def test_read_only_is_always_one_even_if_client_sends_false(self):
        self._put(password="p", read_only=False)
        self.assertEqual(self._rows()[0]["read_only"], 1)

    # ── 加密 key 缺失 ──

    def test_missing_key_refuses_to_store_password(self):
        with patch("dbconn.HERMESMCP_DB_KEY", ""):
            r = self._put(password="p")
        self.assertEqual(r.status_code, 502)
        self.assertIn("HERMESMCP_DB_KEY", r.json()["detail"])
        self.assertEqual(len(self._rows()), 0)


if __name__ == "__main__":
    unittest.main()
