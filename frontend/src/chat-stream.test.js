import test from "node:test";
import assert from "node:assert/strict";
import { applyTurnEvent, createSSEParser, isTerminalEvent, mergeHistory, messagePage, readChatStream, reconcileTurns } from "./chat-stream.js";

const encoder = new TextEncoder();
const envelope = (type, payload, extra = {}) => ({ type, session_id: "live/id", stored_session_id: "stored/id", profile: "配置 A", payload, ...extra });
const event = (type, payload, ending = "\n\n") => `event: ${type}\ndata: ${JSON.stringify(envelope(type, payload))}${ending}`;
function body(chunks) {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      controller.close();
    },
  });
}

const historyMessage = (id, role = "user", text = id) => ({ id, role, text, content: text });

test("SSE parses every possible CRLF chunk boundary and multiline data", () => {
  const source = ': keepalive\r\nevent: message.delta\r\ndata: {\r\ndata: "payload":{"text":"你好"}}\r\n\r\n';
  for (let split = 0; split <= source.length; split += 1) {
    const events = [];
    const parser = createSSEParser((item) => events.push(item));
    parser.feed(source.slice(0, split));
    parser.feed(source.slice(split));
    parser.end();
    assert.deepEqual(events, [{ event: "message.delta", data: '{\n"payload":{"text":"你好"}}' }]);
  }
});

test("SSE supports bare CR, comments, default event, one-space stripping, and final data", () => {
  const events = [];
  const parser = createSSEParser((item) => events.push(item));
  for (const character of ': comment\r\revent: first\rdata:  keep spaces \r\rdata:last') parser.feed(character);
  parser.end();
  parser.end();
  assert.deepEqual(events, [{ event: "first", data: " keep spaces " }, { event: "message", data: "last" }]);
});

test("UTF-8 byte chunks preserve session.ready envelope and final event without blank line", async () => {
  const ready = envelope("session.ready", { session_id: "live/id", stored_session_id: "stored/id", profile: "配置 A" });
  const source = `event: session.ready\r\ndata: ${JSON.stringify(ready)}\r\n\r\n`
    + event("message.delta", { text: "中文回复" })
    + event("message.complete", { status: "complete" }, "");
  const bytes = encoder.encode(source);
  const events = [];
  const result = await readChatStream(body(Array.from(bytes, (byte) => Uint8Array.of(byte))), (type, data) => events.push({ type, data }));
  assert.deepEqual(events[0], { type: "session.ready", data: ready });
  assert.equal(events[1].data.payload.text, "中文回复");
  assert.equal(result.event, "message.complete");
  assert.equal(result.envelope.payload.text, undefined);
});

test("type fallback works and a non-terminal error does not end the stream", async () => {
  const events = [];
  const source = event("error", { message: "暂时警告" })
    + `data: ${JSON.stringify(envelope("message.delta", { text: "继续" }))}\n\n`
    + event("message.complete", { status: "complete", text: "继续" });
  await readChatStream(body([source]), (type) => events.push(type));
  assert.deepEqual(events, ["error", "message.delta", "message.complete"]);
});

test("all contract terminal statuses terminate; ordinary errors never do", async () => {
  for (const status of ["complete", "error", "interrupted"]) {
    const result = await readChatStream(body([event("message.complete", { status })]), () => {});
    assert.equal(result.envelope.payload.status, status);
  }
  for (const status of ["unknown", "error"]) {
    const result = await readChatStream(body([event("chat.error", { status, message: "提交异常" })]), () => {});
    assert.equal(result.event, "chat.error");
  }
  assert.equal(isTerminalEvent("error", { payload: { status: "error" } }), false);
  assert.equal(isTerminalEvent("message.complete", { payload: {} }), false);
  assert.equal(isTerminalEvent("chat.error", { payload: { status: "complete" } }), false);
});

test("EOF without terminal rejects while delivering all partial delta content", async () => {
  let partial = "";
  await assert.rejects(readChatStream(body([
    event("message.delta", { text: "保留" }), event("message.delta", { text: "尾段" }, ""),
  ]), (type, data) => { if (type === "message.delta") partial += data.payload.text; }), /状态未知/);
  assert.equal(partial, "保留尾段");
});

test("decoder is flushed at EOF, including an incomplete UTF-8 sequence in a comment", async () => {
  let partial = "";
  await assert.rejects(readChatStream(body([
    event("message.delta", { text: "已收到" }), ": unfinished ", Uint8Array.of(0xe4, 0xb8),
  ]), (type, data) => { if (type === "message.delta") partial += data.payload.text; }), /状态未知/);
  assert.equal(partial, "已收到");
});

test("terminal does not wait for EOF and ignores subsequent events", async () => {
  let cancelled = false;
  const events = [];
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(event("message.complete", { status: "complete" }) + event("message.delta", { text: "不应追加" })));
    },
    cancel() { cancelled = true; },
  });
  await readChatStream(stream, (type) => events.push(type));
  assert.equal(cancelled, true);
  assert.deepEqual(events, ["message.complete"]);
});

test("malformed JSON and reader errors reject rather than report success", async () => {
  await assert.rejects(readChatStream(body(["event: message.delta\ndata: {bad}\n\n"]), () => {}), /格式异常/);
  await assert.rejects(readChatStream(null, () => {}), /状态未知/);
  let partial = "";
  let reads = 0;
  const stream = new ReadableStream({
    pull(controller) {
      if (reads++ === 0) controller.enqueue(encoder.encode(event("message.delta", { text: "部分结果" })));
      else controller.error(new Error("connection lost"));
    },
  });
  await assert.rejects(readChatStream(stream, (type, data) => { partial += data.payload.text; }), /connection lost/);
  assert.equal(partial, "部分结果");
});

test("history filtering does not change raw offset or chronological order", () => {
  const result = messagePage({
    messages: [
      { id: "1", role: "system", content: "system" },
      { id: "2", role: "user", content: "原文", display_content: "展示文本" },
      { id: "3", role: "tool", content: "internal" },
      { id: "4", role: "assistant", content: "secret", display_kind: "hidden" },
      { id: "5", role: "assistant", content: "答复" },
    ],
    pagination: { returned: 5, limit: 5, offset: 50, order: "latest" },
  });
  assert.equal(result.offset, 55);
  assert.equal(result.hasMore, true);
  assert.deepEqual(result.messages.map((message) => message.id), ["2", "5"]);
  assert.equal(result.messages[0].text, "展示文本");
  assert.equal(result.messages[0].content, "原文");
});

test("fully hidden pages still advance; malformed history is not a successful empty page", () => {
  const result = messagePage({ messages: [{ id: 1, role: "tool" }], pagination: { returned: 1, limit: 1, offset: 0, order: "latest" } });
  assert.deepEqual(result.messages, []);
  assert.equal(result.offset, 1);
  assert.equal(result.hasMore, true);
  assert.throws(() => messagePage({}), /格式不正确/);
  assert.throws(() => messagePage({ messages: [], pagination: { returned: 0, limit: 50, offset: 0, order: "oldest" } }), /格式不正确/);
});

test("older pages prepend with ID deduplication; refreshed latest pages preserve loaded history", () => {
  const existing = [historyMessage("3"), historyMessage("4")];
  const older = [historyMessage("1"), historyMessage("2"), historyMessage("3")];
  const merged = mergeHistory(existing, older, true);
  assert.deepEqual(merged.map((message) => message.id), ["1", "2", "3", "4"]);
  const fresh = mergeHistory(merged, [historyMessage("4", "user", "updated"), historyMessage("5")]);
  assert.deepEqual(fresh.map((message) => message.id), ["1", "2", "3", "4", "5"]);
  assert.equal(fresh[3].text, "updated");
  assert.deepEqual(mergeHistory(fresh, []).map((message) => message.id), ["1", "2", "3", "4", "5"]);
});

test("reconcile only new ordered turns, never old identical prompts or rejected submissions", () => {
  const turns = [
    { id: "a", text: "重复", answer: "回答", delivery: "sent", status: "complete", baseIds: ["old", "old-reply"] },
    { id: "rejected", text: "重复", answer: "", delivery: "unsent", status: "error", baseIds: [] },
    { id: "b", text: "重复", answer: "部分", delivery: "unknown", status: "unknown", baseIds: ["old", "old-reply"] },
  ];
  const history = [
    historyMessage("old", "user", "重复"), historyMessage("old-reply", "assistant", "回答"),
    historyMessage("new-a", "user", "重复"), historyMessage("new-a-reply", "assistant", "回答"),
    historyMessage("new-b", "user", "重复"), historyMessage("new-b-reply", "assistant", "部分完整回答"),
  ];
  const result = reconcileTurns(turns, history);
  assert.equal(result[0].remoteUserId, "new-a");
  assert.equal(result[0].hideAnswer, true);
  assert.equal(result[1], turns[1]);
  assert.equal(result[2].remoteUserId, "new-b");
  assert.equal(result[2].hideAnswer, true);
  assert.deepEqual(reconcileTurns(result, history), result);
});

test("refreshing beyond the cached page resets the contiguous history window", () => {
  const page = (from, to) => Array.from({ length: to - from + 1 }, (_, i) => historyMessage(String(from + i)));
  const refreshed = mergeHistory(page(1, 50), page(101, 150));
  assert.deepEqual(refreshed, page(101, 150));
  assert.deepEqual(mergeHistory(refreshed, page(51, 100), true), page(51, 150));
});

test("older unseen identical prompts cannot consume an unknown local turn", () => {
  const turn = { text: "重复", answer: "旧答复", delivery: "unknown", status: "unknown", baseIds: ["recent"] };
  const history = [historyMessage("old", "user", "重复"), historyMessage("reply", "assistant", "旧答复"), historyMessage("recent")];
  assert.equal(reconcileTurns([turn], history)[0], turn);
  assert.equal(reconcileTurns([turn], history.slice(0, 2))[0], turn);
});

test("persisted reply without delta trailing whitespace hides only the matching local answer", () => {
  const turn = { text: "问题", answer: "答案 \n", delivery: "sent", status: "complete", baseIds: [] };
  const history = [historyMessage("user", "user", "问题"), historyMessage("reply", "assistant", "答案")];
  assert.equal(reconcileTurns([turn], history)[0].hideAnswer, true);
  assert.equal(reconcileTurns([{ ...turn, answer: "不同答案" }], history)[0].hideAnswer, false);
});

test("history with only the submitted user does not discard a local partial answer", () => {
  const turn = { text: "问题", answer: "部分回复", status: "unknown", delivery: "unknown", baseIds: [], hideAnswer: false };
  const result = reconcileTurns([turn], [historyMessage("new", "user", "问题")])[0];
  assert.equal(result.hideUser, true);
  assert.equal(result.hideAnswer, false);
  assert.equal(result.answer, "部分回复");
});

test("message.complete without text uses collected deltas; final text replaces instead of appending", async () => {
  for (const finalText of [undefined, "你好", "修正后的回复", ""]) {
    let turn = { answer: "", tools: [], status: "pending", delivery: "pending" };
    const source = event("message.delta", { text: "你" }) + event("message.delta", { text: "好" })
      + event("message.complete", { status: "complete", ...(finalText === undefined ? {} : { text: finalText }) });
    await readChatStream(body([source]), (type, data) => { turn = applyTurnEvent(turn, type, data.payload); });
    assert.equal(turn.answer, finalText ?? "你好");
    assert.equal(turn.status, "complete");
    assert.equal(turn.delivery, "sent");
  }
});

test("tools with the same name are associated only by tool_id", () => {
  let turn = { answer: "", tools: [] };
  turn = applyTurnEvent(turn, "tool.start", { tool_id: "a", name: "search" });
  turn = applyTurnEvent(turn, "tool.start", { tool_id: "b", name: "search" });
  turn = applyTurnEvent(turn, "tool.complete", { tool_id: "a" });
  assert.deepEqual(turn.tools.map((tool) => [tool.id, tool.state]), [["a", "complete"], ["b", "running"]]);
  assert.equal(applyTurnEvent(turn, "tool.complete", { name: "search" }), turn);
  turn = applyTurnEvent(turn, "message.complete", { status: "interrupted" });
  assert.equal(turn.tools[1].state, "ended");
  assert.equal(turn.delivery, "unknown");
});

test("failed and interrupted completion preserve partial content and never claim successful delivery", () => {
  for (const status of ["error", "interrupted"]) {
    const turn = applyTurnEvent({ answer: "保留部分", tools: [] }, "message.complete", { status });
    assert.equal(turn.answer, "保留部分");
    assert.equal(turn.status, status);
    assert.equal(turn.delivery, "unknown");
  }
});
