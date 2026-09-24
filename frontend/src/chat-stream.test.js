import test from "node:test";
import assert from "node:assert/strict";
import * as chatStream from "./chat-stream.js";

// A namespace import keeps the existing SSE regressions runnable before the new export exists.
const { applyTurnEvent, createSSEParser, historyForDisplay, isTerminalEvent, mergeHistory, messagePage, readChatStream, reconcileTurns } = chatStream;

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

const historyMessage = (id, role = "user", text = id, extra = {}) => ({
  id, role, text, notices: [], hidden: false, tools: [], toolCallId: null, toolName: "", ...extra,
});
const parseHistory = (messages) => messagePage({
  messages, pagination: { returned: messages.length, limit: Math.max(1, messages.length), offset: 0, order: "latest" },
}).messages;
const finalItem = (text = "恢复答案", extra = {}) => ({
  type: "message", role: "assistant", phase: "final", content: [{ type: "output_text", text }], ...extra,
});
const call = (id, name = "search") => ({ id, type: "function", function: { name, arguments: "PRIVATE_ARGS" } });
const displayTools = (messages) => messages.flatMap((message) => message.tools);
function freezeHistory(messages) {
  for (const message of messages) {
    for (const tool of message.tools) Object.freeze(tool);
    Object.freeze(message.tools);
    Object.freeze(message.notices);
    Object.freeze(message);
  }
  return Object.freeze(messages);
}

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

test("history preserves raw row IDs and order while display filtering leaves pagination unchanged", () => {
  const result = messagePage({
    messages: [
      { id: "1", role: "system", content: "system" },
      { id: "2", role: "user", content: "原文", display_content: "展示文本" },
      { id: "3", role: "tool", content: "internal", tool_call_id: "orphan", name: "search" },
      { id: "4", role: "assistant", content: "secret", display_kind: "hidden" },
      { id: "5", role: "assistant", content: "答复" },
      { id: "6", role: "assistant", content: "" },
    ],
    pagination: { returned: 6, limit: 6, offset: 50, order: "latest" },
  });
  assert.equal(result.offset, 56);
  assert.equal(result.hasMore, true);
  assert.deepEqual(result.messages.map((message) => message.id), ["1", "2", "3", "4", "5", "6"]);
  assert.deepEqual(result.messages[1], historyMessage("2", "user", "展示文本"));
  assert.deepEqual(result.messages[5], historyMessage("6", "assistant", ""));
  for (const message of result.messages) {
    assert.deepEqual(Object.keys(message).sort(), Object.keys(historyMessage("shape")).sort());
    assert.equal(typeof message.hidden, "boolean");
  }
  assert.deepEqual(historyForDisplay(result.messages).map((message) => message.id), ["2", "3", "5"]);
});

test("fully hidden pages retain IDs and advance; malformed history is not a successful empty page", () => {
  const result = messagePage({ messages: [{ id: 1, role: "tool", hidden: true }], pagination: { returned: 1, limit: 1, offset: 0, order: "latest" } });
  assert.deepEqual(result.messages.map((message) => message.id), ["1"]);
  assert.equal(result.messages[0].hidden, true);
  assert.equal(result.offset, 1);
  assert.equal(result.hasMore, true);
  assert.deepEqual(historyForDisplay(result.messages), []);
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

test("historyForDisplay is exported independently of stream parsing", () => {
  assert.equal(typeof historyForDisplay, "function", "export historyForDisplay for canonical history rows");
});

test("plain strings, including JSON-looking text and special characters, are not reparsed", () => {
  for (const content of [' {"text":"不是投影"} \n', '[{"type":"text","text":"原文"}]', "汉字 café ' \" ; --\n保留空格  "]) {
    assert.deepEqual(parseHistory([{ id: 0, role: "assistant", content }]), [historyMessage("0", "assistant", content)]);
  }
});

for (const field of ["text", "output_text", "content", "message"]) {
  test(`canonical body recursively extracts the ${field} field`, () => {
    const [row] = parseHistory([{ id: field, role: "assistant", content: { [field]: { content: [{ text: "规范正文" }] } } }]);
    assert.equal(row.text, "规范正文");
    assert.deepEqual(row.notices, []);
    assert.equal(Object.hasOwn(row, "content"), false);
  });
}

test("nested arrays keep confirmed text in order without object coercion", () => {
  const [row] = parseHistory([{ id: "nested", role: "assistant", content: [
    "甲", [{ type: "text", text: "乙" }, { type: "output_text", text: "丙" }],
    { message: { content: [{ output_text: "丁" }, "戊"] } },
  ] }]);
  assert.equal(row.text.replace(/\s/g, ""), "甲乙丙丁戊");
  assert.deepEqual(row.notices, []);
  assert.doesNotMatch(JSON.stringify(row), /\[object Object\]/);
});

for (const [type, payload, notice] of [
  ["image_url", { image_url: { url: "https://media.invalid/PRIVATE_IMAGE" } }, "图片内容（暂不预览）"],
  ["image", { source: { type: "base64", data: "PRIVATE_IMAGE_BASE64" } }, "图片内容（暂不预览）"],
  ["input_audio", { input_audio: { data: "PRIVATE_AUDIO_BASE64", format: "wav" } }, "音频内容（暂不播放）"],
  ["audio", { url: "https://media.invalid/PRIVATE_AUDIO" }, "音频内容（暂不播放）"],
  ["file", { file_data: "data:application/pdf;base64,PRIVATE_FILE", filename: "PRIVATE_FILENAME" }, "文件内容（暂不预览）"],
]) {
  test(`${type} becomes a notice, preserves adjacent text, and never retains media payloads`, () => {
    const rows = parseHistory([{ id: type, role: "assistant", content: ["前", { type, ...payload }, "后"] }]);
    assert.equal(rows[0].text.replace(/\s/g, ""), "前后");
    assert.deepEqual(rows[0].notices, [notice]);
    assert.doesNotMatch(JSON.stringify(rows), /PRIVATE_|https:|data:|base64|\[object Object\]/);
    const visible = historyForDisplay(rows);
    assert.equal(visible.length, 1);
    assert.deepEqual(visible[0].notices, [notice]);
    assert.doesNotMatch(JSON.stringify(visible), /PRIVATE_|https:|data:|base64/);
  });
}

test("unknown objects and invalid body scalars produce a safe notice rather than a dump", () => {
  for (const content of [{}, { unknown: { secret: "PRIVATE_UNKNOWN" } }, 42, true, { text: 42 }]) {
    const rows = parseHistory([{ id: "unknown", role: "assistant", content }]);
    assert.equal(rows[0].text, "");
    assert.deepEqual(rows[0].notices, ["暂不支持的内容"]);
    assert.doesNotMatch(JSON.stringify(rows), /PRIVATE_UNKNOWN|\[object Object\]/);
    assert.equal(historyForDisplay(rows).length, 1, "a notice-only row remains visible");
  }
});

test("null, absent, empty string, and empty array bodies retain IDs without blank bubbles", () => {
  const rows = parseHistory([null, undefined, "", []].map((content, index) => ({ id: index, role: "assistant", content })));
  assert.deepEqual(rows, [0, 1, 2, 3].map((id) => historyMessage(String(id), "assistant", "")));
  assert.deepEqual(historyForDisplay(rows), []);
  assert.deepEqual(historyForDisplay([]), []);
});

for (const type of ["thinking", "reasoning", "redacted_thinking"]) {
  test(`${type} is never body text and only yields a thinking-only notice without an answer or tools`, () => {
    const thought = { type, text: "PRIVATE_THOUGHT", thinking: "PRIVATE_THOUGHT", data: "PRIVATE_THOUGHT" };
    const rows = parseHistory([
      { id: "thought", role: "assistant", content: [thought] },
      { id: "answer", role: "assistant", content: [thought, { type: "text", text: "正式回答" }] },
      { id: "tool", role: "assistant", content: [thought], tool_calls: [call("thought-call")] },
    ]);
    assert.equal(rows[0].text, "");
    assert.deepEqual(rows[0].notices, ["仅含思考记录"]);
    assert.equal(rows[1].text, "正式回答");
    assert.deepEqual(rows[1].notices, []);
    assert.equal(rows[2].text, "");
    assert.deepEqual(rows[2].notices, []);
    assert.equal(rows[2].tools.length, 1);
    assert.doesNotMatch(JSON.stringify(rows), /PRIVATE_THOUGHT|PRIVATE_ARGS/);
  });
}

test("an explicit display projection wins even when null or empty and never revives raw or sidecar text", () => {
  for (const display_content of [null, "", [], "公开投影", { text: "公开投影" }]) {
    const rows = parseHistory([{
      id: "projection", role: "assistant", display_content, content: "PRIVATE_RAW",
      codex_message_items: [finalItem("PRIVATE_SIDECAR")],
    }]);
    assert.equal(rows[0].text, display_content === "公开投影" || display_content?.text ? "公开投影" : "");
    assert.deepEqual(rows[0].notices, []);
    assert.doesNotMatch(JSON.stringify(rows), /PRIVATE_RAW|PRIVATE_SIDECAR/);
    assert.equal(historyForDisplay(rows).length, rows[0].text ? 1 : 0);
  }
});

test("malformed sidecars are ignored when a projection, existing body, or hidden flag takes priority", () => {
  for (const [fields, expectedText] of [
    [{ display_content: null, content: "PRIVATE_RAW" }, ""],
    [{ display_content: "公开投影", content: "PRIVATE_RAW" }, "公开投影"],
    [{ content: "已有正文" }, "已有正文"],
    [{ hidden: true, content: "PRIVATE_HIDDEN" }, ""],
  ]) {
    const [row] = parseHistory([{ id: "priority", role: "assistant", codex_message_items: "{PRIVATE_BAD_SIDECAR", ...fields }]);
    assert.equal(row.text, expectedText);
    assert.deepEqual(row.notices, []);
    assert.doesNotMatch(JSON.stringify(row), /PRIVATE_/);
  }
});

test("hidden flags, hidden display kinds, and system rows cannot be revived by a projection or sidecar", () => {
  for (const flags of [{ hidden: true }, { display_kind: "hidden" }, { display_kind: "system" }, { display_kind: "tool" }, { role: "system" }]) {
    const rows = parseHistory([{
      id: "hidden", role: "assistant", content: "PRIVATE_RAW_HIDDEN", display_content: "PRIVATE_PROJECTION",
      codex_message_items: [finalItem("PRIVATE_SIDECAR")], tool_calls: [call("hidden-call", "PRIVATE_TOOL")], ...flags,
    }]);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].id, "hidden");
    assert.equal(rows[0].hidden, true);
    assert.doesNotMatch(JSON.stringify(rows), /PRIVATE_/);
    assert.deepEqual(historyForDisplay(rows), []);
  }
});

for (const encoding of ["array", "json"]) {
  test(`codex ${encoding} sidecars recover only final assistant message text from a truly empty body`, () => {
    const items = [
      finalItem("PRIVATE_ANALYSIS", { phase: "analysis" }), finalItem("PRIVATE_COMMENTARY", { phase: "commentary" }),
      finalItem("PRIVATE_USER", { role: "user" }), finalItem("PRIVATE_TOOL_RESULT", { type: "function_call_output" }),
      finalItem("unused", { content: [{ type: "text", text: "正式" }, { type: "output_text", text: "答案" }] }),
      finalItem("无阶段", { phase: undefined }),
    ];
    for (const content of [undefined, null, "", []]) {
      const rows = parseHistory([{
        id: "sidecar", role: "assistant", content, codex_message_items: encoding === "json" ? JSON.stringify(items) : items,
      }]);
      assert.equal(rows[0].text.replace(/\s/g, ""), "正式答案无阶段");
      assert.deepEqual(rows[0].notices, []);
      assert.doesNotMatch(JSON.stringify(rows), /PRIVATE_|codex_message_items/);
    }
  });
}

test("a body with text, media, an unknown structure, or reasoning blocks sidecar recovery", () => {
  for (const content of ["已有正文", [{ type: "image_url", image_url: { url: "https://media.invalid/PRIVATE_IMAGE" } }], { unknown: true }, [{ type: "reasoning", text: "PRIVATE_REASONING" }]]) {
    const [row] = parseHistory([{ id: "body-priority", role: "assistant", content, codex_message_items: [finalItem("PRIVATE_RECOVERED")] }]);
    assert.doesNotMatch(JSON.stringify(row), /PRIVATE_/);
    if (typeof content === "string") assert.equal(row.text, content);
    else {
      assert.equal(row.text, "");
      assert.equal(row.notices.length, 1, "non-text content must not silently disappear");
    }
  }
});

test("reasoning, analysis and commentary sidecars yield a notice, never an answer", () => {
  for (const items of [
    [finalItem("PRIVATE_ANALYSIS", { phase: "analysis" }), finalItem("PRIVATE_COMMENTARY", { phase: "commentary" })],
    [{ type: "reasoning", summary: [{ type: "summary_text", text: "PRIVATE_REASONING" }] }],
  ]) {
    for (const codex_message_items of [items, JSON.stringify(items)]) {
      const rows = parseHistory([{ id: "analysis", role: "assistant", content: "", codex_message_items }]);
      assert.equal(rows[0].text, "");
      assert.deepEqual(rows[0].notices, ["仅含思考记录"]);
      assert.equal(historyForDisplay(rows).length, 1);
      assert.doesNotMatch(JSON.stringify(rows), /PRIVATE_/);
      assert.doesNotMatch(JSON.stringify(historyForDisplay(rows)), /PRIVATE_/);
    }
  }
});

test("malformed dedicated sidecars yield unsupported notices without breaking neighboring rows", () => {
  for (const codex_message_items of ["{bad json", "{}", '"PRIVATE_SCALAR"', 42, { items: [] }, [finalItem(42)]]) {
    const rows = parseHistory([
      { id: "bad", role: "assistant", content: "", codex_message_items },
      { id: "good", role: "assistant", content: "下一条" },
    ]);
    assert.equal(rows[0].text, "");
    assert.deepEqual(rows[0].notices, ["暂不支持的内容"]);
    assert.equal(rows[1].text, "下一条");
    assert.doesNotMatch(JSON.stringify(rows), /PRIVATE_SCALAR|bad json/);
  }
});

test("tool calls come only from assistant metadata and tool results retain identifiers, never raw bodies", () => {
  const rows = parseHistory([
    { id: "assistant", role: "assistant", content: "", tool_calls: [call(7, "nested-name"), { id: "flat", name: "flat-name", arguments: "PRIVATE_FLAT_ARGS" }] },
    { id: "user", role: "user", content: "问题", tool_calls: [call("ignored", "PRIVATE_USER_TOOL")] },
    { id: "embedded", role: "assistant", content: [{ type: "tool_use", id: "not-a-call", name: "PRIVATE_EMBEDDED_TOOL", input: { value: "PRIVATE_INPUT" } }] },
    { id: "result", role: "tool", name: "nested-name", tool_call_id: 7, content: "https://result.invalid/PRIVATE_RESULT", tool_calls: [call("ignored-result")] },
  ]);
  assert.deepEqual(rows[0].tools.map(({ id, name }) => ({ id, name })), [{ id: "7", name: "nested-name" }, { id: "flat", name: "flat-name" }]);
  assert.deepEqual(rows[1].tools, []);
  assert.deepEqual(rows[2].tools, []);
  assert.equal(rows[3].toolCallId, "7");
  assert.equal(rows[3].toolName, "nested-name");
  assert.equal(rows[3].text, "");
  assert.deepEqual(rows[3].tools, []);
  assert.doesNotMatch(JSON.stringify(rows), /PRIVATE_|https:/);
});

test("tool results loaded before calls become associated only after raw pages merge, without mutating history", () => {
  const latest = parseHistory([
    { id: "result", role: "tool", tool_call_id: "cross-page", name: "search", content: "PRIVATE_RESULT" },
    { id: "answer", role: "assistant", content: "完成答复" },
  ]);
  const initial = historyForDisplay(latest);
  const initialSnapshot = structuredClone(initial);
  assert.deepEqual(displayTools(initial).map(({ id, hasResult, orphan }) => ({ id, hasResult, orphan })), [
    { id: "cross-page", hasResult: true, orphan: true },
  ]);
  const older = parseHistory([
    { id: "user", role: "user", content: "问题" },
    { id: "call", role: "assistant", content: "", tool_calls: [call("cross-page")] },
  ]);
  const merged = mergeHistory(latest, older, true);
  const before = structuredClone(merged);
  freezeHistory(merged);
  const visible = historyForDisplay(merged);
  assert.deepEqual(visible.map(({ id }) => id), ["user", "call", "answer"]);
  const tools = displayTools(visible);
  assert.equal(tools.length, 1);
  assert.deepEqual(Object.keys(tools[0]).sort(), ["key", "id", "name", "hasResult", "orphan"].sort());
  assert.equal(typeof tools[0].key, "string");
  assert.ok(tools[0].key.length > 0);
  assert.deepEqual({ ...tools[0], key: "stable-key" }, { key: "stable-key", id: "cross-page", name: "search", hasResult: true, orphan: false });
  assert.deepEqual(historyForDisplay(merged), visible);
  assert.deepEqual(merged, before);
  assert.deepEqual(initial, initialSnapshot, "deriving another page must not mutate an earlier display snapshot");
  assert.doesNotMatch(JSON.stringify(visible), /PRIVATE_|running|success/);
});

test("same-named tools associate by ID, duplicate result rows do not duplicate summaries, and unmatched IDs remain orphaned", () => {
  const rows = parseHistory([
    { id: "u", role: "user", content: "问题" },
    { id: "calls", role: "assistant", content: "", tool_calls: [call("a"), call("b")] },
    { id: "b-result", role: "tool", tool_call_id: "b", name: "search", content: "PRIVATE_B_RESULT" },
    { id: "b-result-again", role: "tool", tool_call_id: "b", name: "search", content: "PRIVATE_B_REPEAT" },
    { id: "c-result", role: "tool", tool_call_id: "c", name: "search", content: "PRIVATE_C_RESULT" },
  ]);
  const visible = historyForDisplay(rows);
  assert.deepEqual(visible.map(({ id }) => id), ["u", "calls", "c-result"]);
  const tools = displayTools(visible);
  assert.deepEqual(tools.map(({ id, name, hasResult, orphan }) => ({ id, name, hasResult, orphan })), [
    { id: "a", name: "search", hasResult: false, orphan: false },
    { id: "b", name: "search", hasResult: true, orphan: false },
    { id: "c", name: "search", hasResult: true, orphan: true },
  ]);
  assert.equal(new Set(tools.map(({ key }) => key)).size, tools.length);
  for (const tool of tools) assert.deepEqual(Object.keys(tool).sort(), ["key", "id", "name", "hasResult", "orphan"].sort());
  const refreshed = mergeHistory(rows, rows.slice(1));
  const overlapping = mergeHistory(refreshed, rows.slice(0, 3), true);
  assert.equal(overlapping.length, rows.length);
  assert.deepEqual(displayTools(historyForDisplay(overlapping)), tools);
  assert.doesNotMatch(JSON.stringify(visible), /PRIVATE_|running|success/);
});

test("missing call IDs never associate by name and still yield distinct safe summaries", () => {
  const rows = parseHistory([
    { id: "u", role: "user", content: "问题" },
    { id: "call-without-id", role: "assistant", tool_calls: [call(undefined)] },
    { id: "result-without-id", role: "tool", name: "search", content: "PRIVATE_RESULT" },
  ]);
  const tools = displayTools(historyForDisplay(rows));
  assert.equal(tools.length, 2);
  assert.deepEqual(tools.map(({ name, hasResult, orphan }) => ({ name, hasResult, orphan })), [
    { name: "search", hasResult: false, orphan: false },
    { name: "search", hasResult: true, orphan: true },
  ]);
  assert.equal(new Set(tools.map(({ key }) => key)).size, 2);
  assert.doesNotMatch(JSON.stringify(tools), /PRIVATE_|running|success/);
});

test("tool association cannot cross a user boundary, including a hidden or empty user row", () => {
  for (const boundary of [{ content: "第二问" }, { content: "PRIVATE_HIDDEN_USER", hidden: true }, { content: "" }]) {
    const rows = parseHistory([
      { id: "u1", role: "user", content: "第一问" },
      { id: "call", role: "assistant", tool_calls: [call("reused-id")] },
      { id: "u2", role: "user", ...boundary },
      { id: "result", role: "tool", tool_call_id: "reused-id", name: "search", content: "PRIVATE_RESULT" },
    ]);
    const tools = displayTools(historyForDisplay(rows));
    assert.deepEqual(tools.map(({ id, hasResult, orphan }) => ({ id, hasResult, orphan })), [
      { id: "reused-id", hasResult: false, orphan: false },
      { id: "reused-id", hasResult: true, orphan: true },
    ]);
    assert.equal(new Set(tools.map(({ key }) => key)).size, 2, "keys must distinguish separate user segments");
  }
});

test("hidden tool results cannot complete visible calls and hidden calls cannot consume visible orphan results", () => {
  const rows = parseHistory([
    { id: "u", role: "user", content: "问题" },
    { id: "call", role: "assistant", tool_calls: [call("visible")] },
    { id: "hidden-result", role: "tool", tool_call_id: "visible", hidden: true, content: "PRIVATE_HIDDEN_RESULT" },
    { id: "hidden-call", role: "assistant", tool_calls: [call("hidden", "PRIVATE_HIDDEN_TOOL")], hidden: true },
    { id: "visible-result", role: "tool", tool_call_id: "hidden", name: "search", content: "PRIVATE_RESULT" },
  ]);
  const visible = historyForDisplay(rows);
  assert.deepEqual(visible.map(({ id }) => id), ["u", "call", "visible-result"]);
  assert.deepEqual(displayTools(visible).map(({ id, hasResult, orphan }) => ({ id, hasResult, orphan })), [
    { id: "visible", hasResult: false, orphan: false },
    { id: "hidden", hasResult: true, orphan: true },
  ]);
  assert.doesNotMatch(JSON.stringify(visible), /PRIVATE_/);
});

test("raw pagination offsets use returned, including empty responses and non-visible rows", () => {
  for (const [returned, limit, offset, expectedOffset, hasMore] of [[8, 8, 20, 28, true], [2, 8, 20, 22, false], [0, 8, 20, 20, false]]) {
    const result = messagePage({
      messages: returned ? [{ id: "hidden", role: "assistant", hidden: true }, { id: "empty", role: "assistant", content: "" }] : [],
      pagination: { returned, limit, offset, order: "latest" },
    });
    assert.equal(result.offset, expectedOffset);
    assert.equal(result.hasMore, hasMore);
    assert.deepEqual(result.messages.map(({ id }) => id), returned ? ["hidden", "empty"] : []);
    assert.deepEqual(historyForDisplay(result.messages), []);
  }
});

test("invalid page containers and pagination boundaries still reject instead of becoming empty history", () => {
  for (const data of [null, undefined, {}, [], { messages: "invalid" }]) assert.throws(() => messagePage(data), /格式不正确/);
  const valid = { returned: 0, limit: 1, offset: 0, order: "latest" };
  for (const invalid of [{ returned: -1 }, { returned: 0.5 }, { returned: "0" }, { limit: 0 }, { limit: null }, { offset: -1 }, { offset: 0.5 }, { order: "oldest" }]) {
    assert.throws(() => messagePage({ messages: [], pagination: { ...valid, ...invalid } }), /格式不正确/);
  }
});

test("more than ten thousand empty raw rows survive overlapping page merges without producing bubbles", () => {
  const rows = parseHistory(Array.from({ length: 10001 }, (_, id) => ({ id, role: "assistant", content: "" })));
  const merged = mergeHistory(rows.slice(5000), rows.slice(0, 5001), true);
  assert.equal(merged.length, 10001);
  assert.deepEqual(merged.map(({ id }) => id), Array.from({ length: 10001 }, (_, id) => String(id)));
  assert.deepEqual(historyForDisplay(merged), []);
});

test("reconciliation matches normalized user text, never a raw content fallback", () => {
  const turn = { text: "原始问题", answer: "答案", delivery: "sent", status: "complete", baseIds: [] };
  for (const display_content of ["公开问题", null, ""]) {
    const history = parseHistory([
      { id: "u", role: "user", content: "原始问题", display_content },
      { id: "a", role: "assistant", content: "答案" },
    ]);
    assert.equal(reconcileTurns([turn], history)[0], turn);
  }
  const canonical = [historyMessage("u", "user", "公开问题", { content: "原始问题" }), historyMessage("a", "assistant", "答案")];
  assert.equal(reconcileTurns([turn], canonical)[0], turn, "ignore legacy content even if supplied by an older cache");
  const result = reconcileTurns([{ ...turn, text: "公开问题" }], canonical)[0];
  assert.equal(result.remoteUserId, "u");
  assert.equal(result.hideAnswer, true);
});

for (const [label, reply] of [
  ["legacy raw content", historyMessage("a", "assistant", "其他正文", { content: "答案" })],
  ["notices", historyMessage("a", "assistant", "", { notices: ["答案"] })],
  ["hidden text", historyMessage("a", "assistant", "答案", { hidden: true })],
  ["tool result text", historyMessage("a", "tool", "答案", { toolCallId: "result", toolName: "search" })],
]) {
  test(`reconciliation cannot use ${label} as a persisted assistant answer`, () => {
    const turn = { text: "问题", answer: "答案", delivery: "unknown", status: "unknown", baseIds: [] };
    const result = reconcileTurns([turn], [historyMessage("u", "user", "问题"), reply])[0];
    assert.equal(result.remoteUserId, "u");
    assert.equal(result.hideUser, true);
    assert.equal(result.hideAnswer, false);
    assert.equal(result.answer, "答案");
  });
}

test("reasoning and media notices cannot hide local answers, while sidecar final text can", () => {
  for (const [content, answer] of [
    [[{ type: "reasoning", text: "答案" }], "答案"],
    [[{ type: "thinking", thinking: "PRIVATE_THOUGHT" }], "仅含思考记录"],
    [[{ type: "image_url", image_url: { url: "https://media.invalid/PRIVATE_IMAGE" } }], "图片内容（暂不预览）"],
  ]) {
    const history = parseHistory([{ id: "u", role: "user", content: "问题" }, { id: "a", role: "assistant", content }]);
    const turn = { text: "问题", answer, delivery: "sent", status: "complete", baseIds: [] };
    assert.equal(reconcileTurns([turn], history)[0].hideAnswer, false);
  }
  const history = parseHistory([
    { id: "u", role: "user", content: { message: "问题" } },
    { id: "a", role: "assistant", content: "", codex_message_items: [finalItem("答案完整文本")] },
  ]);
  const turn = { text: "问题", answer: "答案", delivery: "unknown", status: "unknown", baseIds: [] };
  assert.equal(reconcileTurns([turn], history)[0].hideAnswer, true);
});

test("raw empty and hidden IDs anchor baseIds across overlapping pages and exclude old identical prompts", () => {
  const base = parseHistory([
    { id: "old-u", role: "user", content: "重复" },
    { id: "old-a", role: "assistant", content: "回答" },
    { id: "hidden", role: "tool", hidden: true },
    { id: "empty", role: "assistant", content: "" },
  ]);
  const turn = { text: "重复", answer: "回答", delivery: "unknown", status: "unknown", baseIds: base.map(({ id }) => id) };
  assert.deepEqual(turn.baseIds, ["old-u", "old-a", "hidden", "empty"]);
  assert.equal(reconcileTurns([turn], base)[0], turn);
  const incoming = parseHistory([
    { id: "empty", role: "assistant", content: "" },
    { id: "new-u", role: "user", content: { text: "重复" } },
    { id: "new-a", role: "assistant", content: [{ type: "output_text", text: "回答" }] },
  ]);
  const result = reconcileTurns([turn], mergeHistory(base, incoming))[0];
  assert.equal(result.remoteUserId, "new-u");
  assert.equal(result.hideAnswer, true);
  assert.equal(reconcileTurns([turn], incoming.slice(1))[0], turn, "a missing raw boundary is not permission to guess");
});

test("reconciliation stops at the next raw user row even if that row has no visible bubble", () => {
  for (const boundary of [{ content: "" }, { content: "PRIVATE_HIDDEN_USER", hidden: true }]) {
    const history = parseHistory([
      { id: "u1", role: "user", content: "第一问" },
      { id: "u2", role: "user", ...boundary },
      { id: "a2", role: "assistant", content: "第二问的回答" },
    ]);
    const turn = { text: "第一问", answer: "第二问的回答", delivery: "sent", status: "complete", baseIds: [] };
    const result = reconcileTurns([turn], history)[0];
    assert.equal(result.remoteUserId, "u1");
    assert.equal(result.hideAnswer, false);
  }
});

test("historyToolIds deduplicate only non-hidden tool IDs in the matched user segment, never by name", () => {
  const turn = {
    text: "问题", answer: "部分答案", delivery: "unknown", status: "unknown", baseIds: [],
    tools: ["matched", "pending", "orphan", "7", "hidden", "foreign", "unmatched"].map((id) => ({ id, name: "search", state: "ended" })),
  };
  const history = parseHistory([
    { id: "u", role: "user", content: "问题" },
    { id: "calls", role: "assistant", tool_calls: [call("matched"), call("pending"), call(7), call(undefined)] },
    { id: "result", role: "tool", tool_call_id: "matched", name: "search", content: "部分答案" },
    { id: "result-repeat", role: "tool", tool_call_id: "matched", name: "search" },
    { id: "orphan", role: "tool", tool_call_id: "orphan", name: "search" },
    { id: "no-id", role: "tool", name: "search" },
    { id: "hidden-call", role: "assistant", hidden: true, tool_calls: [call("hidden")] },
    { id: "hidden-result", role: "tool", hidden: true, tool_call_id: "hidden", name: "search" },
    { id: "other-u", role: "user", content: "另一问" },
    { id: "foreign", role: "assistant", tool_calls: [call("foreign")] },
  ]);
  const before = structuredClone({ turn, history });
  freezeHistory(history);
  const result = reconcileTurns([turn], history)[0];
  assert.equal(result.remoteUserId, "u");
  assert.equal(result.hideAnswer, false, "a matching raw tool result is not assistant prose");
  assert.deepEqual([...result.historyToolIds].sort(), ["7", "matched", "orphan", "pending"]);
  const suppressedIds = new Set(result.historyToolIds);
  assert.deepEqual(result.tools.filter(({ id }) => !suppressedIds.has(id)).map(({ id }) => id), ["hidden", "foreign", "unmatched"]);
  assert.deepEqual({ turn, history }, before);
  assert.deepEqual(reconcileTurns([result], history)[0], result);
});

test("a non-matching or unsent local turn never loses same-named tools to unrelated history", () => {
  const history = parseHistory([
    { id: "u", role: "user", content: "其他问题" },
    { id: "call", role: "assistant", tool_calls: [call("same-id")] },
  ]);
  for (const delivery of ["sent", "unknown", "unsent"]) {
    const turn = { text: "本地问题", answer: "本地回答", delivery, status: "unknown", baseIds: [], tools: [{ id: "same-id", name: "search", state: "ended" }] };
    assert.equal(reconcileTurns([turn], history)[0], turn);
  }
});

test("unknown and malformed Codex phases cannot become persisted answers", () => {
  for (const phase of ["draft", "", ["analysis"], ["final"], 0, {}, true]) {
    for (const encode of [(items) => items, JSON.stringify]) {
      const history = parseHistory([
        { id: "u", role: "user", content: "问题" },
        { id: "a", role: "assistant", content: "", codex_message_items: encode([finalItem("PRIVATE_DRAFT", { phase })]) },
      ]);
      assert.equal(history[1].text, "");
      assert.deepEqual(history[1].notices, ["暂不支持的内容"]);
      assert.doesNotMatch(JSON.stringify(historyForDisplay(history)), /PRIVATE_DRAFT/);
      const turn = { text: "问题", answer: "PRIVATE_DRAFT", delivery: "sent", status: "complete", baseIds: [] };
      assert.equal(reconcileTurns([turn], history)[0].hideAnswer, false);
    }
  }
  for (const phase of [undefined, null, "final", "final_answer"]) {
    const [row] = parseHistory([{ id: "a", role: "assistant", content: "", codex_message_items: [finalItem("正式答案", { phase })] }]);
    assert.equal(row.text, "正式答案");
    assert.deepEqual(row.notices, []);
  }
});

test("orphan results deduplicate by ID within raw user boundaries, never by name", () => {
  for (const boundary of [{ content: "下一问" }, { content: "" }, { content: "PRIVATE_HIDDEN", hidden: true }]) {
    const rows = parseHistory([
      { id: "hidden", role: "tool", tool_call_id: "same", hidden: true },
      { id: "r1", role: "tool", tool_call_id: "same", name: "search" },
      { id: "r2", role: "tool", tool_call_id: "same", name: "search" },
      { id: "no-id-1", role: "tool", name: "search" },
      { id: "no-id-2", role: "tool", name: "search" },
      { id: "u", role: "user", ...boundary },
      { id: "r3", role: "tool", tool_call_id: "same", name: "search" },
      { id: "r4", role: "tool", tool_call_id: "same", name: "search" },
    ]);
    const snapshot = structuredClone(rows);
    freezeHistory(rows);
    const tools = displayTools(historyForDisplay(rows));
    assert.deepEqual(tools.map(({ id }) => id), ["same", null, null, "same"]);
    assert.ok(tools.every(({ hasResult, orphan }) => hasResult && orphan));
    assert.equal(new Set(tools.map(({ key }) => key)).size, 4);
    assert.deepEqual(rows, snapshot);
  }
});

test("discarded history windows clear stale tool suppression without erasing confirmed text state", () => {
  const turn = { text: "问题", answer: "答案", delivery: "sent", status: "complete", baseIds: [], tools: [{ id: "c", name: "search", state: "ended" }] };
  const history = parseHistory([
    { id: "u", role: "user", content: "问题" },
    { id: "c", role: "assistant", tool_calls: [call("c")] },
    { id: "a", role: "assistant", content: "答案" },
  ]);
  const matched = reconcileTurns([turn], history)[0];
  assert.deepEqual(matched.historyToolIds, ["c"]);
  const latest = mergeHistory(history, parseHistory([
    { id: "u2", role: "user", content: "其他问题" },
    { id: "a2", role: "assistant", content: "其他答案" },
  ]));
  for (const stale of [matched, { ...matched, remoteUserId: undefined, baseIds: ["missing"] }, { ...matched, delivery: "unsent" }]) {
    const result = reconcileTurns([stale], latest)[0];
    assert.deepEqual(result.historyToolIds, []);
    assert.equal(result.hideUser, stale.hideUser);
    assert.equal(result.hideAnswer, stale.hideAnswer);
    assert.equal(result.tools, stale.tools);
    assert.deepEqual(stale.historyToolIds, ["c"]);
    assert.equal(reconcileTurns([result], latest)[0], result);
  }
  assert.deepEqual(reconcileTurns([matched], history.slice(0, 1))[0].historyToolIds, []);
});
