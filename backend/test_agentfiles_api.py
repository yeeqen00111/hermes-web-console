# -*- coding: utf-8 -*-
"""Agent 身份文件端点的离线契约测试。

假上游按 (method, path) 分派，不打真实 dashboard：`main.hc.request` 被整个替换，
所以本文件验证的是「我们发什么帧、怎么映射」而非 Hermes 行为（后者见 probe_agentfiles.py）。
"""
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import agentfiles
import main

HOME = "/opt/data"


def _resp(payload, status=200):
    return Mock(ok=status < 400, status_code=status, json=Mock(return_value=payload), text="<body>")


class FakeHermes:
    """假 dashboard：内存里存三个文件，按上游语义回包（含 404/截断/备份失败）。"""

    def __init__(self, home=HOME, profiles=None):
        self.calls = []
        self.home = home
        self.profiles = profiles if profiles is not None else [
            {"name": "default", "is_default": True, "path": home},
            {"name": "writer", "is_default": False, "path": "/opt/data/profiles/writer"},
        ]
        self.soul = None            # None = SOUL.md 不存在
        self.files = {}             # 绝对路径 → 内容
        self.truncated = {}         # 绝对路径 → 声称的总字节数（模拟 >512KB 被截断）
        self.backup_status = 200

    # ── 断言辅助 ──
    def paths(self):
        return [c[1] for c in self.calls]

    def writes(self):
        return [c for c in self.calls if c[0] == "POST" and c[1] == "/api/fs/write-text"]

    def soul_writes(self):
        return [c for c in self.calls if c[0] == "PUT"]

    def backups(self):
        return [c for c in self.calls if c[1] == "/api/ops/backup"]

    # ── 分派 ──
    def __call__(self, method, path, **kw):
        self.calls.append((method, path, kw))
        if path == "/api/profiles":
            return _resp({"profiles": self.profiles})
        if path == "/api/profiles/default/soul":
            if method == "PUT":
                self.soul = kw["json"]["content"]
                return _resp({"ok": True})
            if self.soul is None:
                return _resp({"content": "", "exists": False})
            return _resp({"content": self.soul, "exists": True})
        if path == "/api/fs/read-text":
            target = kw["params"]["path"]
            if target in self.truncated:
                return _resp({"text": "cut-off", "byteSize": self.truncated[target],
                              "truncated": True, "binary": False})
            if target not in self.files:
                return _resp({"detail": "File not found"}, 404)
            text = self.files[target]
            return _resp({"text": text, "byteSize": len(text.encode("utf-8")),
                          "truncated": False, "binary": False})
        if path == "/api/fs/write-text":
            self.files[kw["json"]["path"]] = kw["json"]["content"]
            return _resp({"ok": True})
        if path == "/api/ops/backup":
            # 真端点 `body: BackupRequest` 是必填参数：**没有 body 就 422**（字段全可选 ≠ body 可省）。
            # 假上游照抄这个契约，否则「没发 body」这类 bug 在离线测试里永远是绿的（2026-09-28 真机踩到）。
            if "json" not in kw or kw["json"] is None:
                return _resp({"detail": [{"msg": "Field required"}]}, 422)
            if self.backup_status >= 400:
                return _resp({"detail": "nope"}, self.backup_status)
            return _resp({"ok": True, "archive": "/opt/data/backups/x.zip"})
        return _resp({"detail": "unexpected"}, 500)


class AgentFilesApiTests(unittest.TestCase):
    def setUp(self):
        self.auth_patch = patch("config.APP_AUTH", False)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        agentfiles._home_cache.clear()          # 模块级缓存必须逐测清空
        agentfiles._backup_done = False
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def upstream(self, **kw):
        fake = FakeHermes(**kw)
        patcher = patch.object(main.hc, "request", side_effect=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    # ── GET 汇总 ──

    def test_list_returns_three_files_with_sizes(self):
        fake = self.upstream()
        fake.soul = "You are Hermes."
        fake.files[f"{HOME}/memories/MEMORY.md"] = "记忆内容"
        fake.files[f"{HOME}/memories/USER.md"] = "User speaks Chinese."
        response = self.client.get("/api/agent-files")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["profile"], "default")
        self.assertEqual(body["files"]["soul"], {"exists": True, "byteSize": 15})
        self.assertEqual(body["files"]["memory"], {"exists": True, "byteSize": len("记忆内容".encode())})
        self.assertEqual(body["files"]["user"], {"exists": True, "byteSize": 20})

    def test_list_marks_missing_files_without_failing(self):
        fake = self.upstream()
        response = self.client.get("/api/agent-files")
        self.assertEqual(response.status_code, 200)
        for name in ("soul", "memory", "user"):
            with self.subTest(name=name):
                self.assertEqual(response.json()["files"][name], {"exists": False, "byteSize": 0})
        self.assertIn("/api/profiles", fake.paths())

    def test_list_never_exposes_server_paths(self):
        fake = self.upstream()
        fake.files[f"{HOME}/memories/MEMORY.md"] = "x"
        text = self.client.get("/api/agent-files").text
        self.assertNotIn(HOME, text)
        self.assertNotIn("memories", text)

    def test_home_is_discovered_once_and_cached(self):
        fake = self.upstream()
        self.client.get("/api/agent-files")
        self.client.get("/api/agent-files")
        self.assertEqual(fake.paths().count("/api/profiles"), 1)

    # ── GET 单个 ──

    def test_get_memory_reads_the_default_home_path(self):
        fake = self.upstream()
        fake.files[f"{HOME}/memories/MEMORY.md"] = "abc"
        response = self.client.get("/api/agent-files/memory")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "abc")
        call = [c for c in fake.calls if c[1] == "/api/fs/read-text"][-1]
        self.assertEqual(call[2]["params"], {"path": f"{HOME}/memories/MEMORY.md"})

    def test_get_soul_uses_the_dedicated_endpoint_not_fs(self):
        fake = self.upstream()
        fake.soul = "身份"
        response = self.client.get("/api/agent-files/soul")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"name": "soul", "exists": True, "content": "身份",
                                           "byteSize": len("身份".encode()), "truncated": False,
                                           "binary": False})
        self.assertNotIn("/api/fs/read-text", fake.paths())

    def test_get_missing_memory_is_not_an_error(self):
        fake = self.upstream()
        response = self.client.get("/api/agent-files/user")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["exists"], False)
        self.assertEqual(response.json()["content"], "")

    def test_get_reports_truncation_and_totalsize(self):
        fake = self.upstream()
        fake.truncated[f"{HOME}/memories/MEMORY.md"] = 900 * 1024
        body = self.client.get("/api/agent-files/memory").json()
        self.assertTrue(body["truncated"])
        self.assertEqual(body["byteSize"], 900 * 1024)

    def test_unknown_name_is_404_without_calling_upstream(self):
        fake = self.upstream()
        for name in ("AGENTS", "config.yaml", "memory.md"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(f"/api/agent-files/{name}").status_code, 404)
        self.assertEqual(fake.calls, [])

    def test_missing_default_profile_maps_to_502_for_memory_files(self):
        # memory/user 的路径要靠 GET /api/profiles 的 default 条目解析出来 → 缺了就没法工作
        self.upstream(profiles=[{"name": "writer", "is_default": False, "path": "/opt/data/profiles/writer"}])
        response = self.client.get("/api/agent-files/memory")
        self.assertEqual(response.status_code, 502)

    def test_soul_does_not_depend_on_the_profile_listing(self):
        # SOUL 走 /api/profiles/default/soul 字面量路径，不需要先发现 home
        fake = self.upstream(profiles=[])
        fake.soul = "x"
        response = self.client.get("/api/agent-files/soul")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "x")

    # ── PUT ──

    def test_put_memory_writes_through_fs_channel_then_backs_up(self):
        fake = self.upstream()
        fake.files[f"{HOME}/memories/MEMORY.md"] = "old"
        response = self.client.put("/api/agent-files/memory",
                                   json={"content": "new content", "base_byteSize": 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "byteSize": 11})
        self.assertEqual(fake.files[f"{HOME}/memories/MEMORY.md"], "new content")
        write = fake.writes()[0]
        self.assertEqual(write[2]["json"], {"path": f"{HOME}/memories/MEMORY.md", "content": "new content"})

    def test_put_soul_uses_the_dedicated_endpoint(self):
        fake = self.upstream()
        fake.soul = "old"
        response = self.client.put("/api/agent-files/soul",
                                   json={"content": "新身份", "base_byteSize": 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.soul, "新身份")
        self.assertEqual(fake.soul_writes()[0][1], "/api/profiles/default/soul")
        self.assertEqual(fake.writes(), [])       # 绝不能走 fs 通道

    def test_put_new_file_with_zero_base_creates_it(self):
        fake = self.upstream()
        response = self.client.put("/api/agent-files/user",
                                   json={"content": "# 用户画像", "base_byteSize": 0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.files[f"{HOME}/memories/USER.md"], "# 用户画像")

    def test_clear_is_an_empty_string_write(self):
        fake = self.upstream()
        fake.files[f"{HOME}/memories/MEMORY.md"] = "old"
        response = self.client.put("/api/agent-files/memory",
                                   json={"content": "", "base_byteSize": 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["byteSize"], 0)
        self.assertEqual(fake.files[f"{HOME}/memories/MEMORY.md"], "")

    def test_stale_base_returns_409_with_current_size_and_writes_nothing(self):
        fake = self.upstream()
        fake.files[f"{HOME}/memories/MEMORY.md"] = "changed by agent"   # 读之后被 agent 改过
        response = self.client.put("/api/agent-files/memory",
                                   json={"content": "mine", "base_byteSize": 3})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["current_byteSize"], len("changed by agent"))
        self.assertIn("重新加载", response.json()["detail"])
        self.assertEqual(fake.writes(), [])
        self.assertEqual(fake.backups(), [])      # 没写就不该备份

    def test_truncated_file_refuses_overwrite(self):
        fake = self.upstream()
        fake.truncated[f"{HOME}/memories/MEMORY.md"] = 900 * 1024
        response = self.client.put("/api/agent-files/memory",
                                   json={"content": "short", "base_byteSize": 900 * 1024})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(fake.writes(), [])

    def test_oversized_content_is_rejected_before_any_upstream_call(self):
        fake = self.upstream()
        response = self.client.put("/api/agent-files/soul",
                                   json={"content": "x" * (8 * 1024 * 1024 + 1), "base_byteSize": 0})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(fake.calls, [])

    def test_backup_runs_once_across_writes(self):
        fake = self.upstream()
        fake.files[f"{HOME}/memories/MEMORY.md"] = "a"
        self.client.put("/api/agent-files/memory", json={"content": "b", "base_byteSize": 1})
        self.client.put("/api/agent-files/memory", json={"content": "c", "base_byteSize": 1})
        self.assertEqual(len(fake.backups()), 1)

    def test_backup_carries_a_json_body(self):
        # 真端点 body 必填（缺失即 422）→ 空对象也必须发出去
        fake = self.upstream()
        fake.files[f"{HOME}/memories/MEMORY.md"] = "a"
        self.client.put("/api/agent-files/memory", json={"content": "b", "base_byteSize": 1})
        call = fake.backups()[0]
        self.assertIn("json", call[2])
        self.assertEqual(call[2]["json"], {})

    def test_backup_failure_blocks_the_write(self):
        fake = self.upstream()
        fake.files[f"{HOME}/memories/MEMORY.md"] = "a"
        fake.backup_status = 500
        response = self.client.put("/api/agent-files/memory",
                                   json={"content": "b", "base_byteSize": 1})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(fake.writes(), [])
        self.assertEqual(fake.files[f"{HOME}/memories/MEMORY.md"], "a")

    def test_put_unknown_name_is_404_without_calling_upstream(self):
        fake = self.upstream()
        self.assertEqual(self.client.put("/api/agent-files/etc",
                                         json={"content": "x", "base_byteSize": 0}).status_code, 404)
        self.assertEqual(fake.calls, [])

    def test_upstream_failure_never_echoes_response_body(self):
        fake = self.upstream()
        fake.files[f"{HOME}/memories/MEMORY.md"] = "a"
        fake.backup_status = 500
        response = self.client.put("/api/agent-files/memory",
                                   json={"content": "secret-content-here", "base_byteSize": 1})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret-content-here", response.text)
        self.assertNotIn("nope", response.text)


if __name__ == "__main__":
    unittest.main()
