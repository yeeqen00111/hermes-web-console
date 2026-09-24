from __future__ import annotations

import asyncio
import itertools
import json
from dataclasses import dataclass, field
from urllib.parse import urlencode

import requests
import websockets

from config import HERMES_BASE


class ChatError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


@dataclass(eq=False)
class LiveSession:
    session_id: str
    stored_session_id: str
    profile: str | None
    status: str = "idle"
    attached: bool = True
    task: asyncio.Task | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    subscribers: set = field(default_factory=set)
    message: str = ""

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def identity(self):
        return {"session_id": self.session_id,
                "stored_session_id": self.stored_session_id, "profile": self.profile}

    def envelope(self, name, payload):
        return {"type": name, **self.identity(), "payload": payload}

    def publish(self, name, envelope):
        for queue in tuple(self.subscribers):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(("chat.error", self.envelope("chat.error", {
                    "message": "接收速度过慢，请刷新历史确认结果；不会自动重发。", "status": "unknown",
                })))
                self.subscribers.discard(queue)
            else:
                queue.put_nowait((name, envelope))


class ChatManager:
    def __init__(self, client):
        self.client = client
        self.ws = None
        self.reader = None
        self.pending = {}
        self.counter = itertools.count(1)
        self.sessions = {}
        self.by_runtime = {}
        self.lock = asyncio.Lock()

    async def _connect(self):
        if self.reader is not None and not self.reader.done():
            return
        try:
            response = await asyncio.to_thread(self.client.request, "POST", "/api/auth/ws-ticket")
            if response.status_code != 200:
                raise ChatError(f"无法取得 dashboard 连接凭证（HTTP {response.status_code}）")
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ChatError("无法读取 dashboard 连接凭证") from exc
        ticket = body.get("ticket") if isinstance(body, dict) else None
        if not isinstance(ticket, str) or not ticket.strip():
            raise ChatError("dashboard 未返回有效连接凭证")
        scheme, _, rest = HERMES_BASE.rstrip("/").partition("://")
        base = f"{'wss' if scheme == 'https' else 'ws'}://{rest}"
        try:
            self.ws = await websockets.connect(f"{base}/api/ws?{urlencode({'ticket': ticket})}")
        except (OSError, websockets.WebSocketException) as exc:
            raise ChatError("无法连接 dashboard 对话通道") from exc
        self.reader = asyncio.create_task(self._read(self.ws))

    async def _request(self, method, params=None):
        if self.reader is None or self.reader.done():
            raise ChatError("dashboard 连接已断开，请重新读取历史后再发送")
        rid = f"py{next(self.counter)}"
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        try:
            await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": rid,
                                           "method": method, "params": params or {}}))
            return await asyncio.wait_for(future, timeout=60)
        finally:
            self.pending.pop(rid, None)
            if not future.done():
                future.cancel()

    def _fail(self, session, message, status="unknown"):
        if session.done.is_set():
            return
        session.status = status
        session.message = message
        session.publish("chat.error", session.envelope("chat.error", {
            "message": message, "status": status,
        }))
        session.done.set()

    async def _read(self, ws):
        try:
            while True:
                frame = json.loads(await ws.recv())
                if not isinstance(frame, dict):
                    continue
                rid, method = frame.get("id"), frame.get("method")
                if rid in self.pending:
                    future = self.pending[rid]
                    if not future.done():
                        if "error" in frame:
                            error = frame["error"]
                            code = 404 if error.get("code") in (4007, 4008) else 502
                            future.set_exception(ChatError(str(error.get("message") or "dashboard 请求被拒绝"), code))
                        else:
                            future.set_result(frame.get("result") or {})
                    continue
                if rid is not None and method:
                    # Never approve a server request implicitly; interactive approvals are a later feature.
                    result = {"choice": "deny"} if method == "approval" else {}
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}))
                    continue
                if method != "event":
                    continue
                event = frame.get("params") or {}
                session = self.by_runtime.get(event.get("session_id"))
                if session is None:
                    continue
                name = event.get("type", "")
                if name not in {"message.start", "message.delta", "message.interim", "message.complete",
                                "reasoning.delta", "tool.start", "tool.complete", "error", "session.title"}:
                    continue
                envelope = {**event, **session.identity()}
                if name == "message.complete":
                    payload = event.get("payload") or {}
                    session.status = payload.get("status", "complete")
                    session.message = str(payload.get("error") or
                                          (payload.get("text") if session.status != "complete" else "") or "")
                    session.done.set()
                session.publish(name, envelope)
        except asyncio.CancelledError:
            raise
        except (OSError, websockets.WebSocketException, ValueError, TypeError):
            pass
        finally:
            for future in tuple(self.pending.values()):
                if not future.done():
                    future.set_exception(ChatError("dashboard 连接中断，提交结果可能未知"))
            for session in set(self.sessions.values()):
                session.attached = False
                if session.active:
                    self._fail(session, "对话连接中断，请刷新历史确认结果；不会自动重发。")
            await ws.close()

    def _remember(self, session, requested_id=None, requested_profile=None):
        self.sessions[(session.profile, session.stored_session_id)] = session
        if requested_id:
            self.sessions[(requested_profile, requested_id)] = session
        self.by_runtime[session.session_id] = session

    async def _live_row(self, session):
        result = await self._request("session.active_list")
        row = next((row for row in result.get("sessions", []) if row.get("id") == session.session_id), None)
        if row and row.get("session_key"):
            session.stored_session_id = row["session_key"]
            self._remember(session)
        return row

    async def start_turn(self, text, stored_session_id=None, profile=None):
        async with self.lock:
            await self._connect()
            session = self.sessions.get((profile, stored_session_id)) if stored_session_id else None
            if session and session.active:
                raise ChatError("该会话仍在生成，请等待完成后再发送", 409)
            if session and session.attached:
                row = await self._live_row(session)
                if row and row.get("status") != "idle":
                    raise ChatError("该会话仍在运行，请刷新历史并等待完成", 409)
                if not row:
                    session.attached = False
            if session is None or not session.attached:
                params = {"profile": profile} if profile else {}
                if stored_session_id:
                    params.update(session_id=stored_session_id, omit_messages=True)
                    result = await self._request("session.resume", params)
                else:
                    result = await self._request("session.create", params)
                runtime_id = result.get("session_id")
                stored_id = result.get("stored_session_id") or result.get("session_key")
                if not runtime_id or not stored_id:
                    raise ChatError("dashboard 未返回完整的会话标识")
                owner = (result.get("info") or {}).get("profile_name") or profile
                if profile is not None and owner != profile:
                    raise ChatError("dashboard 返回的会话归属不匹配，已取消发送")
                existing = self.by_runtime.get(runtime_id)
                if existing and existing.profile != owner:
                    raise ChatError("dashboard 返回的运行时会话归属冲突，已取消发送")
                if existing and existing.active:
                    raise ChatError("该会话仍在生成，请等待完成后再发送", 409)
                session = LiveSession(runtime_id, stored_id, owner)
                self._remember(session, stored_session_id or stored_id, profile)
                if result.get("running") or result.get("inflight"):
                    session.status = "running"
                    raise ChatError("恢复的会话仍在运行，未重复提交消息", 409)
            # Claim before yielding so two HTTP requests cannot submit the same live session concurrently.
            session.status = "running"
            session.message = ""
            session.done.clear()
            queue = asyncio.Queue(maxsize=512)
            session.subscribers.add(queue)
            queue.put_nowait(("session.ready", session.envelope("session.ready", session.identity())))
            session.task = asyncio.create_task(self._run_turn(session, text))
            self._prune()
            return session, queue

    def _prune(self):
        idle = [session for session in self.by_runtime.values() if not session.active]
        for session in idle[:max(0, len(self.by_runtime) - 128)]:
            self.by_runtime.pop(session.session_id, None)
            self.sessions = {key: value for key, value in self.sessions.items() if value is not session}

    async def _run_turn(self, session, text):
        submit = asyncio.create_task(self._request("prompt.submit", {"session_id": session.session_id, "text": text}))
        terminal = asyncio.create_task(session.done.wait())
        try:
            finished, _ = await asyncio.wait((submit, terminal), return_when=asyncio.FIRST_COMPLETED)
            if terminal not in finished:
                result = submit.result()
                if result.get("status") not in (None, "streaming"):
                    raise ChatError("上游未开始独立回合，请刷新历史确认状态；不会自动重发。")
                await asyncio.wait_for(terminal, timeout=300)
        except asyncio.CancelledError:
            self._fail(session, "后端正在关闭，请刷新历史确认结果")
            raise
        except Exception as exc:
            message = str(exc) if isinstance(exc, ChatError) else "对话未完成，请刷新历史确认结果；不会自动重发。"
            self._fail(session, message)
        finally:
            for task in (submit, terminal):
                if not task.done():
                    task.cancel()
            await asyncio.gather(submit, terminal, return_exceptions=True)
            session.subscribers.clear()

    async def state(self, stored_session_id, profile=None):
        session = self.sessions.get((profile, stored_session_id))
        if session is None:
            return {"stored_session_id": stored_session_id, "profile": profile, "status": "unknown"}
        if not session.attached:
            return {**session.identity(), "status": "unknown", "message": session.message}
        if session.active:
            return {**session.identity(), "status": "running"}
        row = await self._live_row(session)
        status = "unknown" if row is None else "running" if row.get("status") != "idle" else session.status
        if row and row.get("status") == "idle" and status == "running":
            status = "idle"
        return {**session.identity(), "status": status, "message": session.message}

    async def close(self):
        tasks = [session.task for session in set(self.sessions.values()) if session.active]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        self.sessions.clear()
        self.by_runtime.clear()
