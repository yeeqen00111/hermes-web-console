import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import main
from channels import FEISHU_ENV_KEYS, PLATFORM_ID


def _card(**overrides):
    card = {
        "id": PLATFORM_ID,
        "name": "Feishu / Lark",
        "description": "Use Hermes inside Feishu / Lark.",
        "docs_url": "https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/intro",
        "enabled": True,
        "configured": True,
        "gateway_running": True,
        "state": "connected",
        "home_channel": {"channel_id": "oc_xxx"},
        "env_vars": [
            {"key": "FEISHU_APP_ID", "required": True, "is_set": True, "redacted_value": "cli_****1234"},
            {"key": "FEISHU_APP_SECRET", "required": True, "is_set": True, "redacted_value": None},
            {"key": "FEISHU_ENCRYPT_KEY", "required": False, "is_set": False, "redacted_value": None},
        ],
    }
    card.update(overrides)
    return card


class ChannelsApiTests(unittest.TestCase):
    def setUp(self):
        self.auth_patch = patch("config.APP_AUTH", False)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.upstream = patch.object(main.hc, "request")
        self.request = self.upstream.start()
        self.addCleanup(self.upstream.stop)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def response(self, payload, status=200):
        self.request.return_value = Mock(
            ok=status < 400, status_code=status, json=Mock(return_value=payload), text="<body>")

    # ── GET ──

    def test_get_returns_only_the_feishu_card(self):
        card = _card()
        self.response({"platforms": [card, {"id": "weixin", "name": "Weixin"},
                                     {"id": "discord", "name": "Discord"}]})
        response = self.client.get("/api/channels/feishu")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), card)
        self.request.assert_called_once_with("GET", "/api/messaging/platforms")

    def test_get_404_when_feishu_missing_from_catalog(self):
        self.response({"platforms": [{"id": "weixin", "name": "Weixin"}]})
        response = self.client.get("/api/channels/feishu")
        self.assertEqual(response.status_code, 404)
        self.assertIn("detail", response.json())

    def test_get_maps_upstream_failure_to_502(self):
        self.response({}, 503)
        response = self.client.get("/api/channels/feishu")
        self.assertEqual(response.status_code, 502)

    def test_get_rejects_malformed_upstream_payload(self):
        for payload in ([], "unexpected", None):
            with self.subTest(payload=payload):
                self.response(payload, 200)
                self.assertEqual(self.client.get("/api/channels/feishu").status_code, 502)

    # ── PUT ──

    def test_put_forwards_unscoped_body_and_returns_hot_served(self):
        self.response({"ok": True, "platform": PLATFORM_ID, "hot_served": False})
        body = {"enabled": True, "env": {"FEISHU_DOMAIN": "https://open.feishu.cn"},
                "clear_env": ["FEISHU_VERIFICATION_TOKEN"]}
        response = self.client.put("/api/channels/feishu", json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "platform": PLATFORM_ID, "hot_served": False})
        # 不带 profile → unscoped（正在跑的网关）；body 逐字透传，本层零改动
        self.request.assert_called_once_with(
            "PUT", "/api/messaging/platforms/feishu", json=body)

    def test_put_preserves_empty_string_semantics_never_converts_to_clear(self):
        # 上游对 env 空串执行 `if trimmed:` 跳过 = "不改"；清除只认 clear_env。
        # 本层必须把空串原样透传，绝不能把它解释成清除（否则会误删用户配置）。
        body = {"env": {"FEISHU_DOMAIN": ""}, "clear_env": []}
        self.response({"ok": True, "platform": PLATFORM_ID, "hot_served": False})
        response = self.client.put("/api/channels/feishu", json=body)
        self.assertEqual(response.status_code, 200)
        self.request.assert_called_once_with(
            "PUT", "/api/messaging/platforms/feishu",
            json={"enabled": None, "env": {"FEISHU_DOMAIN": ""}, "clear_env": []})

    def test_put_supports_minimal_body_and_omits_unset_fields_verbatim(self):
        # 全空 body → 上游 no-op；本层照发（enabled 保持 None 不上送）。
        self.response({"ok": True, "platform": PLATFORM_ID, "hot_served": True})
        response = self.client.put("/api/channels/feishu", json={})
        self.assertEqual(response.status_code, 200)
        self.request.assert_called_once_with(
            "PUT", "/api/messaging/platforms/feishu", json={"enabled": None, "env": {}, "clear_env": []})

    def test_put_rejects_unknown_env_key_locally_without_touching_upstream(self):
        for body in ({"env": {"OPENAI_API_KEY": "sk-abc"}},
                     {"env": {"FEISHU_TOKEN": "x"}},
                     {"clear_env": ["FEISHU_VERIFY_TOKEN"]},
                     {"clear_env": ["FEISHU_APP_SECRET", "DISCORD_TOKEN"]}):
            with self.subTest(body=body):
                response = self.client.put("/api/channels/feishu", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertIn("detail", response.json())
        self.request.assert_not_called()

    def test_put_rejects_unknown_key_even_alongside_valid_ones(self):
        self.response({}, 200)
        response = self.client.put(
            "/api/channels/feishu",
            json={"env": {"FEISHU_APP_ID": "cli_x", "MALFORMED_SNEAK_KEY": "y"}, "clear_env": []})
        self.assertEqual(response.status_code, 400)
        self.request.assert_not_called()

    def test_put_accepts_every_key_in_the_allowlist(self):
        self.response({"ok": True, "platform": PLATFORM_ID, "hot_served": False})
        env = {k: f"val-{i}" for i, k in enumerate(FEISHU_ENV_KEYS)}
        response = self.client.put("/api/channels/feishu", json={"env": env, "clear_env": []})
        self.assertEqual(response.status_code, 200)
        self.request.assert_called_once_with("PUT", "/api/messaging/platforms/feishu",
                                             json={"enabled": None, "env": env, "clear_env": []})

    def test_put_error_never_echoes_upstream_body_or_credentials(self):
        # 上游 500 里塞了疑似密钥原文——本层必须换成固定文案，不回显。
        self.response({}, 500)
        response = self.client.put(
            "/api/channels/feishu", json={"env": {"FEISHU_APP_SECRET": "super-secret-live-value"}})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("super-secret-live-value", response.text)

    # ── POST test ──

    def test_post_test_forwards_and_returns_state_message(self):
        result = {"ok": True, "state": "connected", "message": "Feishu / Lark is connected."}
        self.response(result)
        response = self.client.post("/api/channels/feishu/test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), result)
        self.request.assert_called_once_with("POST", "/api/messaging/platforms/feishu/test")

    def test_post_test_maps_upstream_failure(self):
        self.response({}, 500)
        response = self.client.post("/api/channels/feishu/test")
        self.assertEqual(response.status_code, 502)

    # ── POST restart ──

    def test_post_restart_forwards_to_gateway_restart_and_returns_pid(self):
        result = {"ok": True, "pid": 77325, "name": "gateway-restart"}
        self.response(result)
        response = self.client.post("/api/channels/feishu/restart")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), result)
        self.request.assert_called_once_with("POST", "/api/gateway/restart")

    def test_post_restart_maps_upstream_failure(self):
        self.response({}, 500)
        response = self.client.post("/api/channels/feishu/restart")
        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()