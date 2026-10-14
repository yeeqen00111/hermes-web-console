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
    # 两个文件都读（2026-10-12 修：原来「命中第一个就 break」，一旦 backend/.env 存在，
    # 根 .env 整份就不读了——HERMES_BASE/USER/PASS 全丢，所有走 dashboard 的接口静默落到默认值）。
    # 优先级不变：先读 backend/.env，后读根 .env；setdefault 让「先读到的赢」，
    # 真实环境变量仍旧优先于任何文件。
    for p in (_BACKEND_DIR / ".env", _BACKEND_DIR.parent / ".env"):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()

# ── 配置常量 ──
HERMES_BASE = os.environ.get("HERMES_BASE", "http://localhost:8426")
HERMES_USER = os.environ.get("HERMES_USER", "admin")
HERMES_PASS = os.environ.get("HERMES_PASS", "")            # 必须提供（.env 或环境变量）
APP_TOKEN = os.environ.get("APP_TOKEN", secrets.token_urlsafe(32))

APP_USER = os.environ.get("APP_USER", "admin")
APP_PASS = os.environ.get("APP_PASS", "secret")
APP_AUTH = os.environ.get("APP_AUTH", "").strip().lower() in {"1", "true", "yes"}

# ── 智能问数：共享的数据库注册表（与 hermes-mcp 同一个 SQLite 文件）──────────────
# 两端各自直读直写这个文件，没有网络调用、没有 import 依赖；唯一的约定是
# 「表结构 + 密码加密格式」，见 backend/dbconn.py 的模块注释。
# 相对路径按 backend/ 目录解析（不受启动时工作目录影响）。
# 默认值与 hermes-mcp 的默认路径一致（它未设 HERMESMCP_DB_CONFIG 时就是 <项目根>/db_connections.db）。
_db_config_raw = os.environ.get("HERMESMCP_DB_CONFIG", "").strip()
HERMESMCP_DB_PATH = Path(_db_config_raw) if _db_config_raw else (_BACKEND_DIR.parent / "hermes-mcp" / "db_connections.db")
if not HERMESMCP_DB_PATH.is_absolute():
    HERMESMCP_DB_PATH = (_BACKEND_DIR / HERMESMCP_DB_PATH).resolve()

# 存库密码的加密 key，必须与 hermes-mcp 侧完全一致；缺失时不静默降级（拒绝保存密码）。
HERMESMCP_DB_KEY = os.environ.get("HERMESMCP_DB_KEY", "").strip()

# Hermes 上注册的 MCP 服务名（问数那条）。服务名要进代理路径，故做成配置项，不写死在代码里。
MCP_SERVER_NAME = os.environ.get("MCP_SERVER_NAME", "sql-coustm").strip() or "sql-coustm"


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