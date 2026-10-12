import unittest
from unittest.mock import Mock, call, patch

from fastapi.testclient import TestClient

import main
from channels import ALLOWED_PLATFORMS

# 上游平台卡里 env_vars 的实测快照（2026-10-09 对真实 dashboard GET 得到）。
# 本层不再硬编码这份清单——PUT 时从卡片现取；测试里用它来喂 mock。
PLATFORM_ENV_KEYS = {
    "feishu": ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_ENCRYPT_KEY",
               "FEISHU_VERIFICATION_TOKEN", "FEISHU_ALLOWED_USERS", "FEISHU_DOMAIN"],
    "dingtalk": ["DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET",
                 "DINGTALK_ALLOWED_USERS", "DINGTALK_WEBHOOK_URL"],
}
REQUIRED_KEYS = {
    "feishu": {"FEISHU_APP_ID", "FEISHU_APP_SECRET"},
    "dingtalk": {"DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET"},
}
PLATFORM_NAMES = {"feishu": "Feishu / Lark", "dingtalk": "DingTalk"}

_UNSET = object()   # get_payload 的"未设置"哨兵（payload 本身可能就是 None）


def _resp(payload, status=200):
    return Mock(ok=status < 400, status_code=status, json=Mock(return_value=payload), text="<body>")


def _card(platform_id, **overrides):
    """造一张平台卡。白名单外的 id 也能造（用来验证"不会取到别人的卡"）。"""
    card = {
        "id": platform_id,
        "name": PLATFORM_NAMES.get(platform_id, platform_id.title()),
        "description": f"Use Hermes inside {platform_id}.",
        "docs_url": "https://example.invalid/docs",
        "enabled": True,
        "configured": True,
        "gateway_running": True,
        "state": "connected",
        "home_channel": {"channel_id": "oc_xxx"},
        "env_vars": [
            {"key": key, "required": key in REQUIRED_KEYS.get(platform_id, ()),
             "is_set": True, "redacted_value": None}
            for key in PLATFORM_ENV_KEYS.get(platform_id, [])
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

        # 默认两个平台都在册。PUT 现在会先打一次 GET 取 env allowlist，
        # 所以 mock 必须按 (method, path) 分派，不能只用一个返回值。
        self.cards = {pid: _card(pid) for pid in ALLOWED_PLATFORMS}
        self.get_status = 200
        self.get_payload = _UNSET          # 非 _UNSET 时覆盖 GET 的返回体
        self.put_result = {"ok": True, "hot_served": False}
        self.put_status = 200
        self.test_result = {"ok": True, "state": "connected", "message": "connected."}
        self.test_status = 200
        self.restart_result = {"ok": True, "pid": 77325, "name": "gateway-restart"}
        self.restart_status = 200
        self.request.side_effect = self._dispatch

        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _dispatch(self, method, path, **kwargs):
        if method == "GET":
            if self.get_status >= 400:
                return _resp({}, self.get_status)
            payload = self.get_payload if self.get_payload is not _UNSET else {
                "platforms": list(self.cards.values())}
            return _resp(payload, self.get_status)
        if method == "PUT":
            return _resp(self.put_result, self.put_status)
        if method == "POST":
            if path.endswith("/test"):
                return _resp(self.test_result, self.test_status)
            return _resp(self.restart_result, self.restart_status)
        raise AssertionError(f"unexpected upstream call: {method} {path}")

    def calls(self, method):
        return [c for c in self.request.call_args_list if c.args and c.args[0] == method]

    # ── 平台白名单 ──

    def test_unknown_platform_refused_without_touching_upstream(self):
        # 合法但不在白名单 → 404（本层判定）；非法字符集 → 422（路径校验先拦）。
        # 两条路径都必须**零上游调用**，且绝不把未知 id 拼进上游 URL。
        for path in ("/api/channels/weixin", "/api/channels/discord", "/api/channels/dingtalk2"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
        # 非法字符集由路径校验先拦（FastAPI 给 4xx，具体码不锁）；同样不得打上游。
        for path in ("/api/channels", "/api/channels/", "/api/channels/FEISHU", "/api/channels/a b"):
            with self.subTest(path=path):
                status = self.client.get(path).status_code
                self.assertTrue(400 <= status < 500, f"{path} → {status}")
        for path in ("/api/channels/weixin/test", "/api/channels/weixin/restart"):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path).status_code, 404)
        with self.subTest(path="/api/channels/weixin"):
            self.assertEqual(self.client.put("/api/channels/weixin", json={}).status_code, 404)
        self.request.assert_not_called()

    # ── GET ──

    def test_get_returns_only_the_requested_card(self):
        self.cards["weixin"] = _card("weixin", id="weixin", name="Weixin")
        for pid in ALLOWED_PLATFORMS:
            with self.subTest(platform=pid):
                self.request.reset_mock()
                response = self.client.get(f"/api/channels/{pid}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), self.cards[pid])
                self.request.assert_called_once_with("GET", "/api/messaging/platforms")

    def test_get_404_when_platform_missing_from_catalog(self):
        for pid in ALLOWED_PLATFORMS:
            with self.subTest(platform=pid):
                self.cards = {p: c for p, c in self.cards.items() if p != pid}
                response = self.client.get(f"/api/channels/{pid}")
                self.assertEqual(response.status_code, 404)
                self.assertIn("detail", response.json())

    def test_get_maps_upstream_failure_to_502(self):
        self.get_status = 503
        self.assertEqual(self.client.get("/api/channels/feishu").status_code, 502)

    def test_get_rejects_malformed_upstream_payload(self):
        for payload in ([], "unexpected", None):
            with self.subTest(payload=payload):
                self.get_payload = payload
                self.assertEqual(self.client.get("/api/channels/feishu").status_code, 502)

    # ── PUT ──

    def test_put_forwards_unscoped_body_and_returns_hot_served(self):
        body = {"enabled": True, "env": {"FEISHU_DOMAIN": "https://open.feishu.cn"},
                "clear_env": ["FEISHU_VERIFICATION_TOKEN"]}
        response = self.client.put("/api/channels/feishu", json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "hot_served": False})
        # 不带 profile → unscoped（正在跑的网关）；body 逐字透传，本层零改动
        self.assertEqual(self.calls("PUT"), [
            call("PUT", "/api/messaging/platforms/feishu", json=body)])

    def test_put_forwards_for_dingtalk_too(self):
        body = {"env": {"DINGTALK_ALLOWED_USERS": "u1,u2"}, "clear_env": []}
        self.assertEqual(self.client.put("/api/channels/dingtalk", json=body).status_code, 200)
        self.assertEqual(self.calls("PUT"), [
            call("PUT", "/api/messaging/platforms/dingtalk",
                               json={"enabled": None, "env": {"DINGTALK_ALLOWED_USERS": "u1,u2"},
                                     "clear_env": []})])

    def test_put_preserves_empty_string_semantics_never_converts_to_clear(self):
        # 上游对 env 空串执行 `if trimmed:` 跳过 = "不改"；清除只认 clear_env。
        # 本层必须把空串原样透传，绝不能把它解释成清除（否则会误删用户配置）。
        body = {"env": {"FEISHU_DOMAIN": ""}, "clear_env": []}
        self.assertEqual(self.client.put("/api/channels/feishu", json=body).status_code, 200)
        self.assertEqual(self.calls("PUT"), [
            call("PUT", "/api/messaging/platforms/feishu",
                               json={"enabled": None, "env": {"FEISHU_DOMAIN": ""}, "clear_env": []})])

    def test_put_supports_minimal_body_and_omits_unset_fields_verbatim(self):
        # 全空 body → 上游 no-op；本层照发（enabled 保持 None 不上送）。
        self.assertEqual(self.client.put("/api/channels/feishu", json={}).status_code, 200)
        self.assertEqual(self.calls("PUT"), [
            call("PUT", "/api/messaging/platforms/feishu",
                               json={"enabled": None, "env": {}, "clear_env": []})])

    def test_put_rejects_unknown_env_key_locally_without_writing_upstream(self):
        # 拒绝路径只允许打一次 GET（取 allowlist），**绝不能打上游写接口**。
        cases = {
            "feishu": [{"env": {"OPENAI_API_KEY": "sk-abc"}},
                       {"env": {"FEISHU_TOKEN": "x"}},
                       {"clear_env": ["FEISHU_VERIFY_TOKEN"]},
                       {"clear_env": ["FEISHU_APP_SECRET", "DISCORD_TOKEN"]},
                       {"env": {"DINGTALK_CLIENT_ID": "cross-platform"}}],
            "dingtalk": [{"env": {"DINGTALK_TOKEN": "x"}},
                         {"clear_env": ["DINGTALK_HOME_CHANNEL"]},   # 上游隐藏的自配置项
                         {"env": {"FEISHU_APP_ID": "cross-platform"}}],
        }
        for pid, bodies in cases.items():
            for body in bodies:
                with self.subTest(platform=pid, body=body):
                    self.request.reset_mock()
                    response = self.client.put(f"/api/channels/{pid}", json=body)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("detail", response.json())
                    self.assertEqual(self.calls("PUT"), [])

    def test_put_rejects_unknown_key_even_alongside_valid_ones(self):
        response = self.client.put(
            "/api/channels/feishu",
            json={"env": {"FEISHU_APP_ID": "cli_x", "MALFORMED_SNEAK_KEY": "y"}, "clear_env": []})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls("PUT"), [])

    def test_put_accepts_every_key_in_the_allowlist(self):
        for pid, keys in PLATFORM_ENV_KEYS.items():
            with self.subTest(platform=pid):
                self.request.reset_mock()
                env = {k: f"val-{i}" for i, k in enumerate(keys)}
                response = self.client.put(f"/api/channels/{pid}", json={"env": env, "clear_env": []})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.calls("PUT"), [
                    call("PUT", f"/api/messaging/platforms/{pid}",
                                       json={"enabled": None, "env": env, "clear_env": []})])

    def test_put_allowlist_is_read_from_the_card_not_a_constant(self):
        # 上游加了一个新键 → 本层立刻可写，无需改代码（这正是动态取的唯一理由）。
        self.cards["dingtalk"] = _card("dingtalk", env_vars=[
            {"key": "DINGTALK_CLIENT_ID", "required": True, "is_set": True, "redacted_value": None},
            {"key": "DINGTALK_BRAND_NEW_KNOB", "required": False, "is_set": False, "redacted_value": None},
        ])
        response = self.client.put("/api/channels/dingtalk",
                                   json={"env": {"DINGTALK_BRAND_NEW_KNOB": "v"}, "clear_env": []})
        self.assertEqual(response.status_code, 200)

    def test_put_error_never_echoes_upstream_body_or_credentials(self):
        # 上游 500 里塞了疑似密钥原文——本层必须换成固定文案，不回显。
        self.put_status = 500
        response = self.client.put(
            "/api/channels/feishu", json={"env": {"FEISHU_APP_SECRET": "super-secret-live-value"}})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("super-secret-live-value", response.text)

    # ── POST test ──

    def test_post_test_forwards_and_returns_state_message(self):
        result = {"ok": True, "state": "connected", "message": "is connected."}
        self.test_result = result
        for pid in ALLOWED_PLATFORMS:
            with self.subTest(platform=pid):
                self.request.reset_mock()
                response = self.client.post(f"/api/channels/{pid}/test")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), result)
                self.assertEqual(self.calls("POST"), [
                    call("POST", f"/api/messaging/platforms/{pid}/test")])

    def test_post_test_maps_upstream_failure(self):
        self.test_status = 500
        self.assertEqual(self.client.post("/api/channels/feishu/test").status_code, 502)

    # ── POST restart（全站唯一入口：固定段 feishu）──

    def test_post_restart_forwards_to_gateway_restart_and_returns_pid(self):
        response = self.client.post("/api/channels/feishu/restart")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.restart_result)
        self.assertEqual(self.calls("POST"), [call("POST", "/api/gateway/restart")])

    def test_restart_has_exactly_one_path(self):
        """重启网关只有 /api/channels/feishu/restart 一条：路径里的 feishu 是固定段。

        任何"平台形态"的第二条路径都必须 404 且**不打上游**——包括白名单内的 dingtalk。
        """
        self.request.reset_mock()
        for path in ("/api/channels/dingtalk/restart", "/api/channels/weixin/restart"):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path).status_code, 404)
        self.request.assert_not_called()

    def test_post_restart_maps_upstream_failure(self):
        self.restart_status = 500
        self.assertEqual(self.client.post("/api/channels/feishu/restart").status_code, 502)

    def test_post_restart_error_text_is_gateway_scoped(self):
        """上游拒绝时文案是网关口径——重启是网关级动作，不该出现"飞书渠道配置请求"。"""
        self.restart_status = 409
        response = self.client.post("/api/channels/feishu/restart")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "Hermes 拒绝了本次重启网关请求")

    # ── 对外契约：飞书文案逐字不变（参数化不得改变既有调用者的可观察行为）──

    def test_feishu_error_text_unchanged_by_parameterization(self):
        self.get_status = 503
        self.assertEqual(self.client.get("/api/channels/feishu").json()["detail"],
                         "Hermes 渠道接口请求失败（HTTP 503）")
        self.get_status = 200
        saved = self.cards.pop("feishu")
        self.assertEqual(self.client.get("/api/channels/feishu").json()["detail"],
                         "飞书平台不在 Hermes 渠道目录中")
        self.cards["feishu"] = saved
        self.assertEqual(
            self.client.put("/api/channels/feishu", json={"env": {"NOPE_KEY": "x"}}).json()["detail"]
            .startswith("NOPE_KEY 不是飞书可配置项（可用："),
            True)


if __name__ == "__main__":
    unittest.main()
