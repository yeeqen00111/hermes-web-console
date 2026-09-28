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
                            code = error.get("code")
                            # 4001 = runtime session not in the gateway process
                            # (reaped/evicted). It gets its own HTTP status so
                            # callers can ask the user to refresh instead of
                            # retrying -- but the status must be a real one:
                            # httptools raises KeyError on 4001 and kills the
                            # connection. 410 Gone describes it exactly (the
                            # stored session survives; the runtime one is gone).
                            # 4007/4008 are not-found style codes; anything else
                            # is an upstream rejection -> 502.
                            if code in (4007, 4008):
                                http_code = 404
                            elif code == 4001:
                                http_code = 410
                            else:
                                http_code = 502
                            future.set_exception(ChatError(str(error.get("message") or "dashboard 请求被拒绝"), http_code))
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
                                "reasoning.delta", "tool.start", "tool.complete", "error", "session.title",
                                "session.info"}:
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

    async def _open_live_session(self, stored_session_id, profile, model=None, provider=None, action="发送"):
        """Resume (or create) the runtime session for a stored id. Caller holds self.lock.

        ``model``/``provider`` ride along ONLY on ``session.create``: ``session.resume``
        has no model parameter upstream, so passing one there would be ignored. Existing
        sessions switch models through ``switch_model`` instead.

        Returns ``(session, running)``; a restored session that is still generating is
        reported rather than raised here, because sending must refuse it while switching
        a model may legally queue behind it.
        """
        params = {"profile": profile} if profile else {}
        if stored_session_id:
            params.update(session_id=stored_session_id, omit_messages=True)
            result = await self._request("session.resume", params)
        else:
            if model:
                # Upstream honours provider only alongside an explicit model.
                params.update(model=model, **({"provider": provider} if provider else {}))
            result = await self._request("session.create", params)
        runtime_id = result.get("session_id")
        stored_id = result.get("stored_session_id") or result.get("session_key")
        if not runtime_id or not stored_id:
            raise ChatError("dashboard 未返回完整的会话标识")
        owner = (result.get("info") or {}).get("profile_name") or profile
        if profile is not None and owner != profile:
            raise ChatError(f"dashboard 返回的会话归属不匹配，已取消{action}")
        existing = self.by_runtime.get(runtime_id)
        if existing and existing.profile != owner:
            raise ChatError(f"dashboard 返回的运行时会话归属冲突，已取消{action}")
        if existing and existing.active:
            raise ChatError(f"该会话仍在生成，请等待完成后再{action}", 409)
        running = bool(result.get("running") or result.get("inflight"))
        session = LiveSession(runtime_id, stored_id, owner)
        if running:
            session.status = "running"
        self._remember(session, stored_session_id or stored_id, profile)
        return session, running

    async def start_turn(self, text, stored_session_id=None, profile=None, model=None, provider=None):
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
                session, running = await self._open_live_session(
                    stored_session_id, profile, model=model, provider=provider)
                if running:
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

    async def interrupt(self, stored_session_id, profile=None):
        """Stop the running turn for a session live in this process.

        Only sessions we are actively driving can be interrupted; sessions
        started elsewhere or already finished must be refreshed instead. The
        upstream interrupt is a no-op cleanup on an idle turn (it still
        reports "interrupted"), so we gate on our own active flag rather than
        trusting the upstream result. The terminal message.complete(status=
        "interrupted") then arrives on the existing SSE stream unchanged.
        """
        session = self.sessions.get((profile, stored_session_id))
        if session is None or not session.attached:
            raise ChatError("会话不在本次运行中，请刷新历史后再决定是否停止", 409)
        if not session.active:
            raise ChatError("该会话当前没有正在生成的内容", 409)
        await self._connect()
        try:
            result = await self._request("session.interrupt", {"session_id": session.session_id})
        except ChatError as exc:
            # 410 = the gateway reaped the runtime id (see _read); the stored
            # session persists, so the user should refresh rather than retry.
            if exc.status_code == 410:
                raise ChatError("会话已不在运行态，请刷新历史确认结果", 410) from exc
            raise
        return result

    async def switch_model(self, stored_session_id, profile, model, provider, confirm_expensive=False):
        """Switch one live session's model via config.set (session-scoped, never global).

        ``value`` is the CLI string the official picker sends: ``<model> --provider <slug>``.
        Omitting ``--global`` is what keeps the switch off config.yaml, so the profile
        default is untouched -- that stays the model-config page's job.

        ``config.set`` needs a live runtime session, so a stored id we are not currently
        attached to is resumed first. That is cheap: the gateway skips the agent build
        whenever an explicit provider is given (methods_config_set._set_model), so the
        switch is recorded as a session override and applied when the next turn builds.

        A turn already streaming is fine -- the gateway stashes the pick and reports
        ``deferred``, applying it at the next turn start.
        """
        async with self.lock:
            await self._connect()
            session = self.sessions.get((profile, stored_session_id))
            if session is not None and not session.attached:
                session = None
            if session is None:
                session, _ = await self._open_live_session(stored_session_id, profile, action="切换模型")
            result = await self._request("config.set", {
                "key": "model",
                "value": f"{model} --provider {provider}",
                "session_id": session.session_id,
                "confirm_expensive_model": bool(confirm_expensive),
            })
        return {
            "model": model,
            "provider": provider,
            "value": result.get("value") or model,
            "warning": result.get("warning") or "",
            "confirm_required": bool(result.get("confirm_required")),
            "confirm_message": result.get("confirm_message") or "",
            "deferred": bool(result.get("deferred")),
            "scope": result.get("scope") or "session",
        }

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
