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

export function textContent(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map((part) => typeof part === "string" ? part : part?.text || "").join("");
  return "";
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
  const messages = data.messages
    .filter((message) => ["user", "assistant"].includes(message.role)
      && !message.hidden && !["hidden", "system", "tool"].includes(message.display_kind))
    .map((message) => ({
      id: String(message.id),
      role: message.role,
      text: textContent(message.display_content ?? message.content),
      content: textContent(message.content),
    }));
  return {
    messages,
    // Never use the filtered length for a backend offset.
    offset: page.offset + page.returned,
    hasMore: page.returned >= page.limit,
  };
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
    if (turn.delivery === "unsent") return turn;
    const boundary = turn.baseIds.at(-1);
    const boundaryIndex = boundary == null ? -1 : history.findIndex((message) => message.id === boundary);
    if (!turn.remoteUserId && boundary != null && boundaryIndex < 0) return turn;
    const index = history.findIndex((message, position) => position > Math.max(after, boundaryIndex) && message.role === "user"
      && (turn.remoteUserId ? message.id === turn.remoteUserId
        : !turn.baseIds.includes(message.id) && (message.content === turn.text || message.text === turn.text)));
    if (index < 0) return turn;
    after = index;
    const replies = [];
    for (let i = index + 1; i < history.length && history[i].role !== "user"; i += 1) replies.push(history[i]);
    const replyFound = replies.some((message) => message.role === "assistant"
      && (!turn.answer || [message.text, message.content].some((text) => text && text.startsWith(turn.answer.trimEnd()))));
    return {
      ...turn,
      remoteUserId: history[index].id,
      hideUser: true,
      hideAnswer: turn.hideAnswer || replyFound || (!turn.answer && turn.status === "complete"),
    };
  });
}
