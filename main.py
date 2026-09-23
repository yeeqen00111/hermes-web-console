# -*- coding: utf-8 -*-
"""
Hermes Dashboard API Demo
==========================
登录 → 验证身份 → 拉模型列表（全部可选 + 自定义端点），每个步骤打印清晰结果。
用法：
    python main.py                                   # 用默认/环境变量配置
    python main.py http://ip:port admin mypassword   # 或命令行传 3 个参数
环境变量（优先级高于命令行默认值之后，低于命令行参数）：
    HERMES_BASE / HERMES_USER / HERMES_PASS
依赖：pip install requests
"""
import json
import os
import sys

import requests

# ---------------- 配置 ----------------
BASE = os.environ.get("HERMES_BASE") or "http://localhost:8426"
USER = os.environ.get("HERMES_USER") or "admin"
PASS = os.environ.get("HERMES_PASS") or ""


def pretty(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ---------------- 客户端封装 ----------------
class HermesClient:
    """持登录会话的客户端：401 自动重登一次再重试，配合层可直接复用。"""

    def __init__(self, base, user, password):
        self.base = base.rstrip("/")
        self.user, self.password = user, password
        self.s = requests.Session()
        self._ensure_login()

    def _ensure_login(self):
        r = self.s.post(f"{self.base}/auth/password-login", json={
            "provider": "basic",
            "username": self.user,
            "password": self.password,
        }, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"登录失败 HTTP {r.status_code}: {r.text}")
        self.login_body = r.json()          # 期望 {"ok": true, "next": "/"}

    def _retry_on_401(self, method, path, **kw):
        resp = self.s.request(method, self.base + path, timeout=30, **kw)
        if resp.status_code == 401:          # 会话失效（过期/容器重启）→ 重新登录后重试
            self._ensure_login()
            resp = self.s.request(method, self.base + path, timeout=30, **kw)
        return resp

    def get(self, path, **kw):
        return self._retry_on_401("GET", path, **kw)

    def post(self, path, **kw):
        return self._retry_on_401("POST", path, **kw)


# ---------------- 各步骤 ----------------
def step_login(client):
    print("── 1 登录 ──────────────────────────────")
    print("状态码:", 200, "响应:", pretty(client.login_body))


def step_verify(client):
    print("── 2 验证身份 (/api/auth/me) ───────────")
    r = client.get("/api/auth/me")
    r.raise_for_status()
    print("状态码:", r.status_code)
    print(pretty(r.json()))


def step_custom_models(client):
    """只看自定义部分：从 /api/model/options 里筛出 slug/name 含 'custom'（或'自定义'）
    的 provider，列出它们的具体模型。其他 provider 只列名字、不打模型。"""
    print("── 3 自定义模型列表 (/api/model/options, 仅 custom) ──")
    r = client.get("/api/model/options")
    r.raise_for_status()
    payload = r.json()

    providers = payload.get("providers") or []
    def is_custom(p):
        slug = str(p.get("slug") or p.get("id") or p.get("name") or "").lower()
        name = str(p.get("name") or "").lower()
        return "custom" in slug or "custom" in name or "自定义" in (p.get("name") or "")

    custom = [p for p in providers if is_custom(p)]
    if not custom:
        print("  未发现名为 custom/自定义 的 provider。现有 provider 仅列举如下（不含模型）：")
        for p in providers:
            print(f"  • {p.get('slug') or p.get('id') or p.get('name')}")
        return

    for p in custom:
        slug = p.get("slug") or p.get("id") or p.get("name")
        models = p.get("models") or []
        print(f"  • {slug} （{len(models)} 个模型）")
        for m in models:
            print(f"      - {m}")


def step_custom_endpoints(client):
    print("── 4 自定义端点 (/api/providers/custom-endpoints) ──")
    r = client.get("/api/providers/custom-endpoints")
    r.raise_for_status()
    data = r.json()
    # 响应可能是 {endpoints: [...]} 或直接是列表，两种都处理
    rows = data.get("endpoints") if isinstance(data, dict) and isinstance(data.get("endpoints"), list) \
        else data if isinstance(data, list) else [data]
    print(f"自定义端点数量: {len(rows)}")
    for e in rows:
        print(f"  • id={e.get('id') or '-'}  name={e.get('name') or '-'}  "
              f"base_url={e.get('base_url') or '-'}  model={e.get('model') or '-'}")


def main():
    args = sys.argv[1:]
    base = args[0] if len(args) > 0 and not args[0].startswith("-") else BASE
    user = args[1] if len(args) > 1 else USER
    password = args[2] if len(args) > 2 else PASS

    print(f"目标: {base}  用户: {user}\n")
    try:
        client = HermesClient(base, user, password)
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)

    step_login(client)
    step_verify(client)
    step_custom_models(client)
    step_custom_endpoints(client)
    print("\n✅ 全部完成。后续请求直接用 client.get('/api/...')。")


if __name__ == "__main__":
    main()