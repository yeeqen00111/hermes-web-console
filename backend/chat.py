# -*- coding: utf-8 -*-
"""
Hermes 聊天客户端 —— 只连 dashboard（/api/ws JSON-RPC 桥），不碰 gateway 内部。
流程：已登录 cookie → POST /api/auth/ws-ticket 换单次 ticket → WS /api/ws?ticket=…
     → session.create → prompt.submit → 事件流（message.delta / tool.start / …）

依赖：pip install websockets requests
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
from typing import Any, Callable

import websockets

HERMES_BASE = os.environ.get("HERMES_BASE", "http://localhost:8426")

# server→client 请求（agent 问你问题）；demo 阶段统一取消，避免 agent 半路干等。
_SERVER_REQUEST_METHODS = frozenset({
    "approval", "clarify", "sudo", "secret",
    "vault.code", "vault.unlock_prompt", "connection", "terminal.read", "window.read",
})
# 回合终态事件名
_TERMINAL_EVENTS = frozenset({
    "message.complete", "run.completed", "run.failed", "session.error", "message.error",
})


def _netloc() -> str:
    """把 http(s)://host[:port] 换成 ws(s)://host[:port]（同 netloc）。"""
    scheme, _, rest = HERMES_BASE.partition("://")
    return f"wss://{rest}" if scheme == "https" else f"ws://{rest}"


async def _get_ws_ticket(session, timeout: float = 15) -> str:
    """用已登录的 dashboard cookie 换单次 WS ticket。"""
    r = await asyncio.to_thread(
        session.post, f"{HERMES_BASE}/api/auth/ws-ticket", timeout=timeout,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ws-ticket 失败 HTTP {r.status_code}: {r.text[:300]}")
    body = r.json()
    ticket = body.get("ticket") or (body.get("data") or {}).get("ticket")
    if not ticket:
        raise RuntimeError(f"ws-ticket 响应没有 ticket 字段: {body}")
    return ticket


async def stream_turn(
    session,
    *,
    text: str,
    on_event: Callable[[str, dict], Any],
) -> dict:
    """执行一轮对话：建会话 → 提交 prompt → 事件回调 on_event(type, params) 直至终态。"""
    base = _netloc()
    ticket = await _get_ws_ticket(session)

    ws = await websockets.connect(f"{base}/api/ws?ticket={ticket}")
    try:
        pending: dict[str, asyncio.Future] = {}
        counter = itertools.count(1)
        turn_done = asyncio.Event()
        terminal: dict = {"name": "", "params": {}}

        async def _request(method: str, params: dict | None = None) -> dict:
            """发 JSON-RPC 请求并等待 result；由 reader 循环回填 fut。"""
            rid = f"py{next(counter)}"
            fut = asyncio.get_event_loop().create_future()
            pending[rid] = fut
            await ws.send(json.dumps({
                "jsonrpc": "2.0", "id": rid, "method": method, "params": params or {},
            }))
            try:
                return await asyncio.wait_for(fut, timeout=180)
            except asyncio.TimeoutError:
                pending.pop(rid, None)
                raise

        async def reader() -> None:
            """常驻读帧：分发响应给 pending；事件回调 on_event；server-request 一律取消；终态置位。"""
            while True:
                raw = await ws.recv()
                if not raw:
                    continue
                frame = json.loads(raw)
                if not isinstance(frame, dict):
                    continue
                fid = frame.get("id")
                method = frame.get("method") or ""

                # 1) 我们等的结果
                if fid is not None and fid in pending:
                    fut = pending.pop(fid)
                    if "result" in frame:
                        fut.set_result(frame["result"])
                    else:
                        fut.set_exception(RuntimeError(f"{method} error: {frame.get('error')}"))
                    continue

                # 2) server→client 请求：demo 一律 取消(空 result)
                if fid is not None and method in _SERVER_REQUEST_METHODS:
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": fid, "result": {}}))
                    continue

                # 3) 普通事件 / 通知
                params = frame.get("params") or {}
                if method == "event":
                    # dashboard 的通知帧：{"method":"event","params":{type,payload,session_id,…}}
                    method = params.get("type") or "event"
                    payload = params.get("payload")
                    params = payload if payload is not None else params
                try:
                    await on_event(method, params)
                except Exception:
                    pass  # 回调查错误不应杀死 reader

                if method in _TERMINAL_EVENTS:
                    terminal.update({"name": method, "params": params})
                    turn_done.set()

        reader_task = asyncio.ensure_future(reader())

        try:
            # 1. 建会话
            sid_resp = await _request("session.create")
            session_id = sid_resp.get("session_id") or sid_resp.get("id")
            if not session_id:
                raise RuntimeError(f"session.create 无 session_id: {sid_resp}")

            # 2. 提交对话，等终态
            submit_fut = asyncio.ensure_future(_request("prompt.submit", {
                "session_id": session_id, "text": text}))
            try:
                await asyncio.wait_for(turn_done.wait(), timeout=300)
            except asyncio.TimeoutError:
                submit_fut.cancel()

            if submit_fut.done() and not submit_fut.cancelled():
                try:
                    result = submit_fut.result()
                except Exception as exc:  # noqa: BLE001
                    result = {"error": str(exc)}
            else:
                result = {"error": "prompt.submit 未完成"}

            summary = {
                "ok": True,
                "session_id": session_id,
                "result": result,
                "terminal": terminal.get("name") or "",
            }
        finally:
            reader_task.cancel()
            for fut in pending.values():
                if not fut.done():
                    fut.cancel()
    finally:
        await ws.close()

    return summary