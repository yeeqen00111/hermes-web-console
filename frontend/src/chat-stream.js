// Streaming parser: line endings and UTF-8 characters may straddle network chunks.
export function createSSEParser(onEvent) {
  let line = "";
  let event = "message";
  let data = [];
  let skipLF = false;
  let ended = false;

  function dispatch() {
    if (data.length) onEvent({ event, data: data.join("\n") });
    event = "message";
    data = [];
  }

  function finishLine() {
    if (!line) dispatch();
    else if (!line.startsWith(":")) {
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value || "message";
      if (field === "data") data.push(value);
    }
    line = "";
  }

  return {
    feed(chunk) {
      if (ended) return;
      for (const character of chunk) {
        if (skipLF) {
          skipLF = false;
          if (character === "\n") continue;
        }
        if (character === "\r" || character === "\n") {
          finishLine();
          skipLF = character === "\r";
        } else line += character;
      }
    },
    end() {
      if (ended) return;
      ended = true;
      if (line) finishLine();
      dispatch();
    },
  };
}

export function isTerminalEvent(event, envelope) {
  const status = envelope.payload?.status;
  return (event === "message.complete" && ["complete", "error", "interrupted"].includes(status))
    || (event === "chat.error" && ["unknown", "error"].includes(status));
}

export async function readChatStream(body, onEvent) {
  if (!body) throw new Error("响应没有可读取的消息流，提交状态未知。请先检查历史，不要重复发送。");
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let terminal = null;
  let eof = false;
  const parser = createSSEParser(({ event, data }) => {
    if (terminal) return;
    let envelope;
    try {
      envelope = JSON.parse(data);
    } catch {
      throw new Error("消息流格式异常，提交状态未知。请先检查历史。");
    }
    if (!envelope || typeof envelope !== "object") throw new Error("消息流内容异常，提交状态未知。");
    const type = event === "message" ? envelope.type || event : event;
    // Pass the entire envelope through, including the session.ready identifiers.
    onEvent(type, envelope);
    if (isTerminalEvent(type, envelope)) terminal = { event: type, envelope };
  });
  try {
    while (!terminal) {
      const { value, done } = await reader.read();
      if (done) {
        eof = true;
        parser.feed(decoder.decode());
        parser.end();
        break;
      }
      parser.feed(decoder.decode(value, { stream: true }));
    }
    if (!terminal) throw new Error("连接中断，生成及提交状态未知；已保留部分内容。请先检查历史，不要重复发送。");
    return terminal;
  } finally {
    if (!eof) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export function applyTurnEvent(turn, event, payload) {
  if (event === "message.delta") {
    return { ...turn, answer: turn.answer + (typeof payload.text === "string" ? payload.text : "") };
  }
  if (event === "message.complete" && ["complete", "error", "interrupted"].includes(payload.status)) {
    return {
      ...turn, answer: typeof payload.text === "string" ? payload.text : turn.answer,
      status: payload.status, delivery: payload.status === "complete" ? "sent" : "unknown",
      tools: turn.tools.map((tool) => tool.state === "running" ? { ...tool, state: "ended" } : tool),
    };
  }
  if (["tool.start", "tool.complete"].includes(event) && payload.tool_id != null) {
    const id = String(payload.tool_id);
    const previous = turn.tools.find((tool) => tool.id === id);
    const tool = {
      id, name: payload.name || previous?.name || "工具",
      state: event === "tool.complete" ? "complete" : "running",
    };
    return { ...turn, tools: previous ? turn.tools.map((item) => item.id === id ? tool : item) : [...turn.tools, tool] };
  }
  return turn;
}

const unsupportedContent = "暂不支持的内容";
const reasoningOnly = "仅含思考记录";

function parseContent(value, depth = 0) {
  if (value == null) return { text: "", notices: [] };
  if (typeof value === "string") return { text: value, notices: [] };
  if (depth > 16) return { text: "", notices: [unsupportedContent] };
  if (Array.isArray(value)) {
    const parts = value.map((part) => parseContent(part, depth + 1));
    return { text: parts.map((part) => part.text).join(""), notices: [...new Set(parts.flatMap((part) => part.notices))] };
  }
  if (typeof value === "object") {
    const type = value.type;
    if (["image", "image_url", "input_image"].includes(type)) return { text: "", notices: ["图片内容（暂不预览）"] };
    if (["audio", "input_audio", "output_audio"].includes(type)) return { text: "", notices: ["音频内容（暂不播放）"] };
    if (["file", "document", "input_file"].includes(type)) return { text: "", notices: ["文件内容（暂不预览）"] };
    if (["thinking", "reasoning", "redacted_thinking"].includes(type)) return { text: "", notices: [reasoningOnly] };
    if (type != null && !["text", "input_text", "output_text"].includes(type)) return { text: "", notices: [unsupportedContent] };
    const field = ["text", "output_text", "content", "message"].find((key) => value[key] != null);
    if (field) return parseContent(value[field], depth + 1);
    if (["text", "output_text", "content", "message"].some((key) => Object.hasOwn(value, key))) return { text: "", notices: [] };
  }
  return { text: "", notices: [unsupportedContent] };
}

function codexContent(value) {
  if (value == null || value === "") return { text: "", notices: [] };
  let items = value;
  if (typeof items === "string") {
    try { items = JSON.parse(items); }
    catch { return { text: "", notices: [unsupportedContent] }; }
  }
  if (!Array.isArray(items)) return { text: "", notices: [unsupportedContent] };
  let text = "";
  const notices = new Set();
  for (const item of items) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      notices.add(unsupportedContent);
      continue;
    }
    if (item.type === "reasoning") {
      notices.add(reasoningOnly);
      continue;
    }
    if (item.type !== "message" || item.role !== "assistant") continue;
    if (["analysis", "commentary"].includes(item.phase)) {
      notices.add(reasoningOnly);
      continue;
    }
    if ((item.phase != null && !["final", "final_answer"].includes(item.phase)) || !Array.isArray(item.content)) {
      notices.add(unsupportedContent);
      continue;
    }
    for (const part of item.content) {
      if (["text", "output_text"].includes(part?.type) && typeof part.text === "string") text += part.text;
      else notices.add(unsupportedContent);
    }
  }
  return { text, notices: [...notices] };
}

function storedId(value) {
  return (typeof value === "string" && value.trim()) || (typeof value === "number" && Number.isFinite(value)) ? String(value) : null;
}

function toolName(value) {
  return typeof value === "string" && value.trim() ? value : "工具";
}

export function errorText(value, fallback = "请求失败") {
  if (typeof value === "string" && value) return value;
  if (value && typeof value.message === "string") return value.message;
  if (value != null) return JSON.stringify(value);
  return fallback;
}

export function messagePage(data) {
  const page = data?.pagination;
  if (!Array.isArray(data?.messages) || page?.order !== "latest"
    || !Number.isInteger(page.returned) || page.returned < 0
    || !Number.isInteger(page.offset) || page.offset < 0
    || !Number.isInteger(page.limit) || page.limit < 1) {
    throw new Error("历史消息响应格式不正确，请重试。");
  }
  const messages = data.messages.map((message) => {
    if (!message || storedId(message.id) === null || typeof message.role !== "string") {
      throw new Error("历史消息条目格式不正确，请重试。");
    }
    const id = String(message.id);
    const hidden = Boolean(message.hidden) || ["hidden", "system", "tool"].includes(message.display_kind)
      || !["user", "assistant", "tool"].includes(message.role);
    const projected = Object.hasOwn(message, "display_content");
    const tools = !hidden && message.role === "assistant" && Array.isArray(message.tool_calls)
      ? message.tool_calls.map((call, index) => ({
        key: `${id}:call:${index}`, id: storedId(call?.id ?? call?.tool_call_id), name: toolName(call?.function?.name ?? call?.name),
      })) : [];
    let body = hidden || message.role === "tool" ? { text: "", notices: [] }
      : parseContent(projected ? message.display_content : message.content);
    if (!hidden && !projected && message.role === "assistant" && !body.text.trim() && !body.notices.length) {
      body = codexContent(message.codex_message_items);
    }
    const hasReasoning = [message.reasoning, message.reasoning_content, message.reasoning_details]
      .some((value) => typeof value === "string" && value.trim());
    let notices = body.notices.filter((notice) => notice !== reasoningOnly || (!body.text.trim() && !tools.length));
    if (!hidden && !projected && message.role === "assistant" && hasReasoning && !body.text.trim() && !tools.length && !notices.length) {
      notices = [reasoningOnly];
    }
    return {
      id, role: message.role, text: body.text, notices, hidden, tools,
      toolCallId: !hidden && message.role === "tool" ? storedId(message.tool_call_id) : null,
      toolName: !hidden && message.role === "tool" ? toolName(message.tool_name ?? message.name) : "",
    };
  });
  return {
    messages,
    // Hidden and empty rows still count toward paging and submission boundaries.
    offset: page.offset + page.returned,
    hasMore: page.returned >= page.limit,
  };
}

export function historyForDisplay(history) {
  const messages = [];
  const calls = new Map();
  const orphanIds = new Set();
  for (const message of history) {
    if (message.role === "user") {
      calls.clear();
      orphanIds.clear();
    }
    if (message.hidden) continue;
    if (message.role === "tool") {
      const call = message.toolCallId && calls.get(message.toolCallId);
      if (call) call.hasResult = true;
      else if (!orphanIds.has(message.toolCallId)) {
        messages.push({
          ...message, role: "assistant", tools: [{
            key: `${message.id}:result`, id: message.toolCallId, name: message.toolName,
            hasResult: true, orphan: true,
          }],
        });
        if (message.toolCallId) orphanIds.add(message.toolCallId);
      }
      continue;
    }
    const tools = [];
    for (const call of message.tools) {
      if (call.id && calls.has(call.id)) continue;
      const tool = { ...call, hasResult: false, orphan: false };
      tools.push(tool);
      if (call.id) calls.set(call.id, tool);
    }
    messages.push({ ...message, tools });
  }
  return messages.filter((message) => message.text.trim() || message.notices.length || message.tools.length);
}

export function mergeHistory(existing, incoming, older = false) {
  const incomingIds = new Set(incoming.map((message) => message.id));
  // A disjoint latest page leaves a gap, so restart the contiguous paging window.
  if (!older && incoming.length && !existing.some((message) => incomingIds.has(message.id))) return incoming;
  const first = older ? incoming : existing;
  const last = older ? existing : incoming;
  const latestById = new Map([...existing, ...incoming].map((message) => [message.id, message]));
  const idsInLast = new Set(last.map((message) => message.id));
  const ordered = [...first.filter((message) => !idsInLast.has(message.id)), ...last];
  const seen = new Set();
  return ordered.filter((message) => {
    if (seen.has(message.id)) return false;
    seen.add(message.id);
    return true;
  }).map((message) => latestById.get(message.id));
}

// SSE has no message IDs. Only reconcile a local turn with new, ordered server
// messages, never a pre-existing identical prompt or an explicitly rejected one.
export function reconcileTurns(turns, history) {
  let after = -1;
  return turns.map((turn) => {
    const unmatched = turn.historyToolIds?.length ? { ...turn, historyToolIds: [] } : turn;
    if (turn.delivery === "unsent") return unmatched;
    const boundary = turn.baseIds.at(-1);
    const boundaryIndex = boundary == null ? -1 : history.findIndex((message) => message.id === boundary);
    if (!turn.remoteUserId && boundary != null && boundaryIndex < 0) return unmatched;
    const index = history.findIndex((message, position) => position > Math.max(after, boundaryIndex)
      && message.role === "user" && !message.hidden && message.text.trim()
      && (turn.remoteUserId ? message.id === turn.remoteUserId
        : !turn.baseIds.includes(message.id) && message.text === turn.text));
    if (index < 0) return unmatched;
    after = index;
    const replies = [];
    for (let i = index + 1; i < history.length && history[i].role !== "user"; i += 1) {
      if (!history[i].hidden) replies.push(history[i]);
    }
    const answer = turn.answer.trimEnd();
    const replyFound = replies.some((message) => message.role === "assistant" && message.text.trim()
      && (!answer || (turn.status === "complete" ? message.text.trimEnd() === answer : message.text.startsWith(answer))));
    const historyToolIds = [...new Set(replies.flatMap((message) => message.role === "tool"
      ? [message.toolCallId] : (message.tools || []).map((tool) => tool.id)).filter(Boolean))];
    return {
      ...turn,
      remoteUserId: history[index].id,
      hideUser: true,
      hideAnswer: Boolean(replyFound || (!turn.answer && turn.status === "complete")),
      historyToolIds,
    };
  });
}
