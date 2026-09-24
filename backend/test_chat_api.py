import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

import main
from chat import ChatError, LiveSession


class ChatApiTests(unittest.TestCase):
    def setUp(self):
        self.manager = SimpleNamespace(start_turn=AsyncMock(), state=AsyncMock(), close=AsyncMock())
        self.manager_patch = patch.object(main, "ChatManager", return_value=self.manager)
        self.manager_patch.start()
        self.addCleanup(self.manager_patch.stop)
        self.auth_patch = patch("config.APP_AUTH", False)
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.upstream = patch.object(main.hc, "request")
        self.request = self.upstream.start()
        self.addCleanup(self.upstream.stop)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def response(self, payload, status=200):
        self.request.return_value = Mock(ok=status < 400, status_code=status, json=Mock(return_value=payload))

    def test_history_list_uses_explicit_paging_order_and_profile(self):
        payload = {"sessions": [{"id": "stored-1", "profile": "work"}], "total": 1, "limit": 20, "offset": 0}
        self.response(payload)
        response = self.client.get("/api/sessions?profile=work&limit=20&offset=0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        self.request.assert_called_once_with("GET", "/api/sessions", params={
            "limit": 20, "offset": 0, "profile": "work", "order": "recent", "archived": "exclude", "min_messages": 1})

    def test_message_page_preserves_resolved_id_and_order(self):
        payload = {"session_id": "tip-2", "profile": "work", "messages": [{"id": 42, "content": "hello"}],
                   "pagination": {"limit": 50, "offset": 50, "returned": 1, "order": "latest"}}
        self.response(payload)
        response = self.client.get("/api/sessions/root-1/messages?profile=work&offset=50")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        self.request.assert_called_once_with("GET", "/api/sessions/root-1/messages", params={
            "limit": 50, "offset": 50, "profile": "work", "order": "latest", "include_compacted": True})

    def test_invalid_pagination_and_path_do_not_reach_upstream(self):
        for path in ("/api/sessions?limit=101", "/api/sessions?offset=-1",
                     "/api/sessions/id/messages?limit=501", "/api/sessions/id/messages?offset=-2",
                     "/api/sessions/%2E%2E/messages", "/api/sessions/a%3Fb/messages"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 422)
        self.request.assert_not_called()

    def test_history_errors_preserve_missing_and_busy_without_raw_body(self):
        for status in (404, 503, 500):
            with self.subTest(status=status):
                self.response({}, status)
                response = self.client.get("/api/sessions/id/messages")
                self.assertEqual(response.status_code, status if status in (404, 503) else 502)
                self.assertIn("detail", response.json())

    def test_chat_rejects_empty_text_before_upstream(self):
        for text in ("", "   ", "\n\t"):
            self.assertEqual(self.client.post("/api/chat", json={"text": text}).status_code, 422)
        self.manager.start_turn.assert_not_called()

    def test_chat_busy_is_not_a_successful_stream(self):
        self.manager.start_turn.side_effect = ChatError("busy", 409)
        response = self.client.post("/api/chat", json={"text": "hello", "stored_session_id": "stored-1", "profile": "work"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"detail": "busy"})

    def test_sse_preserves_ids_sequence_and_error_terminal(self):
        session = LiveSession("runtime-1", "stored-1", "work")
        queue = asyncio.Queue()
        session.subscribers.add(queue)
        queue.put_nowait(("session.ready", session.envelope("session.ready", session.identity())))
        queue.put_nowait(("message.complete", {**session.envelope("message.complete", {"text": "partial", "status": "error"}), "seq": 7}))
        self.manager.start_turn.return_value = session, queue
        response = self.client.post("/api/chat", json={"text": "hello"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        for value in ('"seq": 7', '"stored_session_id": "stored-1"', '"status": "error"', "event: session.ready"):
            self.assertIn(value, response.text)
        self.assertNotIn(queue, session.subscribers)

    def test_state_is_read_only_and_profile_scoped(self):
        self.manager.state.return_value = {"stored_session_id": "s", "profile": "work", "status": "unknown"}
        response = self.client.get("/api/sessions/s/state?profile=work")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unknown")
        self.manager.state.assert_awaited_once_with("s", "work")
        self.manager.start_turn.assert_not_called()
        self.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
