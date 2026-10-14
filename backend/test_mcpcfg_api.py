# -*- coding: utf-8 -*-
"""智能问数 · MCP 服务配置接口测试（离线，不打真 dashboard）。

本层是**纯代理**：只验证「路径 / 方法 / 请求体转发正确」+「上游错误换成人话、不回显响应体」。
一个易踩的点：上游启停接口收的是 `{enabled, profile?}` **对象**，发裸布尔会被判 422。
"""
import unittest
from unittest.mock import Mock, call, patch

from fastapi.testclient import TestClient

import main
import mcpcfg
from config import MCP_SERVER_NAME

UPSTREAM_LIST = "/api/mcp/servers"


def _resp(payload, status=200, text="<body>"):
    return Mock(ok=status < 400, status_code=status, json=Mock(return_value=payload), text=text)


class McpServersApiTests(unittest.TestCase):
    def setUp(self):
        self.auth = patch("config.APP_AUTH", False)
        self.auth.start()
        self.addCleanup(self.auth.stop)

        # 打桩要打在**用到它的那个模块**上：hc 是 config 里的共享单例，
        # mcpcfg 直接引用它；只 patch main.hc 的话这里会真去连 dashboard。
        self.hc = Mock()
        self.patcher = patch.object(mcpcfg, "hc", self.hc)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.upstream = self.hc.request

        self.upstream.return_value = _resp({"servers": [
            {"name": MCP_SERVER_NAME, "transport": "http", "url": "http://10.0.0.9:9123/mcp",
             "auth": None, "enabled": True, "tools": None},
            {"name": "metaso", "transport": "http", "url": "https://metaso.cn/api/mcp",
             "auth": "header", "enabled": True, "tools": None},
        ]})
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    # ── GET ──

    def test_list_forwards_and_marks_the_configured_server(self):
        r = self.client.get("/api/mcp-servers")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.upstream.call_args, call("GET", UPSTREAM_LIST))
        body = r.json()
        self.assertEqual(body["configured_name"], MCP_SERVER_NAME)
        self.assertTrue(body["configured_found"])
        self.assertEqual([s["name"] for s in body["servers"]], [MCP_SERVER_NAME, "metaso"])

    def test_list_marks_missing_when_configured_server_is_absent(self):
        self.upstream.return_value = _resp({"servers": [{"name": "metaso"}]})
        self.assertFalse(self.client.get("/api/mcp-servers").json()["configured_found"])

    def test_list_maps_upstream_failure_and_hides_body(self):
        self.upstream.return_value = _resp({}, 500, text="token=leak-me")
        r = self.client.get("/api/mcp-servers")
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("leak-me", r.text)

    def test_list_rejects_malformed_payload(self):
        self.upstream.return_value = _resp(["not", "a", "dict"])
        self.assertEqual(self.client.get("/api/mcp-servers").status_code, 502)

    def test_dashboard_unreachable_becomes_502_not_500(self):
        """dashboard 挂了要给人话，不能抛裸 500；异常原文（含地址/账号）不许回显。"""
        import requests
        self.upstream.side_effect = requests.ConnectionError(
            "HTTPConnectionPool(host='10.0.0.9', port=8426): refused")
        r = self.client.get("/api/mcp-servers")
        self.assertEqual(r.status_code, 502)
        self.assertIn("HERMES_BASE", r.json()["detail"])
        self.assertNotIn("10.0.0.9", r.text)

    # ── POST {name}/test ──

    def test_test_forwards_and_passes_through_tool_list(self):
        upstream_body = {"ok": False, "error": "连接被拒绝", "tools": []}
        self.upstream.return_value = _resp(upstream_body)
        r = self.client.post(f"/api/mcp-servers/{MCP_SERVER_NAME}/test")
        self.assertEqual(r.status_code, 200)                 # 上游 200 + ok:false 仍是 200，交给页面显示
        self.assertEqual(r.json(), upstream_body)
        self.assertEqual(self.upstream.call_args,
                         call("POST", f"{UPSTREAM_LIST}/{MCP_SERVER_NAME}/test"))

    def test_test_unknown_server_maps_to_404_with_name(self):
        self.upstream.return_value = _resp({}, 404)
        r = self.client.post("/api/mcp-servers/nope/test")
        self.assertEqual(r.status_code, 404)
        self.assertIn("nope", r.json()["detail"])

    def test_test_upstream_error_hides_body(self):
        self.upstream.return_value = _resp({}, 401, text="token=leak-me")
        r = self.client.post(f"/api/mcp-servers/{MCP_SERVER_NAME}/test")
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("leak-me", r.text)

    # ── PUT {name}/enabled ──

    def test_enabled_forwards_wrapped_object_not_bare_bool(self):
        self.upstream.return_value = _resp({"ok": True, "name": MCP_SERVER_NAME, "enabled": False})
        r = self.client.put(f"/api/mcp-servers/{MCP_SERVER_NAME}/enabled", json={"enabled": False})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.upstream.call_args,
                         call("PUT", f"{UPSTREAM_LIST}/{MCP_SERVER_NAME}/enabled",
                              json={"enabled": False}))

    def test_enabled_defaults_to_true_when_body_is_empty(self):
        self.upstream.return_value = _resp({"ok": True})
        self.client.put(f"/api/mcp-servers/{MCP_SERVER_NAME}/enabled", json={})
        self.assertEqual(self.upstream.call_args.kwargs["json"], {"enabled": True})

    def test_enabled_unknown_server_maps_to_404(self):
        self.upstream.return_value = _resp({}, 404)
        self.assertEqual(self.client.put("/api/mcp-servers/nope/enabled",
                                         json={"enabled": True}).status_code, 404)

    # ── 新增 / 删除 / 改地址 ──

    def test_create_sends_url_entry_with_auth_none(self):
        self.upstream.side_effect = [
            _resp({"servers": []}),                                   # 重名预检
            _resp({"name": "sql-coustm2", "transport": "http",
                   "url": "http://127.0.0.1:9123/mcp", "auth": None, "enabled": True}),
        ]
        r = self.client.post("/api/mcp-servers",
                             json={"name": "sql-coustm2", "url": "http://127.0.0.1:9123/mcp"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "sql-coustm2")
        create_call = self.upstream.call_args_list[1]
        self.assertEqual(create_call.args, ("POST", UPSTREAM_LIST))
        self.assertEqual(create_call.kwargs["json"],
                         {"name": "sql-coustm2", "url": "http://127.0.0.1:9123/mcp", "auth": "none"})

    def test_create_rejects_duplicate_without_touching_upstream_write(self):
        self.upstream.return_value = _resp({"servers": [{"name": "orders"}]})
        r = self.client.post("/api/mcp-servers", json={"name": "orders", "url": "http://h:1/mcp"})
        self.assertEqual(r.status_code, 409)
        # 只有那一次 GET 预检，没有 POST 写调用
        self.assertEqual(len(self.upstream.call_args_list), 1)

    def test_create_maps_upstream_conflict(self):
        self.upstream.side_effect = [_resp({"servers": []}), _resp({}, 409)]
        r = self.client.post("/api/mcp-servers", json={"name": "x", "url": "http://h:1/mcp"})
        self.assertEqual(r.status_code, 409)

    def test_create_validates_name_and_url(self):
        cases = [
            ({"name": "", "url": "http://h/mcp"}, "名称"),
            ({"name": "../etc/passwd", "url": "http://h/mcp"}, "名称"),
            ({"name": "a b", "url": "http://h/mcp"}, "名称"),
            ({"name": "x" * 65, "url": "http://h/mcp"}, "名称"),
            # 中文/非 ASCII 一律不收：名字会被拼进 URL 路径，非 ASCII 在路径里是雷
            ({"name": "订单库", "url": "http://h/mcp"}, "名称"),
            ({"name": "ok", "url": ""}, "地址"),
            ({"name": "ok", "url": "ftp://h/mcp"}, "http"),
            ({"name": "ok", "url": "http://h/a b"}, "地址"),
        ]
        for body, keyword in cases:
            with self.subTest(**body):
                r = self.client.post("/api/mcp-servers", json=body)
                self.assertEqual(r.status_code, 400)
                self.assertIn(keyword, r.json()["detail"])
        self.upstream.assert_not_called()      # 本地校验拦下的，一次上游都不打

    def test_remove_forwards_delete(self):
        self.upstream.side_effect = [_resp({"servers": [{"name": "orders"}]}), _resp({"ok": True})]
        r = self.client.delete("/api/mcp-servers/orders")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.upstream.call_args_list[1].args,
                         ("DELETE", f"{UPSTREAM_LIST}/orders"))

    def test_remove_unknown_is_404_without_delete(self):
        self.upstream.return_value = _resp({"servers": []})
        r = self.client.delete("/api/mcp-servers/nope")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(len(self.upstream.call_args_list), 1)

    def test_create_sends_only_name_url_auth(self):
        """请求体只有 name/url/auth 三个键——没有鉴权字段（改地址功能已去掉，2026-10-13）。"""
        self.upstream.side_effect = [
            _resp({"servers": []}),
            _resp({"name": "hermes-mcp"}),
        ]
        self.client.post("/api/mcp-servers",
                         json={"name": "hermes-mcp", "url": "http://hermes-mcp:9123/mcp"})
        sent = self.upstream.call_args_list[1].kwargs["json"]
        self.assertEqual(sorted(sent), ["auth", "name", "url"])

    # ── 路径约定（教训：/api/channels/feishu/restart）──

    def test_paths_use_placeholders_never_literal_values(self):
        paths = [r.path for r in main.app.routes if r.path.startswith("/api/mcp-servers")]
        for expected in ("/api/mcp-servers",
                         "/api/mcp-servers/{name}",
                         "/api/mcp-servers/{name}/test",
                         "/api/mcp-servers/{name}/enabled"):
            self.assertIn(expected, paths)
        for p in paths:
            self.assertNotIn(MCP_SERVER_NAME, p, f"路径里写死了服务名：{p}")

    def test_no_change_address_route_anymore(self):
        """改地址已去掉（2026-10-13）：上游没有"改单条"的接口，硬做只能先删后建。
        任何地址形态的路由都不该存在，且 PUT 到它应该是 404/405 而不是打上游。"""
        for r in main.app.routes:
            self.assertNotIn("/address", getattr(r, "path", ""), r.path)
        self.upstream.return_value = _resp({"servers": [{"name": "a"}]})
        self.upstream.reset_mock()
        r = self.client.put("/api/mcp-servers/a/address", json={"name": "a", "url": "http://h/mcp"})
        self.assertIn(r.status_code, (404, 405))
        self.upstream.assert_not_called()

    def test_path_is_hyphenated_to_avoid_clashing_with_upstream(self):
        """上游是 /api/mcp/servers；同源照抄会撞，所以我们必须用连字符。"""
        for r in main.app.routes:
            self.assertFalse(r.path.startswith("/api/mcp/servers"), r.path)


if __name__ == "__main__":
    unittest.main()
