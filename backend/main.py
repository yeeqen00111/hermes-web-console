#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hermes 配置后端（FastAPI demo）
================================
前端唯一入口：前端调 /api/*，后端持有 Hermes 服务账号（.env / 环境变量），
内部访问 dashboard 的 REST 与 JSON-RPC WebSocket。

运行（backend 目录下，读 ../.env 或 backend/.env）：
    D:/application/env/conda/env/py310/python.exe -m uvicorn main:app --port 8000

依赖：pip install fastapi uvicorn requests websockets
"""
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from urllib.parse import quote

import requests

# 保证从任何 cwd / --reload 子进程都能找到同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from config import APP_PASS, APP_TOKEN, APP_USER, hc, require_app_token
from chat import ChatError, ChatManager


@asynccontextmanager
async def lifespan(app):
    app.state.chat_manager = ChatManager(hc)
    try:
        yield
    finally:
        await app.state.chat_manager.close()


app = FastAPI(title="Hermes Config Backend", lifespan=lifespan)
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

# 消息渠道（飞书）路由
from channels import router as channels_router  # noqa: E402
app.include_router(channels_router)

# Agent 身份文件（SOUL / MEMORY / USER，仅 default profile）路由
from agentfiles import router as agentfiles_router  # noqa: E402
app.include_router(agentfiles_router)


class LoginBody(BaseModel):
    username: str
    password: str


# Model ids and provider slugs are interpolated into the gateway's whitespace-delimited
# switch string ("<model> --provider <slug>"), so any whitespace would let a value forge
# extra CLI flags -- "x --global" alone would persist the pick to config.yaml. A leading
# "--" is rejected for the same reason. (pydantic-core's regex engine has no lookahead,
# so the "--" half is a validator rather than part of the pattern.)
CLI_TOKEN = r"^\S+$"


def _no_flag_prefix(value):
    if value is not None and value.startswith("--"):
        raise ValueError("模型与厂商标识不能以 -- 开头")
    return value


@app.post("/api/login")
def login(body: LoginBody):
    """你的系统登录：校验前端账号，返回 app token（后续请求带 Bearer 用）。"""
    if body.username != APP_USER or body.password != APP_PASS:
        raise HTTPException(status_code=401, detail="bad credentials")
    return {"token": APP_TOKEN, "next": "/"}


class ChatBody(BaseModel):
    text: str = Field(min_length=1, max_length=100000)
    stored_session_id: str | None = Field(default=None, min_length=1, max_length=256,
                                          pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    profile: str | None = Field(default=None, min_length=1, max_length=128)
    # Only honoured when the turn creates a fresh session; existing ones switch
    # through POST /api/sessions/{id}/model instead (session.resume takes no model).
    model: str | None = Field(default=None, min_length=1, max_length=256, pattern=CLI_TOKEN)
    provider: str | None = Field(default=None, min_length=1, max_length=256, pattern=CLI_TOKEN)

    @field_validator("text")
    @classmethod
    def nonblank_text(cls, value):
        if not value.strip():
            raise ValueError("消息不能为空")
        return value.strip()

    @field_validator("model", "provider")
    @classmethod
    def cli_safe(cls, value):
        return _no_flag_prefix(value)

    @field_validator("provider")
    @classmethod
    def provider_needs_model(cls, value, info):
        if value and not info.data.get("model"):
            raise ValueError("provider 需与 model 同时提供")
        return value


class ModelSwitchBody(BaseModel):
    model: str = Field(min_length=1, max_length=256, pattern=CLI_TOKEN)
    provider: str = Field(min_length=1, max_length=256, pattern=CLI_TOKEN)
    confirm_expensive_model: bool = False

    @field_validator("model", "provider")
    @classmethod
    def cli_safe(cls, value):
        return _no_flag_prefix(value)


@app.post("/api/chat", dependencies=[Depends(require_app_token)])
async def chat(body: ChatBody, request: Request):
    try:
        session, queue = await request.app.state.chat_manager.start_turn(
            body.text, body.stored_session_id, body.profile, body.model, body.provider)
    except ChatError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except (requests.RequestException, OSError, asyncio.TimeoutError) as exc:
        raise HTTPException(502, "无法连接 dashboard，请稍后重试") from exc

    async def generate():
        try:
            while True:
                try:
                    name, envelope = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: {name}\ndata: {json.dumps(envelope, ensure_ascii=False)}\n\n"
                if name in ("message.complete", "chat.error"):
                    break
        finally:
            # A browser disconnect only detaches its subscriber; it does not stop the upstream turn.
            session.subscribers.discard(queue)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _history_get(path, params):
    try:
        response = hc.request("GET", path, params=params)
        if not response.ok:
            status = response.status_code if response.status_code in (400, 404, 503) else 502
            raise HTTPException(status, "历史会话不存在" if status == 404 else "读取 Hermes 历史失败，请稍后重试")
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(502, "无法读取 Hermes 历史，请稍后重试") from exc


@app.get("/api/sessions", dependencies=[Depends(require_app_token)])
def sessions(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
             profile: str | None = Query(None, min_length=1, max_length=128)):
    return _history_get("/api/sessions", {"limit": limit, "offset": offset, "profile": profile,
                                         "order": "recent", "archived": "exclude", "min_messages": 1})


@app.get("/api/sessions/{session_id}/messages", dependencies=[Depends(require_app_token)])
def session_messages(session_id: str = Path(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
                     limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                     profile: str | None = Query(None, min_length=1, max_length=128)):
    return _history_get(f"/api/sessions/{quote(session_id, safe='')}/messages", {
        "limit": limit, "offset": offset, "order": "latest", "profile": profile, "include_compacted": True,
    })


@app.get("/api/sessions/{session_id}/state", dependencies=[Depends(require_app_token)])
async def session_state(request: Request,
                        session_id: str = Path(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
                        profile: str | None = Query(None, min_length=1, max_length=128)):
    try:
        return await request.app.state.chat_manager.state(session_id, profile)
    except (ChatError, OSError, asyncio.TimeoutError) as exc:
        raise HTTPException(502, "暂时无法确认生成状态，请刷新历史；不会自动重发") from exc


@app.post("/api/sessions/{session_id}/interrupt", dependencies=[Depends(require_app_token)])
async def session_interrupt(request: Request,
                            session_id: str = Path(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
                            profile: str | None = Query(None, min_length=1, max_length=128)):
    """停止本进程内正在生成的回合；中断后的终态经原 SSE 流自然收尾。"""
    try:
        result = await request.app.state.chat_manager.interrupt(session_id, profile)
    except ChatError as exc:
        # 4001 = runtime id 已被 gateway 回收；前端据此提示刷新历史。
        raise HTTPException(exc.status_code, str(exc)) from exc
    except (OSError, asyncio.TimeoutError) as exc:
        raise HTTPException(502, "无法连接 dashboard，请稍后重试") from exc
    return {"ok": True, **result}


@app.post("/api/sessions/{session_id}/model", dependencies=[Depends(require_app_token)])
async def session_model(request: Request, body: ModelSwitchBody,
                        session_id: str = Path(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
                        profile: str | None = Query(None, min_length=1, max_length=128)):
    """切换该会话使用的模型。会话级生效，不改 config.yaml 里的默认模型。

    ``confirm_required`` 表示上游要求二次确认（昂贵模型），原样透传给前端。
    """
    try:
        result = await request.app.state.chat_manager.switch_model(
            session_id, profile, body.model, body.provider, body.confirm_expensive_model)
    except ChatError as exc:
        # 410 = runtime 会话已被 gateway 回收，stored 会话仍在；前端提示刷新历史。
        raise HTTPException(exc.status_code, str(exc)) from exc
    except (OSError, asyncio.TimeoutError) as exc:
        raise HTTPException(502, "无法连接 dashboard，请稍后重试") from exc
    return {"ok": True, **result}


def _guard(resp):
    if not resp.ok:
        raise HTTPException(status_code=502, detail=f"Hermes {resp.status_code}: {resp.text[:500]}")
    return resp.json()


# ── 对外 API：只暴露白名单端点，前端永远碰不到 dashboard 原始面 ──
@app.get("/api/models", dependencies=[Depends(require_app_token)])
def models():
    """模型/供应商清单，标注自定义 provider。"""
    payload = _guard(hc.request("GET", "/api/model/options"))
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


@app.get("/api/custom-endpoints", dependencies=[Depends(require_app_token)])
def custom_endpoints():
    data = _guard(hc.request("GET", "/api/providers/custom-endpoints"))
    rows = data.get("endpoints") if isinstance(data, dict) and isinstance(data.get("endpoints"), list) \
        else data if isinstance(data, list) else [data]
    return {
        "endpoints": [
            {"name": e.get("name"), "base_url": e.get("base_url"),
             "model": e.get("model")}
            for e in rows
        ]
    }


@app.get("/api/status", dependencies=[Depends(require_app_token)])
def dashboard_status():
    """透传 dashboard 综合状态（也顺手当健康检查）。"""
    try:
        return _guard(hc.request("GET", "/api/status"))
    except HTTPException as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail=f"Hermes 不可达: {exc.detail}")


# ── 生产静态资源（Docker 镜像内 vite build 产物）──────────────
# 本机 dev 时 backend/ 外没有 static/ 目录 → 不挂载，前端照旧走 Vite(5173)。
# 镜像里 /app/static 存在 → 挂到 "/"（html=True 直接出 index.html），/api/* 路由
# 已先注册、优先级更高，前端相对路径 fetch("/api/...") 天然同源，无需代理/CORS。
from pathlib import Path  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")