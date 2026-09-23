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

# 保证从任何 cwd / --reload 子进程都能找到同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import APP_PASS, APP_TOKEN, APP_USER, hc, require_app_token

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


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(body: LoginBody):
    """你的系统登录：校验前端账号，返回 app token（后续请求带 Bearer 用）。"""
    if body.username != APP_USER or body.password != APP_PASS:
        raise HTTPException(status_code=401, detail="bad credentials")
    return {"token": APP_TOKEN, "next": "/"}


# ── 对话（只连 dashboard 的 /api/ws JSON-RPC 桥，经 ws-ticket）──────────
class ChatBody(BaseModel):
    text: str


@app.post("/api/chat", dependencies=[Depends(require_app_token)])
async def chat(body: ChatBody):
    """把一条用户消息交给 Hermes，返回 SSE 事件流（message.delta 等）。"""
    from chat import stream_turn

    hc.ensure_logged_in()   # WS 流程开始前必须已登录（否则 ws-ticket 拿不到）

    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(name: str, params: dict) -> None:
        # 只把前端可能要渲染的事件透传；内部 RPC 帧可在此过滤
        if name.startswith(("message.", "tool.", "session.", "run.", "error", "gateway.", "reasoning.")):
            await queue.put((name, params))

    task = asyncio.create_task(
        stream_turn(hc.s, text=body.text, on_event=on_event)
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