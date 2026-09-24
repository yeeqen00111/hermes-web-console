# -*- coding: utf-8 -*-
"""Offline contract tests for the shared dashboard ChatManager.

Run with the requested Python using -B (do not create bytecode files).
Only chat.py is imported: config is replaced before import, so no .env is read.
RPC fixtures distinguish runtime session_id from stored_session_id;
active_list rows use id/session_key and working/waiting/idle, without profile.
No implementation fallback or skip: missing public classes must produce RED.
"""
from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests
import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK


BASE = "https://dashboard.invalid"
TIMEOUT = 2.0


def rpc_session(runtime="runtime-created", stored="stored-created", profile="alpha"):
    return {
        "session_id": runtime,
        "stored_session_id": stored,
        "info": {"profile_name": profile},
        "running": False,
        "inflight": False,
    }


def active_session(runtime, stored, *, status="idle"):
    return {
        "id": runtime,
        "session_key": stored,
        "status": status,
    }


class FakeTicketResponse:
    def __init__(self, body=None, status_code=200):
        self.body = {"ticket": "fake-ticket"} if body is None else body
        self.status_code = status_code
        self.text = json.dumps(self.body)
        self.ok = 200 <= status_code < 400
        self.json_error = None

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return deepcopy(self.body)

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"ticket HTTP {self.status_code}", response=self)


class FakeClient:
    def __init__(self):
        self.calls = []
        self.response = FakeTicketResponse()
        self.error = None

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs, threading.get_ident()))
        if (method, path) != ("POST", "/api/auth/ws-ticket"):
            raise AssertionError(f"Unexpected HTTP request: {method} {path}")
        if self.error is not None:
            raise self.error
        return self.response


class FakeWebSocket:
    """Queue-backed JSON-RPC bridge; prompt.submit ACK never emits a terminal."""

    def __init__(self):
        self.incoming = asyncio.Queue()
        self.calls = []
        self.sent = []
        self.changed = asyncio.Condition()
        self.replies = {}
        self.hold = set()
        self.created = rpc_session()
        self.resumed = rpc_session("runtime-resumed", "stored-resumed", "beta")
        self.active = []
        self.reader_tasks = set()
        self.receiving = 0
        self.max_receiving = 0
        self.close_calls = 0
        self.closed = False

    def calls_for(self, method):
        return [call for call in self.calls if call["method"] == method]

    def plan_result(self, method, result):
        self.replies.setdefault(method, deque()).append(("result", deepcopy(result)))

    def plan_error(self, method, message):
        self.replies.setdefault(method, deque()).append(
            ("error", {"code": -32000, "message": message})
        )

    def reply(self, call, result):
        self.incoming.put_nowait(json.dumps({
            "jsonrpc": "2.0", "id": call["id"], "result": deepcopy(result),
        }))

    async def send(self, raw):
        frame = json.loads(raw)
        self.sent.append(frame)
        if "method" not in frame:  # Reply to a server-originated RPC.
            return
        if "id" not in frame or frame.get("jsonrpc") != "2.0":
            raise AssertionError(f"Invalid JSON-RPC request: {frame!r}")
        self.calls.append(frame)
        method = frame["method"]
        if method not in self.hold:
            planned = self.replies.get(method)
            if planned:
                kind, result = planned.popleft()
            else:
                kind = "result"
                if method == "session.create":
                    result = self.created
                elif method == "session.resume":
                    result = self.resumed
                elif method == "session.active_list":
                    result = {"sessions": self.active}
                elif method == "prompt.submit":
                    result = {"status": "streaming"}
                else:
                    raise AssertionError(f"Unexpected RPC: {method}")
            self.incoming.put_nowait(json.dumps({
                "jsonrpc": "2.0", "id": frame["id"], kind: deepcopy(result),
            }))
        async with self.changed:
            self.changed.notify_all()

    async def wait_calls(self, method, count=1):
        async with self.changed:
            await asyncio.wait_for(
                self.changed.wait_for(lambda: len(self.calls_for(method)) >= count),
                TIMEOUT,
            )
        return self.calls_for(method)[count - 1]

    async def recv(self):
        self.reader_tasks.add(asyncio.current_task())
        self.receiving += 1
        self.max_receiving = max(self.max_receiving, self.receiving)
        try:
            item = await self.incoming.get()
            if isinstance(item, BaseException):
                raise item
            return item
        finally:
            self.receiving -= 1

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.recv()
        except ConnectionClosedOK:
            raise StopAsyncIteration

    def emit(self, name, runtime, stored, profile, payload):
        envelope = {
            "type": name,
            "session_id": runtime,
            "stored_session_id": stored,
            "profile": profile,
            "payload": deepcopy(payload),
        }
        self.incoming.put_nowait(json.dumps({
            "jsonrpc": "2.0", "method": "event", "params": envelope,
        }, ensure_ascii=False))
        return envelope

    def disconnect(self):
        self.closed = True
        self.incoming.put_nowait(ConnectionClosedError(None, None))

    async def close(self, *args, **kwargs):
        self.close_calls += 1
        self.closed = True
        self.incoming.put_nowait(ConnectionClosedOK(None, None))


class FakeConnection:
    """Supports both await connect(...) and async with connect(...)."""

    def __init__(self, connector):
        self.connector = connector

    async def open(self):
        if self.connector.error is not None:
            raise self.connector.error
        return self.connector.ws

    def __await__(self):
        return self.open().__await__()

    async def __aenter__(self):
        return await self.open()

    async def __aexit__(self, *exc):
        await self.connector.ws.close()


class FakeConnector:
    def __init__(self, ws):
        self.ws = ws
        self.calls = []
        self.error = None

    def __call__(self, url, *args, **kwargs):
        self.calls.append((url, args, kwargs))
        return FakeConnection(self)


def load_chat_without_config():
    fake_config = types.ModuleType("config")
    fake_config.HERMES_BASE = BASE
    name = "_offline_chat_contract_target"
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().with_name("chat.py")
    )
    module = importlib.util.module_from_spec(spec)
    # Register during execution for dataclasses and postponed type annotations.
    with patch.dict(sys.modules, {
        "config": fake_config, "backend.config": fake_config, name: module,
    }):
        spec.loader.exec_module(module)
    return module


class FakeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    async def test_prompt_ack_requires_explicit_terminal_event(self):
        ws = FakeWebSocket()
        await ws.send(json.dumps({
            "jsonrpc": "2.0", "id": "test-1", "method": "prompt.submit",
            "params": {"session_id": "runtime-1", "text": "hello"},
        }))
        self.assertEqual(json.loads(await ws.recv()), {
            "jsonrpc": "2.0", "id": "test-1", "result": {"status": "streaming"},
        })
        self.assertTrue(ws.incoming.empty(), "ACK must not synthesize completion")
        envelope = ws.emit("message.complete", "runtime-1", "stored-1", "alpha", {
            "status": "complete", "text": "done",
        })
        self.assertEqual(json.loads(await ws.recv())["params"], envelope)

    async def test_rpc_error_and_disconnect_are_delivered_by_recv(self):
        ws = FakeWebSocket()
        ws.plan_error("session.create", "creation refused")
        await ws.send(json.dumps({
            "jsonrpc": "2.0", "id": "test-2", "method": "session.create",
            "params": {},
        }))
        frame = json.loads(await ws.recv())
        self.assertEqual(frame["id"], "test-2")
        self.assertEqual(frame["error"]["message"], "creation refused")
        self.assertNotIn("result", frame)
        ws.disconnect()
        with self.assertRaises(ConnectionClosedError):
            await ws.recv()

    async def test_ticket_response_is_copy_isolated_and_reports_http_errors(self):
        response = FakeTicketResponse()
        body = response.json()
        body["ticket"] = "modified"
        self.assertEqual(response.json(), {"ticket": "fake-ticket"})
        with self.assertRaises(requests.HTTPError):
            FakeTicketResponse({}, 403).raise_for_status()


class ChatManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Avoid Windows debug-stack overhead dominating short offline deadlines.
        asyncio.get_running_loop().set_debug(False)
        self.ws = FakeWebSocket()
        self.client = FakeClient()
        self.connector = FakeConnector(self.ws)
        self.loop_thread = threading.get_ident()
        self.tasks = []
        self.manager = None
        # Install before importing chat.py. Neither config nor a real network
        # request is allowed, even when the implementation is incomplete.
        for patcher in (
            patch.object(websockets, "connect", self.connector),
            patch("requests.sessions.Session.request", side_effect=AssertionError(
                "Real HTTP is forbidden in test_chat.py"
            )),
            patch("socket.create_connection", side_effect=AssertionError(
                "Real TCP is forbidden in test_chat.py"
            )),
            patch("socket.socket.connect", side_effect=AssertionError(
                "Real socket connections are forbidden in test_chat.py"
            )),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.chat = load_chat_without_config()
        self.assertTrue(
            hasattr(self.chat, "ChatManager") and hasattr(self.chat, "ChatError"),
            "RED: backend/chat.py must export ChatManager and ChatError",
        )
        self.manager = self.chat.ChatManager(self.client)

    async def asyncTearDown(self):
        try:
            if self.manager is not None:
                await asyncio.wait_for(self.manager.close(), TIMEOUT)
        finally:
            # Test-owned start/subscription tasks only; normal browser detach
            # tests must never cancel the LiveSession's background task.
            for task in self.tasks:
                if not task.done():
                    task.cancel()
            if self.tasks:
                await asyncio.gather(*self.tasks, return_exceptions=True)

    def background(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task

    async def next_event(self, queue, expected_name):
        item = await asyncio.wait_for(queue.get(), TIMEOUT)
        self.assertIsInstance(item, tuple)
        self.assertEqual(len(item), 2)
        name, envelope = item
        self.assertEqual(name, expected_name)
        self.assertIsInstance(envelope, dict)
        for key in ("payload", "session_id", "stored_session_id", "profile"):
            self.assertIn(key, envelope)
        return envelope

    def assert_identity(self, envelope, session):
        self.assertEqual(envelope["session_id"], session.session_id)
        self.assertEqual(envelope["stored_session_id"], session.stored_session_id)
        self.assertEqual(envelope["profile"], session.profile)

    async def start(self, text="hello", stored_session_id=None, profile=None):
        result = await asyncio.wait_for(self.manager.start_turn(
            text, stored_session_id=stored_session_id, profile=profile,
        ), TIMEOUT)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        session, queue = result
        for field in (
            "session_id", "stored_session_id", "profile", "status", "task", "subscribers",
        ):
            self.assertTrue(hasattr(session, field), field)
        self.assertIsInstance(queue, asyncio.Queue)
        self.assertIsInstance(session.task, asyncio.Task)
        ready = await self.next_event(queue, "session.ready")
        self.assert_identity(ready, session)
        self.assert_identity(ready["payload"], session)
        return session, queue

    async def submitted(self, session, text="hello", count=1):
        call = await self.ws.wait_calls("prompt.submit", count)
        self.assertEqual(call["params"]["session_id"], session.session_id)
        self.assertEqual(call["params"]["text"], text)
        self.assertFalse(session.task.done(), "prompt.submit ACK is not a terminal")
        return call

    async def complete(self, session, queue, status="complete"):
        payload = {"status": status, "text": "finished"}
        if status == "error":
            payload["error"] = "model refused the turn"
        expected = self.ws.emit(
            "message.complete", session.session_id, session.stored_session_id,
            session.profile, payload,
        )
        actual = await self.next_event(queue, "message.complete")
        self.assertEqual(actual, expected, "Forward the full original envelope")
        await asyncio.wait_for(asyncio.shield(session.task), TIMEOUT)
        self.assertFalse(session.task.cancelled())
        self.assertEqual(session.status, status)
        return actual

    async def assert_chat_error(self, awaitable, status=502, message=None):
        with self.assertRaises(self.chat.ChatError) as caught:
            await asyncio.wait_for(awaitable, TIMEOUT)
        self.assertEqual(caught.exception.status_code, status)
        if message is not None:
            self.assertIn(message, str(caught.exception))
        return caught.exception

    async def assert_background_error(self, session, queue, message=None):
        envelope = await self.next_event(queue, "chat.error")
        self.assert_identity(envelope, session)
        payload = envelope["payload"]
        self.assertIsInstance(payload, dict)
        self.assertTrue(payload.get("message") or payload.get("error"))
        if message is not None:
            self.assertIn(message, json.dumps(payload, ensure_ascii=False))
        try:
            await asyncio.wait_for(asyncio.shield(session.task), TIMEOUT)
        except self.chat.ChatError as exc:
            self.assertEqual(exc.status_code, 502)
        self.assertFalse(session.task.cancelled())
        self.assertNotIn(session.status, ("running", "busy"))
        self.assertTrue(queue.empty(), "Failure must not also emit successful completion")

    async def idle_session(self):
        session, queue = await self.start()
        await self.submitted(session)
        await self.complete(session, queue)
        session.subscribers.discard(queue)
        self.ws.active = [active_session(
            session.session_id, session.stored_session_id,
        )]
        return session

    async def test_chat_error_message_default_and_busy_status(self):
        error = self.chat.ChatError("upstream unavailable")
        self.assertEqual(str(error), "upstream unavailable")
        self.assertEqual(error.status_code, 502)
        self.assertEqual(self.chat.ChatError("busy", status_code=409).status_code, 409)

    async def test_create_uses_distinct_ids_and_server_profile_and_threaded_ticket(self):
        session, queue = await self.start()
        self.assertIn(queue, session.subscribers)
        self.assertEqual(session.session_id, "runtime-created")
        self.assertEqual(session.stored_session_id, "stored-created")
        self.assertEqual(session.profile, "alpha", "Use info.profile_name even without a request profile")
        self.assertIsNone(self.ws.calls_for("session.create")[0]["params"].get("profile"))
        await self.submitted(session)
        self.assertTrue(queue.empty(), "Only ready is emitted before upstream events")
        self.assertEqual(len(self.client.calls), 1)
        method, path, _, thread_id = self.client.calls[0]
        self.assertEqual((method, path), ("POST", "/api/auth/ws-ticket"))
        self.assertNotEqual(thread_id, self.loop_thread, "Ticket I/O must run in a thread")
        self.assertEqual(len(self.connector.calls), 1)
        target = urlsplit(self.connector.calls[0][0])
        self.assertEqual((target.scheme, target.netloc, target.path),
                         ("wss", "dashboard.invalid", "/api/ws"))
        self.assertEqual(parse_qs(target.query), {"ticket": ["fake-ticket"]})
        await self.complete(session, queue)

    async def test_explicit_profile_is_forwarded_to_session_create(self):
        session, queue = await self.start(profile="alpha")
        self.assertEqual(self.ws.calls_for("session.create")[0]["params"]["profile"], "alpha")
        self.assertEqual(session.profile, "alpha")
        await self.submitted(session)
        await self.complete(session, queue)

    async def test_explicit_profile_mismatch_on_create_or_resume_never_submits(self):
        for method, stored_id, profile, result in (
            ("session.create", None, "beta", self.ws.created),
            ("session.resume", "stored-resumed", "alpha", self.ws.resumed),
        ):
            with self.subTest(method=method):
                await self.assert_chat_error(self.manager.start_turn(
                    "must not submit", stored_id, profile,
                ), message="归属不匹配")
                calls = self.ws.calls_for(method)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["params"].get("profile"), profile)
                self.assertEqual(self.ws.calls_for("prompt.submit"), [])
                for owner in (profile, result["info"]["profile_name"]):
                    state = await asyncio.wait_for(self.manager.state(
                        result["stored_session_id"], owner,
                    ), TIMEOUT)
                    self.assertEqual(state["status"], "unknown")

    async def test_none_optional_arguments_create_a_session(self):
        session, queue = await self.start(stored_session_id=None, profile=None)
        self.assertEqual(len(self.ws.calls_for("session.create")), 1)
        self.assertEqual(self.ws.calls_for("session.resume"), [])
        self.assertIsNone(self.ws.calls_for("session.create")[0]["params"].get("profile"))
        await self.submitted(session)
        await self.complete(session, queue)

    async def test_two_turns_reuse_connection_and_runtime_after_active_list(self):
        first = await self.idle_session()
        old_task = first.task
        checkpoint = len(self.ws.calls)
        second, queue = await self.start("second turn", first.stored_session_id, first.profile)
        self.assertEqual(second.session_id, first.session_id)
        self.assertEqual(second.stored_session_id, first.stored_session_id)
        self.assertIsNot(second.task, old_task)
        await self.submitted(second, "second turn", count=2)
        methods = [call["method"] for call in self.ws.calls[checkpoint:]]
        self.assertIn("session.active_list", methods)
        self.assertLess(methods.index("session.active_list"), methods.index("prompt.submit"))
        self.assertEqual(len(self.ws.calls_for("session.create")), 1)
        self.assertEqual(self.ws.calls_for("session.resume"), [])
        self.assertEqual(len(self.connector.calls), 1)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(len(self.ws.reader_tasks), 1, "Keep one reader across turns")
        self.assertEqual(self.ws.max_receiving, 1)
        await self.complete(second, queue)

    async def test_unknown_stored_session_resumes_with_profile_and_omit_messages(self):
        session, queue = await self.start("continued", "stored-resumed", "beta")
        self.assertEqual(self.ws.calls_for("session.create"), [])
        self.assertEqual(self.ws.calls_for("session.resume")[0]["params"], {
            "session_id": "stored-resumed", "profile": "beta", "omit_messages": True,
        })
        self.assertEqual(session.session_id, "runtime-resumed")
        self.assertEqual(session.stored_session_id, "stored-resumed")
        self.assertEqual(session.profile, "beta")
        await self.submitted(session, "continued")
        await self.complete(session, queue)

    async def test_resume_without_profile_uses_server_profile(self):
        session, queue = await self.start(stored_session_id="stored-resumed")
        params = self.ws.calls_for("session.resume")[0]["params"]
        self.assertEqual(params["session_id"], "stored-resumed")
        self.assertTrue(params["omit_messages"])
        self.assertIsNone(params.get("profile"))
        self.assertEqual(session.profile, "beta")
        await self.submitted(session)
        await self.complete(session, queue)

    async def test_local_busy_rejects_duplicate_without_submitting(self):
        session, queue = await self.start()
        await self.submitted(session)
        await self.assert_chat_error(self.manager.start_turn(
            "duplicate", session.stored_session_id, session.profile,
        ), status=409)
        self.assertEqual(len(self.ws.calls_for("prompt.submit")), 1)
        self.assertFalse(session.task.done())
        await self.complete(session, queue)

    async def test_concurrent_resume_allows_only_one_turn_and_returns_busy_409(self):
        self.ws.hold.add("session.resume")
        first = self.background(self.manager.start_turn("first", "stored-resumed", "beta"))
        held_call = await self.ws.wait_calls("session.resume")
        entered = asyncio.Event()

        async def racing_start():
            entered.set()
            return await self.manager.start_turn("racing second", "stored-resumed", "beta")

        second = self.background(racing_start())
        await asyncio.wait_for(entered.wait(), TIMEOUT)
        self.assertEqual(self.ws.calls_for("prompt.submit"), [])
        self.ws.hold.discard("session.resume")
        self.ws.reply(held_call, self.ws.resumed)
        session, queue = await asyncio.wait_for(asyncio.shield(first), TIMEOUT)
        await self.assert_chat_error(asyncio.shield(second), status=409)
        self.assertEqual(len(self.ws.calls_for("session.resume")), 1)
        ready = await self.next_event(queue, "session.ready")
        self.assert_identity(ready, session)
        await self.submitted(session, "first")
        self.assertEqual(len(self.ws.calls_for("prompt.submit")), 1)
        await self.complete(session, queue)

    async def assert_cached_upstream_busy(self, status):
        session = await self.idle_session()
        self.ws.active[0]["status"] = status
        count = len(self.ws.calls_for("session.active_list"))
        await self.assert_chat_error(self.manager.start_turn(
            "must not submit", session.stored_session_id, session.profile,
        ), status=409)
        self.assertGreater(len(self.ws.calls_for("session.active_list")), count)
        self.assertEqual(len(self.ws.calls_for("prompt.submit")), 1)

    async def test_cached_runtime_working_rejects_next_turn(self):
        await self.assert_cached_upstream_busy("working")

    async def test_cached_runtime_waiting_rejects_next_turn(self):
        await self.assert_cached_upstream_busy("waiting")

    async def assert_resumed_upstream_busy(self, flag):
        self.ws.resumed[flag] = True
        self.ws.active = [active_session(
            "runtime-resumed", "stored-resumed",
            status="working" if flag == "running" else "waiting",
        )]
        await self.assert_chat_error(self.manager.start_turn(
            "must not submit", "stored-resumed", "beta",
        ), status=409)
        self.assertEqual(len(self.ws.calls_for("session.resume")), 1)
        self.assertEqual(self.ws.calls_for("prompt.submit"), [])

    async def test_resumed_runtime_running_rejects_turn(self):
        await self.assert_resumed_upstream_busy("running")

    async def test_resumed_runtime_inflight_rejects_turn(self):
        await self.assert_resumed_upstream_busy("inflight")

    async def test_create_rpc_error_is_immediate_chat_error_502(self):
        self.ws.plan_error("session.create", "creation refused")
        await self.assert_chat_error(self.manager.start_turn("hello"), message="creation refused")
        self.assertEqual(self.ws.calls_for("prompt.submit"), [])

    async def test_resume_rpc_error_is_immediate_chat_error_502(self):
        self.ws.plan_error("session.resume", "resume refused")
        await self.assert_chat_error(self.manager.start_turn(
            "hello", "stored-resumed", "beta",
        ), message="resume refused")
        self.assertEqual(self.ws.calls_for("prompt.submit"), [])

    async def test_active_list_rpc_error_prevents_second_submit(self):
        session = await self.idle_session()
        self.ws.plan_error("session.active_list", "active list unavailable")
        await self.assert_chat_error(self.manager.start_turn(
            "hello", session.stored_session_id, session.profile,
        ), message="active list unavailable")
        self.assertEqual(len(self.ws.calls_for("prompt.submit")), 1)

    async def test_prompt_rpc_error_reaches_queue_without_waiting_for_terminal(self):
        self.ws.plan_error("prompt.submit", "prompt refused")
        session, queue = await self.start()
        await self.ws.wait_calls("prompt.submit")
        await self.assert_background_error(session, queue, "prompt refused")
        self.assertTrue(self.ws.incoming.empty())

    async def test_ticket_http_rejection_is_chat_error_and_never_connects(self):
        self.client.response = FakeTicketResponse({"error": "denied"}, 403)
        await self.assert_chat_error(self.manager.start_turn("hello"))
        self.assertEqual(self.connector.calls, [])
        self.assertEqual(self.ws.calls, [])

    async def test_ticket_network_error_is_chat_error_and_never_connects(self):
        self.client.error = requests.ConnectionError("fake ticket connection failure")
        await self.assert_chat_error(self.manager.start_turn("hello"))
        self.assertEqual(self.connector.calls, [])

    async def test_missing_or_empty_ticket_never_connects(self):
        for body in ({}, {"ticket": ""}, {"ticket": None}):
            with self.subTest(body=body):
                self.client.response = FakeTicketResponse(body)
                await self.assert_chat_error(self.manager.start_turn("hello"))
                self.assertEqual(self.connector.calls, [])

    async def test_non_string_or_whitespace_ticket_never_connects(self):
        for ticket in (False, True, 0, 123, 1.5, [], ["ticket"], {},
                       {"value": "ticket"}, " ", "\t\r\n", "\u3000"):
            with self.subTest(ticket=ticket):
                self.client.response = FakeTicketResponse({"ticket": ticket})
                count = len(self.client.calls)
                await self.assert_chat_error(self.manager.start_turn("hello"))
                self.assertEqual(len(self.client.calls), count + 1)
                self.assertEqual(self.connector.calls, [])
                self.assertEqual(self.ws.calls, [])

    async def test_invalid_ticket_json_is_chat_error(self):
        self.client.response.json_error = ValueError("invalid ticket JSON")
        await self.assert_chat_error(self.manager.start_turn("hello"))
        self.assertEqual(self.connector.calls, [])

    async def test_invalid_ticket_body_types_are_chat_errors(self):
        for body in ([], "not a mapping", 123):
            with self.subTest(body=body):
                self.client.response = FakeTicketResponse(body)
                await self.assert_chat_error(self.manager.start_turn("hello"))
                self.assertEqual(self.connector.calls, [])

    async def test_websocket_connect_failure_is_chat_error_502(self):
        self.connector.error = OSError("fake WS connection failure")
        await self.assert_chat_error(self.manager.start_turn("hello"))
        self.assertEqual(len(self.connector.calls), 1)
        self.assertEqual(self.ws.calls, [])

    async def test_disconnect_after_ack_emits_chat_error_and_finishes_task(self):
        session, queue = await self.start()
        await self.submitted(session)
        self.ws.disconnect()
        await self.assert_background_error(session, queue)

    async def test_disconnect_while_prompt_rpc_pending_unblocks_task(self):
        self.ws.hold.add("prompt.submit")
        session, queue = await self.start()
        await self.submitted(session)
        self.ws.disconnect()
        await self.assert_background_error(session, queue)

    async def test_terminal_before_prompt_ack_finishes_turn_and_clears_pending(self):
        self.ws.hold.add("prompt.submit")
        session, queue = await self.start()
        call = await self.submitted(session)
        self.assertEqual(set(self.manager.pending), {call["id"]})
        await self.complete(session, queue)
        self.assertEqual(session.status, "complete")
        self.assertEqual(self.manager.pending, {})
        self.assertTrue(queue.empty())
        # A late ACK must be harmless; the state RPC is a FIFO reader barrier.
        self.ws.reply(call, {"status": "streaming"})
        self.ws.active = [active_session(
            session.session_id, session.stored_session_id,
        )]
        state = await asyncio.wait_for(self.manager.state(
            session.stored_session_id, session.profile,
        ), TIMEOUT)
        self.assertEqual(state["status"], "complete")
        self.assert_identity(state, session)
        self.assertEqual(self.manager.pending, {})
        self.assertEqual(len(self.ws.calls_for("prompt.submit")), 1)

    async def test_disconnect_while_create_rpc_pending_unblocks_start(self):
        self.ws.hold.add("session.create")
        start_task = self.background(self.manager.start_turn("hello"))
        await self.ws.wait_calls("session.create")
        self.ws.disconnect()
        await self.assert_chat_error(asyncio.shield(start_task))
        self.assertEqual(self.ws.calls_for("prompt.submit"), [])

    async def test_concurrent_sessions_share_ws_and_do_not_receive_each_others_events(self):
        self.ws.plan_result("session.create", rpc_session("runtime-a", "stored-a", "alpha"))
        self.ws.plan_result("session.create", rpc_session("runtime-b", "stored-b", "beta"))
        starts = [
            self.background(self.start("first", profile="alpha")),
            self.background(self.start("second", profile="beta")),
        ]
        (first, qa), (second, qb) = await asyncio.wait_for(
            asyncio.gather(*starts), TIMEOUT,
        )
        await self.ws.wait_calls("prompt.submit", 2)
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(len(self.connector.calls), 1)
        self.assertEqual(len(self.ws.reader_tasks), 1)
        self.assertEqual(self.ws.max_receiving, 1)
        # Foreign terminal comes first. Following delta events form a FIFO
        # processing barrier without sleeps or polling.
        self.ws.emit("message.complete", "foreign-runtime", "foreign-stored", "alpha",
                     {"status": "complete"})
        eb = self.ws.emit("message.delta", second.session_id, second.stored_session_id,
                          second.profile, {"text": "only second"})
        ea = self.ws.emit("message.delta", first.session_id, first.stored_session_id,
                          first.profile, {"text": "only first"})
        self.assertEqual(await self.next_event(qa, "message.delta"), ea)
        self.assertEqual(await self.next_event(qb, "message.delta"), eb)
        self.assertTrue(qa.empty())
        self.assertTrue(qb.empty())
        self.assertFalse(first.task.done())
        self.assertFalse(second.task.done())
        await self.complete(second, qb)
        self.assertFalse(first.task.done(), "Another runtime's terminal cannot finish this turn")
        self.assertTrue(qa.empty())
        await self.complete(first, qa)

    async def test_error_terminal_status_and_full_payload_are_preserved(self):
        session, queue = await self.start()
        await self.submitted(session)
        envelope = await self.complete(session, queue, status="error")
        self.assertEqual(session.status, "error")
        self.assertEqual(envelope["payload"]["error"], "model refused the turn")
        self.assertTrue(queue.empty())

    async def test_error_terminal_text_is_available_in_state_message(self):
        session, queue = await self.start()
        await self.submitted(session)
        text = "模型拒绝请求：café 'quoted'\n请稍后重试"
        envelope = self.ws.emit(
            "message.complete", session.session_id, session.stored_session_id,
            session.profile, {"status": "error", "text": text},
        )
        self.assertEqual(await self.next_event(queue, "message.complete"), envelope)
        await asyncio.wait_for(asyncio.shield(session.task), TIMEOUT)
        self.assertFalse(session.task.cancelled())
        self.assertEqual(session.status, "error")
        self.ws.active = [active_session(
            session.session_id, session.stored_session_id,
        )]
        state = await asyncio.wait_for(self.manager.state(
            session.stored_session_id, session.profile,
        ), TIMEOUT)
        self.assert_identity(state, session)
        self.assertEqual(state["status"], "error")
        self.assertEqual(state["message"], text)
        self.assertTrue(queue.empty())

    async def test_interrupted_terminal_status_is_not_rewritten_as_complete(self):
        session, queue = await self.start()
        await self.submitted(session)
        await self.complete(session, queue, status="interrupted")
        self.assertEqual(session.status, "interrupted")
        self.assertTrue(queue.empty())

    async def test_cancelled_sse_subscriber_detaches_without_cancelling_turn(self):
        session, queue = await self.start()
        await self.submitted(session)
        task = session.task
        listening = asyncio.Event()

        async def browser_subscription():
            try:
                listening.set()
                while True:
                    await queue.get()
            finally:
                session.subscribers.discard(queue)

        browser = self.background(browser_subscription())
        await asyncio.wait_for(listening.wait(), TIMEOUT)
        browser.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await browser
        self.assertNotIn(queue, session.subscribers)
        self.assertIs(session.task, task)
        self.assertFalse(task.done())
        self.ws.emit("message.complete", session.session_id, session.stored_session_id,
                     session.profile, {"status": "complete", "text": "offline result"})
        await asyncio.wait_for(asyncio.shield(task), TIMEOUT)
        self.assertFalse(task.cancelled())
        self.assertNotIn(session.status, ("running", "busy"))
        self.assertTrue(queue.empty(), "Detached subscribers no longer receive events")

    async def test_slow_subscriber_overflow_errors_and_detaches_without_cancelling_turn(self):
        session, slow = await self.start()
        await self.submitted(session)
        task = session.task
        self.assertGreater(slow.maxsize, 0, "Subscribers must have bounded queues")
        responsive = asyncio.Queue(maxsize=1)
        session.subscribers.add(responsive)
        # Fill only the slow subscriber through real event dispatch. Draining
        # the responsive subscriber is a barrier, not a timing-dependent sleep.
        for index in range(slow.maxsize):
            envelope = self.ws.emit(
                "message.delta", session.session_id, session.stored_session_id,
                session.profile, {"text": str(index)},
            )
            self.assertEqual(await self.next_event(responsive, "message.delta"), envelope)
        self.assertTrue(slow.full())
        self.assertIn(slow, session.subscribers)
        overflow = self.ws.emit(
            "message.delta", session.session_id, session.stored_session_id,
            session.profile, {"text": "overflow"},
        )
        self.assertEqual(await self.next_event(responsive, "message.delta"), overflow)
        error = await self.next_event(slow, "chat.error")
        self.assert_identity(error, session)
        self.assertTrue(error["payload"].get("message"))
        self.assertEqual(error["payload"]["status"], "unknown")
        self.assertTrue(slow.empty(), "Overflow must discard buffered events")
        self.assertNotIn(slow, session.subscribers)
        self.assertIn(responsive, session.subscribers)
        self.assertIs(session.task, task)
        self.assertFalse(task.done(), "Slow subscribers must not stop generation")
        self.assertEqual(session.status, "running")
        following = self.ws.emit(
            "message.delta", session.session_id, session.stored_session_id,
            session.profile, {"text": "still generating"},
        )
        self.assertEqual(await self.next_event(responsive, "message.delta"), following)
        await self.complete(session, responsive)
        self.assertEqual(session.status, "complete")
        self.assertTrue(slow.empty(), "Detached subscribers must receive no later events")
        self.assertTrue(responsive.empty())
        self.assertEqual(len(self.ws.calls_for("prompt.submit")), 1)

    async def test_same_stored_id_in_different_profiles_does_not_reuse_busy_runtime(self):
        self.ws.created = rpc_session("runtime-alpha", "same-stored", "alpha")
        first, qa = await self.start(profile="alpha")
        await self.submitted(first)
        self.ws.active = [active_session("runtime-alpha", "same-stored", status="working")]
        self.ws.resumed = rpc_session("runtime-beta", "same-stored", "beta")
        second, qb = await self.start("beta turn", "same-stored", "beta")
        self.assertEqual(second.session_id, "runtime-beta")
        self.assertEqual(second.profile, "beta")
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(self.ws.calls_for("session.resume")[0]["params"], {
            "session_id": "same-stored", "profile": "beta", "omit_messages": True,
        })
        await self.submitted(second, "beta turn", count=2)
        await self.complete(second, qb)
        self.assertFalse(first.task.done())
        self.assertTrue(qa.empty())
        await self.complete(first, qa)
        self.assertEqual(len(self.connector.calls), 1)

    async def test_unknown_state_is_unknown_without_resuming_or_submitting(self):
        for stored, profile in (("not-mapped", None), ("not-mapped", "beta"), ("", None)):
            with self.subTest(stored=stored, profile=profile):
                state = await asyncio.wait_for(self.manager.state(stored, profile), TIMEOUT)
                self.assertIsInstance(state, dict)
                self.assertEqual(state["status"], "unknown")
        self.assertEqual(self.ws.calls_for("session.create"), [])
        self.assertEqual(self.ws.calls_for("session.resume"), [])
        self.assertEqual(self.ws.calls_for("prompt.submit"), [])

    async def test_state_reads_active_list_working_waiting_and_idle(self):
        session = await self.idle_session()
        for status, expected in (
            ("working", "running"), ("waiting", "running"), ("idle", "complete"),
        ):
            with self.subTest(status=status):
                self.ws.active = [
                    active_session("other-runtime", "other-stored", status="working"),
                    active_session(session.session_id, session.stored_session_id,
                                   status=status),
                ]
                count = len(self.ws.calls_for("session.active_list"))
                state = await asyncio.wait_for(self.manager.state(
                    session.stored_session_id, session.profile,
                ), TIMEOUT)
                self.assertEqual(state["status"], expected)
                self.assert_identity(state, session)
                self.assertGreater(len(self.ws.calls_for("session.active_list")), count)
        self.assertEqual(len(self.ws.calls_for("prompt.submit")), 1)

    async def test_state_mapping_is_profile_scoped(self):
        session = await self.idle_session()
        state = await asyncio.wait_for(self.manager.state(session.stored_session_id, "beta"), TIMEOUT)
        self.assertEqual(state["status"], "unknown")
        self.assertEqual(self.ws.calls_for("session.resume"), [])

    async def test_state_rpc_error_is_chat_error_502(self):
        session = await self.idle_session()
        self.ws.plan_error("session.active_list", "state unavailable")
        await self.assert_chat_error(self.manager.state(
            session.stored_session_id, session.profile,
        ), message="state unavailable")

    async def test_active_list_compaction_updates_state_and_lookup_for_next_turn(self):
        session = await self.idle_session()
        original_runtime = session.session_id
        old_stored = session.stored_session_id
        self.ws.active = [active_session(original_runtime, "stored-after-compaction")]
        state = await asyncio.wait_for(self.manager.state(old_stored, session.profile), TIMEOUT)
        self.assertEqual(state["stored_session_id"], "stored-after-compaction")
        self.assertEqual(session.stored_session_id, "stored-after-compaction")
        second, queue = await self.start("after compaction", "stored-after-compaction", session.profile)
        self.assertEqual(second.session_id, original_runtime)
        self.assertEqual(second.stored_session_id, "stored-after-compaction")
        await self.submitted(second, "after compaction", count=2)
        self.assertEqual(len(self.ws.calls_for("session.create")), 1)
        self.assertEqual(self.ws.calls_for("session.resume"), [])
        self.assertEqual(len(self.connector.calls), 1)
        await self.complete(second, queue)

    async def test_second_turn_refreshes_compacted_session_key_before_ready(self):
        session = await self.idle_session()
        old_stored = session.stored_session_id
        self.ws.active[0]["session_key"] = "compacted-during-idle"
        second, queue = await self.start("next", old_stored, session.profile)
        self.assertEqual(second.stored_session_id, "compacted-during-idle")
        await self.submitted(second, "next", count=2)
        self.assertEqual(self.ws.calls_for("session.resume"), [])
        await self.complete(second, queue)

    async def test_unicode_special_characters_and_large_prompt_are_not_modified(self):
        text = "中文 café e\u0301 ' OR 1=1; -- <tag> \\ \n\t" + "x" * 10001
        session, queue = await self.start(text)
        await self.submitted(session, text)
        await self.complete(session, queue)

    async def test_ten_thousand_events_keep_order_and_terminal_payload(self):
        session, queue = await self.start()
        await self.submitted(session)

        async def stream_and_consume():
            # A responsive browser drains each batch. Do not demand an
            # unbounded subscriber queue or starve it with 10k synchronous puts.
            for begin in range(0, 10001, 100):
                end = min(begin + 100, 10001)
                for index in range(begin, end):
                    self.ws.emit("message.delta", session.session_id, session.stored_session_id,
                                 session.profile, {"text": str(index), "index": index})
                for index in range(begin, end):
                    name, envelope = await queue.get()
                    self.assertEqual(name, "message.delta")
                    self.assert_identity(envelope, session)
                    self.assertEqual(envelope["payload"], {"text": str(index), "index": index})
            terminal = self.ws.emit("message.complete", session.session_id, session.stored_session_id,
                                    session.profile, {"status": "complete", "text": "all done"})
            name, envelope = await queue.get()
            self.assertEqual(name, "message.complete")
            self.assertEqual(envelope, terminal)

        await asyncio.wait_for(stream_and_consume(), 10.0)
        await asyncio.wait_for(asyncio.shield(session.task), TIMEOUT)
        self.assertTrue(queue.empty())

    async def test_close_releases_ws_and_finishes_pending_turn(self):
        self.ws.hold.add("prompt.submit")
        session, _ = await self.start()
        await self.submitted(session)
        await asyncio.wait_for(self.manager.close(), TIMEOUT)
        self.assertTrue(self.ws.closed)
        self.assertGreaterEqual(self.ws.close_calls, 1)
        self.assertTrue(session.task.done())
        if not session.task.cancelled():
            error = session.task.exception()
            if error is not None:
                self.assertIsInstance(error, self.chat.ChatError)
                self.assertEqual(error.status_code, 502)
        self.assertTrue(all(task.done() for task in self.ws.reader_tasks))
        await asyncio.wait_for(self.manager.close(), TIMEOUT)

    async def test_close_before_connect_does_not_acquire_ticket(self):
        await asyncio.wait_for(self.manager.close(), TIMEOUT)
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.connector.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
