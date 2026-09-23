#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hermes 配置后端（FastAPI demo）
================================
前端唯一入口：前端带自己的 Bearer token 调 /api/*，
后端持有 Hermes 服务账号（env 里），内部访问 dashboard 的 REST。

运行：
    HERMES_BASE=http://localhost:8426 \
    HERMES_USER=admin \
    HERMES_PASS='<dashboard密码>' \
    python -m uvicorn main:app --host 127.0.0.1 --port 8000

依赖：
    pip install fastapi uvicorn requests
"""
import asyncio
import json
import os
import secrets

import requests
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ── 配置：凭据一律走环境变量，别写死在代码/仓库里 ──────────────
HERMES_BASE = os.environ.get("HERMES_BASE", "http://localhost:8426")
HERMES_USER = os.environ.get("HERMES_USER", "admin")
HERMES_PASS = os.environ.get("HERMES_PASS", "")            # ← 必须用环境变量提供，不设默认值
APP_TOKEN = os.environ.get("APP_TOKEN", secrets.token_urlsafe(32))  # 前端令牌，没给则随机生成

app = FastAPI(title="Hermes Config Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],   # 只放行前端 dev server
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 模型配置（自定义端点 CRUD）路由
from modelcfg import router as modelcfg_router  # noqa: E402
app.include_router(modelcfg_router)

# 你系统的"前端账号"（跟 Hermes 无关，是给前端登录取 token 用的）。正式用 SSO/数据库替换。
APP_USER = os.environ.get("APP_USER", "admin")
APP_PASS = os.environ.get("APP_PASS", "secret")
# demo 阶段默认不鉴权；正式把 APP_AUTH 设为 1 即启用 Bearer token 校验
APP_AUTH = os.environ.get("APP_AUTH", "").strip().lower() in {"1", "true", "yes"}


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(body: LoginBody):
    """你的系统登录：校验前端账号，返回 app token（后续请求带 Bearer 用）。"""
    if body.username != APP_USER or body.password != APP_PASS:
        raise HTTPException(status_code=401, detail="bad credentials")
    return {"token": APP_TOKEN, "next": "/"}


def _require_app_token(authorization: str = Header(default="")) -> str:
    """前端鉴权：demo 默认放行；设 APP_AUTH=1 后要求 Bearer <APP_TOKEN>。
    正式替换成你的用户登录/SSO。"""
    if not APP_AUTH:
        return "open"
    if not HERMES_PASS:
        raise HTTPException(status_code=503, detail="backend 未配置 HERMES_PASS")
    if authorization != f"Bearer {APP_TOKEN}":
        raise HTTPException(status_code=401, detail="bad app token")
    return "ok"


# ── Hermes 客户端：登录一次，401 自动重登重试 ─────────────────
class HermesClient:
    def __init__(self):
        self.s = requests.Session()
        self._login()

    def _login(self):
        r = self.s.post(
            f"{HERMES_BASE}/auth/password-login",
            json={"provider": "basic", "username": HERMES_USER, "password": HERMES_PASS},
            timeout=15,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Hermes 登录失败 HTTP {r.status_code}: {r.text}")

    def request(self, method: str, path: str, **kw) -> requests.Response:
        resp = self.s.request(method, HERMES_BASE + path, timeout=30, **kw)
        if resp.status_code == 401:            # 会话失效（容器重启等）→ 重登一次
            self._login()
            resp = self.s.request(method, HERMES_BASE + path, timeout=30, **kw)
        return resp


_hc = HermesClient()   # 启动即登录一次


# ── 对话（只连 dashboard 的 /api/ws JSON-RPC 桥，经 ws-ticket）──────────
class ChatBody(BaseModel):
    text: str


@app.post("/api/chat", dependencies=[Depends(_require_app_token)])
async def chat(body: ChatBody):
    """把一条用户消息交给 Hermes，返回 SSE 事件流（message.delta 等）。"""
    from chat import stream_turn

    queue: asyncio.Queue = asyncio.Queue()
    done_flag: bool = False

    async def on_event(name: str, params: dict) -> None:
        # 只把前端可能要渲染的事件透传；内部 RPC 帧可在此过滤
        if name.startswith(("message.", "tool.", "session.", "run.", "error", "gateway.", "reasoning.")):
            await queue.put((name, params))

    task = asyncio.create_task(
        stream_turn(_hc.s, text=body.text, on_event=on_event)
    )

    async def generate():
        try:
            sent_terminal = False
            while True:
                try:
                    name, params = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    if task.done():
                        break
                    continue
                payload = json.dumps(params, ensure_ascii=False)
                yield f"event: {name}\ndata: {payload}\n\n"
                if name in ("message.complete", "run.completed", "run.failed",
                            "session.error", "message.error"):
                    sent_terminal = True
                    break
            if not task.done():
                # 终态已发出但任务还在收尾：等它一小会儿
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=15)
                except Exception:
                    pass
            else:
                # 任务已结束：把异常作为 SSE 错误事件吐出（避免静默空响应）
                exc = task.exception()
                if exc is not None:
                    msg = json.dumps({"message": str(exc)}, ensure_ascii=False)
                    yield f"event: error\ndata: {msg}\n\n"
                elif not sent_terminal:
                    yield "event: error\ndata: {\"message\":\"对话结束但未收到终态事件\"}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _guard(resp: requests.Response):
    if not resp.ok:
        raise HTTPException(status_code=502, detail=f"Hermes {resp.status_code}: {resp.text[:500]}")
    return resp.json()


# ── 对外 API：只暴露白名单端点，前端永远碰不到 dashboard 原始面 ──
@app.get("/api/models", dependencies=[Depends(_require_app_token)])
def models():
    """模型/供应商清单，标注自定义 provider。"""
    payload = _guard(_hc.request("GET", "/api/model/options"))
    providers = []
    for p in (payload.get("providers") or []):
        slug = str(p.get("slug") or p.get("id") or p.get("name") or "")
        providers.append({
            "slug": slug,
            "name": p.get("name") or slug,
            "models": p.get("models") or [],
            "custom": "custom" in slug.lower() or "自定义" in (p.get("name") or ""),
        })
    return {"providers": providers}


@app.get("/api/custom-endpoints", dependencies=[Depends(_require_app_token)])
def custom_endpoints():
    data = _guard(_hc.request("GET", "/api/providers/custom-endpoints"))
    rows = data.get("endpoints") if isinstance(data, dict) and isinstance(data.get("endpoints"), list) \
        else data if isinstance(data, list) else [data]
    return {
        "endpoints": [
            {"name": e.get("name"), "base_url": e.get("base_url"),
             "model": e.get("model")}
            for e in rows
        ]
    }


@app.get("/api/status", dependencies=[Depends(_require_app_token)])
def dashboard_status():
    """透传 dashboard 综合状态（也顺手当健康检查）。"""
    try:
        return _guard(_hc.request("GET", "/api/status"))
    except HTTPException as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail=f"Hermes 不可达: {exc.detail}")