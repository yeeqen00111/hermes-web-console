# -*- coding: utf-8 -*-
"""
集中配置：.env 加载 + Hermes 客户端（惰性登录）+ 前端鉴权依赖。
main.py / modelcfg.py / chat.py 都从这里 import，避免循环导入。
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

import requests
from fastapi import Header, HTTPException

# ── .env 加载：backend/.env 优先，其次项目根/.env；真实环境变量优先于文件 ──
_BACKEND_DIR = Path(__file__).resolve().parent


def _load_env_file() -> None:
    for p in (_BACKEND_DIR / ".env", _BACKEND_DIR.parent / ".env"):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        break


_load_env_file()

# ── 配置常量 ──
HERMES_BASE = os.environ.get("HERMES_BASE", "http://localhost:8426")
HERMES_USER = os.environ.get("HERMES_USER", "admin")
HERMES_PASS = os.environ.get("HERMES_PASS", "")            # 必须提供（.env 或环境变量）
APP_TOKEN = os.environ.get("APP_TOKEN", secrets.token_urlsafe(32))

APP_USER = os.environ.get("APP_USER", "admin")
APP_PASS = os.environ.get("APP_PASS", "secret")
APP_AUTH = os.environ.get("APP_AUTH", "").strip().lower() in {"1", "true", "yes"}


# ── Hermes 客户端：惰性登录（首次请求才登录），401 自动重登重试 ──
class HermesClient:
    def __init__(self):
        self.s = requests.Session()
        self._logged_in = False

    def _login(self):
        r = self.s.post(
            f"{HERMES_BASE}/auth/password-login",
            json={"provider": "basic", "username": HERMES_USER, "password": HERMES_PASS},
            timeout=15,
        )
        if r.status_code != 200:
            raise HTTPException(
                status_code=503,
                detail=f"Hermes 登录失败 HTTP {r.status_code}（检查 HERMES_BASE/USER/PASS）: {r.text[:200]}",
            )
        self._logged_in = True

    def ensure_logged_in(self):
        """公开入口：chat 等 WS 流程在开始前必须调用。"""
        if not self._logged_in:
            self._login()

    def request(self, method: str, path: str, **kw) -> requests.Response:
        self.ensure_logged_in()
        resp = self.s.request(method, HERMES_BASE + path, timeout=30, **kw)
        if resp.status_code == 401:            # 会话失效（容器重启等）→ 重登一次
            self._login()
            resp = self.s.request(method, HERMES_BASE + path, timeout=30, **kw)
        return resp


hc = HermesClient()   # 惰性登录：import 不连 dashboard，首次请求才登录


# ── 前端鉴权依赖：demo 默认放行；APP_AUTH=1 后要求 Bearer <APP_TOKEN> ──
def require_app_token(authorization: str = Header(default="")) -> str:
    if not APP_AUTH:
        return "open"
    if not HERMES_PASS:
        raise HTTPException(status_code=503, detail="backend 未配置 HERMES_PASS")
    if authorization != f"Bearer {APP_TOKEN}":
        raise HTTPException(status_code=401, detail="bad app token")
    return "ok"