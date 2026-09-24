import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { applyTurnEvent, errorText, mergeHistory, messagePage, readChatStream, reconcileTurns } from "./chat-stream.js";

const SELECTION_KEY = "hermes.chat.selection";
let nextKey = 0;
const localKey = () => `local-${Date.now()}-${++nextKey}`;
const identity = (id, profile) => JSON.stringify([String(id), profile || ""]);
const settledStatuses = ["idle", "complete", "error", "interrupted"];

function conversation(selection = {}) {
  return {
    key: localKey(), storedId: selection.stored_session_id || null,
    sessionId: null, profile: selection.profile || null,
    title: selection.title || (selection.stored_session_id ? "历史对话" : "新对话"),
    history: [], turns: [], input: "", sending: false, error: "", warning: "",
    status: selection.stored_session_id ? "unknown" : "idle", stateMessage: "",
    checking: false, stateError: "", watchRunning: false,
    historyLoaded: false, historyLoading: false, historyError: "", moreLoading: false,
    offset: 0, hasMore: false, cursorInvalid: false, version: 0, checkEpoch: 0,
  };
}

function initialStore() {
  let selection;
  try {
    const saved = JSON.parse(localStorage.getItem(SELECTION_KEY));
    if (typeof saved?.stored_session_id === "string" && saved.stored_session_id) {
      selection = { stored_session_id: saved.stored_session_id, profile: typeof saved.profile === "string" ? saved.profile : null };
    }
  } catch { /* Storage is optional; messages never go into it. */ }
  const first = conversation(selection);
  return { selected: first.key, conversations: { [first.key]: first } };
}

async function jsonResponse(response) {
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorText(data?.detail, `请求失败 HTTP ${response.status}`));
  if (!data) throw new Error("服务器响应格式不正确，请重试。");
  return data;
}

function sessionURL(current, endpoint, offset) {
  const query = new URLSearchParams();
  if (current.profile) query.set("profile", current.profile);
  if (offset !== undefined) {
    query.set("limit", "50");
    query.set("offset", String(offset));
  }
  return `/api/sessions/${encodeURIComponent(current.storedId)}/${endpoint}?${query}`;
}

export default function Chat({ active = true }) {
  const [store, setStore] = useState(initialStore);
  // Synchronous authoritative snapshots prevent double submits and keep every
  // async callback tied to its original local key, even before React re-renders.
  const storeRef = useRef(store);
  const mounted = useRef(true);
  const [listing, setListing] = useState({ items: [], offset: 0, total: 0, loading: false, error: "", loaded: false });
  const listingRef = useRef(listing);
  const listRequest = useRef(null);
  const historyRequests = useRef(new Map());
  const stateRequests = useRef(new Map());
  const sendRequests = useRef(new Map());
  const [storageError, setStorageError] = useState("");
  const logRef = useRef(null);
  const inputRef = useRef(null);
  const composing = useRef(false);
  const stickToBottom = useRef(true);
  const prependScroll = useRef(null);
  const current = store.conversations[store.selected];

  const change = useCallback((key, transform) => {
    if (!mounted.current) return;
    const previous = storeRef.current;
    if (!previous.conversations[key]) return;
    const updated = transform(previous.conversations[key]);
    const next = { ...previous, conversations: { ...previous.conversations, [key]: updated } };
    storeRef.current = next;
    setStore(next);
    return updated;
  }, []);

  const changeListing = useCallback((transform) => {
    if (!mounted.current) return;
    listingRef.current = transform(listingRef.current);
    setListing(listingRef.current);
  }, []);

  const loadSessions = useCallback(async (refresh = false) => {
    listRequest.current?.abort();
    const controller = new AbortController();
    listRequest.current = controller;
    const offset = refresh ? 0 : listingRef.current.offset;
    changeListing((old) => ({ ...old, loading: true, error: "", retryRefresh: refresh }));
    try {
      const data = await jsonResponse(await fetch(`/api/sessions?limit=20&offset=${offset}`, { signal: controller.signal }));
      if (controller.signal.aborted || listRequest.current !== controller || !mounted.current) return;
      if (!Array.isArray(data.sessions) || !Number.isInteger(data.total) || !Number.isInteger(data.offset)) {
        throw new Error("会话列表响应格式不正确，请重试。");
      }
      changeListing((old) => {
        const items = refresh ? data.sessions : [...old.items, ...data.sessions];
        const seen = new Set();
        return {
          ...old, loaded: true, offset: data.offset + data.sessions.length, total: data.total,
          items: items.filter((item) => {
            if (item.id == null) return false;
            const id = identity(item.id, item.profile);
            if (seen.has(id)) return false;
            seen.add(id);
            return true;
          }),
        };
      });
      for (const local of Object.values(storeRef.current.conversations)) {
        const summary = data.sessions.find((item) => local.storedId && identity(local.storedId, local.profile) === identity(item.id, item.profile));
        if (summary?.title && summary.title !== local.title) change(local.key, (old) => ({ ...old, title: summary.title }));
      }
    } catch (error) {
      if (!controller.signal.aborted && listRequest.current === controller) {
        changeListing((old) => ({ ...old, error: error.message }));
      }
    } finally {
      if (listRequest.current === controller) changeListing((old) => ({ ...old, loading: false }));
    }
  }, [changeListing, change]);

  const loadHistory = useCallback(async (key, { more = false, signal } = {}) => {
    const before = storeRef.current.conversations[key];
    if (!before?.storedId || before.sending || signal?.aborted || (more && (before.status === "running" || before.checking))) return;
    historyRequests.current.get(key)?.controller.abort();
    const request = { controller: new AbortController(), version: before.version };
    historyRequests.current.set(key, request);
    const abort = () => request.controller.abort();
    // Keep the parent listener removable after either success or cancellation.
    signal?.addEventListener("abort", abort, { once: true });
    const valid = () => mounted.current && !request.controller.signal.aborted
      && historyRequests.current.get(key) === request
      && storeRef.current.conversations[key]?.version === request.version
      && storeRef.current.conversations[key]?.storedId === before.storedId
      && storeRef.current.conversations[key]?.profile === before.profile
      && !storeRef.current.conversations[key]?.sending;
    change(key, (old) => ({ ...old, historyLoading: true, moreLoading: more, historyError: "" }));

    async function page(offset, older) {
      const data = await jsonResponse(await fetch(sessionURL(before, "messages", offset), { signal: request.controller.signal }));
      if (!valid()) return null;
      const result = messagePage(data);
      if (older && storeRef.current.conversations[key].status === "running") return null;
      if (older && storeRef.current.selected === key && logRef.current) {
        prependScroll.current = { key, height: logRef.current.scrollHeight, top: logRef.current.scrollTop };
      }
      change(key, (old) => {
        const history = mergeHistory(old.history, result.messages, older);
        return {
          ...old, history, turns: reconcileTurns(old.turns, history), historyLoaded: true,
          storedId: data.session_id ?? old.storedId, profile: data.profile ?? old.profile,
          offset: result.offset, hasMore: result.hasMore,
          // An explicit older-page request always rebases an invalid cursor first.
          cursorInvalid: more && old.status !== "running" ? false : old.cursorInvalid,
        };
      });
      return result;
    }

    try {
      if (!more || before.cursorInvalid || !before.historyLoaded) {
        const latest = await page(0, false);
        if (more && latest?.hasMore && valid() && storeRef.current.conversations[key].status !== "running") {
          await page(latest.offset, true);
        }
      } else if (before.hasMore) await page(before.offset, true);
    } catch (error) {
      if (valid()) change(key, (old) => ({ ...old, historyError: error.message }));
    } finally {
      signal?.removeEventListener("abort", abort);
      if (historyRequests.current.get(key) === request) {
        historyRequests.current.delete(key);
        change(key, (old) => ({ ...old, historyLoading: false, moreLoading: false }));
      }
    }
  }, [change]);

  const readState = useCallback(async (key, signal) => {
    const before = storeRef.current.conversations[key];
    if (!before?.storedId || before.sending || signal?.aborted) return null;
    stateRequests.current.get(key)?.controller.abort();
    const request = { controller: new AbortController(), version: before.version };
    stateRequests.current.set(key, request);
    const abort = () => request.controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    const valid = () => mounted.current && !request.controller.signal.aborted
      && stateRequests.current.get(key) === request
      && storeRef.current.conversations[key]?.version === request.version
      && storeRef.current.conversations[key]?.storedId === before.storedId
      && storeRef.current.conversations[key]?.profile === before.profile
      && !storeRef.current.conversations[key]?.sending;
    change(key, (old) => ({ ...old, checking: true, stateError: "" }));
    try {
      const data = await jsonResponse(await fetch(sessionURL(before, "state"), { signal: request.controller.signal }));
      if (!valid()) return null;
      if (!["running", "unknown", ...settledStatuses].includes(data.status)) throw new Error("任务状态响应格式不正确。");
      const ended = before.watchRunning && settledStatuses.includes(data.status);
      change(key, (old) => ({
        ...old, status: data.status, stateMessage: errorText(data.message, ""),
        storedId: data.stored_session_id ?? old.storedId,
        sessionId: data.session_id ?? old.sessionId, profile: data.profile ?? old.profile,
        watchRunning: data.status === "running" || (old.watchRunning && data.status === "unknown"),
        cursorInvalid: old.cursorInvalid || data.status === "running",
      }));
      return { ended };
    } catch (error) {
      if (valid()) change(key, (old) => ({ ...old, status: "unknown", stateError: error.message }));
      return null;
    } finally {
      signal?.removeEventListener("abort", abort);
      if (stateRequests.current.get(key) === request) {
        stateRequests.current.delete(key);
        change(key, (old) => ({ ...old, checking: false }));
      }
    }
  }, [change]);

  useEffect(() => {
    mounted.current = true;
    void loadSessions(true);
    return () => {
      mounted.current = false;
      listRequest.current?.abort();
      for (const { controller } of historyRequests.current.values()) controller.abort();
      for (const { controller } of stateRequests.current.values()) controller.abort();
      for (const controller of sendRequests.current.values()) controller.abort();
    };
  }, [loadSessions]);

  useEffect(() => {
    try {
      if (current.storedId) localStorage.setItem(SELECTION_KEY, JSON.stringify({ stored_session_id: current.storedId, profile: current.profile }));
      else localStorage.removeItem(SELECTION_KEY);
      setStorageError("");
    } catch {
      setStorageError("浏览器无法保存所选会话；刷新后请从列表重新打开。");
    }
  }, [current.storedId, current.profile]);

  useEffect(() => {
    if (!active || !current.storedId || current.sending) return;
    const key = current.key;
    const controller = new AbortController();
    let timer;
    async function check(first) {
      if (controller.signal.aborted) return;
      let result;
      if (first) {
        // Opening never submits a prompt, even when a previous process is busy.
        [, result] = await Promise.all([
          loadHistory(key, { signal: controller.signal }), readState(key, controller.signal),
        ]);
      } else result = await readState(key, controller.signal);
      if (controller.signal.aborted) return;
      if (result?.ended) await loadHistory(key, { signal: controller.signal });
      if (controller.signal.aborted) return;
      const latest = storeRef.current.conversations[key];
      if (latest?.watchRunning && !latest.sending) timer = setTimeout(() => { void check(false); }, 3000);
    }
    void check(true);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [active, current.key, current.storedId, current.profile, current.sending, current.checkEpoch, loadHistory, readState]);

  useEffect(() => {
    stickToBottom.current = true;
    composing.current = false;
    if (active) inputRef.current?.focus({ preventScroll: true });
  }, [active, current.key]);

  useLayoutEffect(() => {
    if (!active || !logRef.current) return;
    const log = logRef.current;
    const anchor = prependScroll.current;
    if (anchor?.key === current.key) {
      log.scrollTop = anchor.top + log.scrollHeight - anchor.height;
      prependScroll.current = null;
    } else if (stickToBottom.current) log.scrollTop = log.scrollHeight;
  }, [active, current.key, current.history, current.turns]);

  function selectSession(item) {
    const previous = storeRef.current;
    let target = item.key && previous.conversations[item.key];
    if (!target) target = Object.values(previous.conversations).find((candidate) => candidate.storedId
      && identity(candidate.storedId, candidate.profile) === identity(item.id, item.profile));
    if (!target) target = conversation({ stored_session_id: String(item.id), profile: item.profile, title: item.title });
    const next = { selected: target.key, conversations: { ...previous.conversations, [target.key]: target } };
    storeRef.current = next;
    setStore(next);
  }

  function newDraft() {
    const draft = conversation({ profile: current.profile });
    const next = { selected: draft.key, conversations: { ...storeRef.current.conversations, [draft.key]: draft } };
    storeRef.current = next;
    setStore(next);
  }

  function retryHistory() {
    change(current.key, (old) => ({ ...old, checkEpoch: old.checkEpoch + 1 }));
  }

  async function sendManual() {
    const before = storeRef.current.conversations[storeRef.current.selected];
    const text = before.input.trim();
    if (!text || before.sending || before.status === "running" || before.checking || before.historyLoading
      || (before.storedId && !before.historyLoaded)) return;
    const key = before.key;
    const turnId = localKey();
    const controller = new AbortController();
    sendRequests.current.set(key, controller);
    historyRequests.current.get(key)?.controller.abort();
    stateRequests.current.get(key)?.controller.abort();
    change(key, (old) => ({
      ...old, input: "", sending: true, error: "", warning: "", stateError: "", stateMessage: "",
      status: "running", watchRunning: false, version: old.version + 1, cursorInvalid: true,
      title: old.title === "新对话" ? text.slice(0, 36) : old.title,
      historyLoading: false, moreLoading: false, checking: false,
      turns: [...old.turns, {
        id: turnId, text, answer: "", delivery: "pending", status: "pending", tools: [],
        baseIds: old.history.map((message) => message.id), hideUser: false, hideAnswer: false,
      }],
    }));
    if (storeRef.current.selected === key) stickToBottom.current = true;

    function updateTurn(transform, patch = {}) {
      change(key, (old) => ({ ...old, ...patch, turns: updateTurns(old.turns, turnId, transform) }));
    }
    function fail(message, delivery = "unknown", busy = false) {
      change(key, (old) => ({
        ...old, error: message, input: old.input || text, status: busy ? "running" : "unknown",
        watchRunning: busy,
        turns: updateTurns(old.turns, turnId, (turn) => ({
          ...turn, delivery, status: delivery === "unsent" ? "error" : "unknown",
          tools: turn.tools.map((tool) => tool.state === "running" ? { ...tool, state: "unknown" } : tool),
        })),
      }));
    }

    try {
      const response = await fetch("/api/chat", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
        body: JSON.stringify({ text, ...(before.storedId ? { stored_session_id: before.storedId } : {}), ...(before.profile ? { profile: before.profile } : {}) }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        const detail = errorText(data?.detail, `HTTP ${response.status}`);
        fail(response.status === 409 ? `已有任务正在进行，本条消息未发送。${detail}` : `本条消息未发送：${detail}`, "unsent", response.status === 409);
        return;
      }
      await readChatStream(response.body, (event, envelope) => {
        if (!mounted.current) return;
        const payload = envelope.payload ?? {};
        switch (event) {
          case "session.ready":
            change(key, (old) => ({
              ...old,
              storedId: envelope.stored_session_id ?? payload.stored_session_id ?? old.storedId,
              sessionId: envelope.session_id ?? payload.session_id ?? old.sessionId,
              profile: envelope.profile ?? payload.profile ?? old.profile,
            }));
            break;
          case "message.delta":
          case "tool.start":
          case "tool.complete":
            updateTurn((turn) => applyTurnEvent(turn, event, payload));
            break;
          case "message.complete": {
            if (!["complete", "error", "interrupted"].includes(payload.status)) break;
            const success = payload.status === "complete";
            const message = success ? "" : errorText(payload.error, payload.status === "interrupted" ? "生成已中断，请核对历史后再决定是否重试。" : "生成失败，请核对历史后再决定是否重试。");
            updateTurn((turn) => applyTurnEvent(turn, event, payload), { status: payload.status, error: message, watchRunning: false });
            if (!success) change(key, (old) => ({ ...old, input: old.input || text }));
            break;
          }
          case "chat.error":
            if (["unknown", "error"].includes(payload.status)) {
              fail(errorText(payload.message, "提交失败，请核对历史后再决定是否重试。"), payload.status === "error" ? "unsent" : "unknown");
            }
            break;
          case "error":
          case "session.error":
            change(key, (old) => ({ ...old, warning: errorText(payload.message ?? payload, "服务提示异常，仍在等待终态。") }));
            break;
          default:
            break;
        }
      });
    } catch (error) {
      if (mounted.current) fail(`请求未确认：${error.message}`);
    } finally {
      sendRequests.current.delete(key);
      change(key, (old) => ({ ...old, sending: false }));
      if (mounted.current && storeRef.current.conversations[key]?.storedId) void loadSessions(true);
    }
  }

  const locals = Object.values(store.conversations);
  const localIdentities = new Set(locals.filter((item) => item.storedId).map((item) => identity(item.storedId, item.profile)));
  const sessions = [
    ...locals.map((item) => {
      const summary = listing.items.find((saved) => item.storedId && identity(saved.id, saved.profile) === identity(item.storedId, item.profile));
      return { ...item, id: item.storedId, preview: item.turns.at(-1)?.text || summary?.preview || "", local: true };
    }),
    ...listing.items.filter((item) => !localIdentities.has(identity(item.id, item.profile))),
  ];
  const blocked = current.sending || current.status === "running" || current.checking || current.historyLoading
    || (current.storedId && !current.historyLoaded);
  const toolLabels = { running: "运行中", complete: "完成", unknown: "状态未知", ended: "已结束" };

  function restoreInput(text) {
    change(current.key, (old) => ({ ...old, input: text }));
    inputRef.current?.focus();
  }

  return (
    <div className="chat-shell">
      <aside className="chat-sidebar" aria-label="会话列表">
        <div className="chat-sidebar-heading">
          <h2>会话</h2>
          <button type="button" onClick={newDraft}>新建对话</button>
        </div>
        <button className="chat-list-refresh" type="button" disabled={listing.loading} onClick={() => loadSessions(true)}>刷新列表</button>
        <ul className="session-list">
          {sessions.map((item) => (
            <li key={item.key || identity(item.id, item.profile)}>
              <button type="button" className={item.key === current.key ? "session-button selected" : "session-button"}
                aria-current={item.key === current.key ? "true" : undefined} onClick={() => selectSession(item)}>
                <span className="session-title">{item.title || "未命名对话"}</span>
                {item.preview && <span className="session-preview">{item.preview}</span>}
                <span className="session-detail">{item.sending ? "发送中" : item.status === "running" ? "生成中" : item.local && !item.id ? "草稿 · 首次发送后创建" : item.profile || "历史会话"}</span>
              </button>
            </li>
          ))}
        </ul>
        {listing.loading && <p className="hint" role="status">加载会话列表…</p>}
        {listing.error && <div className="chat-feedback" role="alert"><p className="error">{listing.error}</p><button type="button" disabled={listing.loading} onClick={() => loadSessions(listing.retryRefresh)}>重试列表</button></div>}
        {listing.loaded && listing.total === 0 && !listing.error && <p className="hint">暂无已保存的会话。</p>}
        {listing.offset < listing.total && <button type="button" disabled={listing.loading} onClick={() => loadSessions(false)}>更多会话</button>}
      </aside>

      <section className="chat" aria-label="对话面板">
        <header>
          <h1>对话</h1>
          <p className="meta chat-current-title">{current.title}</p>
          {current.storedId && <button type="button" disabled={current.sending || current.historyLoading || current.checking} onClick={retryHistory}>刷新历史与状态</button>}
        </header>
        {storageError && <p className="chat-notice" role="status">{storageError}</p>}
        {current.checking && <p className="hint" role="status">正在检查任务状态…</p>}
        {!current.sending && current.status === "running" && <p className="chat-notice" role="status">此会话正在生成，请勿重复提交。每 3 秒检查一次，结束后刷新消息。</p>}
        {!current.sending && current.status === "unknown" && <p className="chat-notice" role="status">任务状态未知，不代表空闲。请先核对历史；如仍发送，后端会再次检查是否忙碌，不会自动重发。</p>}
        {!current.sending && ["error", "interrupted"].includes(current.status) && <p className="chat-notice" role="status">{current.status === "error" ? "上次任务失败。" : "上次任务已中断。"}{current.stateMessage}</p>}
        {current.stateError && <div className="chat-feedback" role="alert"><p className="error">状态读取失败：{current.stateError}</p><button type="button" onClick={retryHistory} disabled={current.checking || current.historyLoading || current.sending}>重试状态</button></div>}

        <div className="chat-log" ref={logRef} aria-label="消息记录" tabIndex={0}
          onScroll={() => { const log = logRef.current; stickToBottom.current = log.scrollHeight - log.scrollTop - log.clientHeight < 64; }}>
          {current.storedId && (current.hasMore || current.cursorInvalid) && <div className="chat-history-more">
            <button type="button" disabled={current.historyLoading || current.sending || current.status === "running" || current.checking}
              onClick={() => loadHistory(current.key, { more: true })}>{current.moreLoading ? "加载中…" : "加载更早消息"}</button>
          </div>}
          {current.historyLoading && <p className="hint" role="status">{current.moreLoading ? "正在加载更早消息…" : "正在读取历史消息…"}</p>}
          {current.historyLoaded && !current.hasMore && <p className="hint">已读取 dashboard 当前会话段；压缩前的历史可能不在此接口返回范围内。</p>}
          {current.historyError && <div className="chat-feedback" role="alert"><p className="error">历史消息读取失败：{current.historyError}</p><button type="button" disabled={current.historyLoading || current.sending} onClick={retryHistory}>重试历史</button></div>}
          {!current.historyLoading && !current.historyError && !current.history.length && !current.turns.length
            && (!current.storedId || current.historyLoaded) && <div className="state-block">
              <p>{current.status === "running" ? "等待正在进行的任务完成" : current.storedId ? "暂无可显示的消息" : "开始一段新对话"}</p>
              <p className="hint">{current.storedId ? "系统与工具内部消息不在这里显示。" : "输入消息后才会创建会话。"}</p>
            </div>}
          {current.history.map((message) => <div key={message.id} className={`msg ${message.role}`}>
            <div className="bubble"><span className="chat-sr-only">{message.role === "user" ? "你：" : "助手："}</span>{message.text}</div>
          </div>)}
          {current.turns.map((turn) => {
            if (turn.hideUser && turn.hideAnswer) return null;
            return <div key={turn.id} className="chat-turn">
              {!turn.hideUser && <div className="msg user"><div className="bubble">
                <span className="chat-sr-only">你：</span>{turn.text}
                {turn.delivery !== "sent" && <span className="message-delivery">{turn.delivery === "pending" ? "提交中，尚未确认保存" : turn.delivery === "unsent" ? "未发送" : "提交状态未知，未确认保存"}</span>}
              </div></div>}
              {!turn.hideAnswer && turn.answer && <div className="msg assistant"><div className="bubble">
                <span className="chat-sr-only">助手：</span>{turn.answer}{turn.status === "pending" && <span className="caret" aria-hidden="true" />}
                {turn.status !== "pending" && turn.status !== "complete" && <span className="message-delivery">{turn.status === "interrupted" ? "生成中断" : turn.status === "error" ? "生成失败" : "连接中断 · 状态未知"} · 已保留内容</span>}
              </div></div>}
              {turn.status === "pending" && !turn.answer && <p className="hint" role="status">等待回复…</p>}
              {turn.tools.map((tool) => <div className="tool-row" key={tool.id}><span className={`dot ${tool.state === "complete" ? "ok" : tool.state === "running" ? "run" : ""}`} aria-hidden="true" />{tool.name}<span>{toolLabels[tool.state]}</span></div>)}
              {["unsent", "unknown"].includes(turn.delivery) && <div className="chat-turn-retry">
                <span className="hint">{turn.delivery === "unsent" ? "本条未发送，可恢复输入后手动重试。" : "请先核对历史，重试可能重复提交。"}</span>
                <button type="button" disabled={current.sending} onClick={() => restoreInput(turn.text)}>恢复输入</button>
              </div>}
            </div>;
          })}
          {current.warning && <p className="chat-notice" role="status">服务提示：{current.warning}</p>}
          {current.error && <p className="error" role="alert">{current.error}</p>}
        </div>

        <form className="chat-input-row" onSubmit={(event) => { event.preventDefault(); if (!composing.current) void sendManual(); }}>
          <label className="chat-sr-only" htmlFor="chat-message-input">消息内容</label>
          <input id="chat-message-input" ref={inputRef} className="search" value={current.input} autoComplete="off"
            onChange={(event) => change(current.key, (old) => ({ ...old, input: event.target.value }))}
            onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                if (!composing.current && !event.nativeEvent.isComposing && event.keyCode !== 229 && !event.shiftKey) void sendManual();
              }
            }}
            aria-describedby="chat-input-hint" placeholder="说点什么…（Enter 发送）" />
          <button type="submit" disabled={blocked || !current.input.trim()}>{current.sending ? "发送中…" : "发送"}</button>
        </form>
        <p className="hint chat-input-hint" id="chat-input-hint">{current.sending ? "可切换会话或页面，发送会继续；输入内容按会话保留。" : current.status === "running" ? "正在生成，暂不能发送或加载更早消息。" : "Enter 发送 · 输入法选词不会发送 · 失败后不会自动重发"}</p>
      </section>
    </div>
  );
}

function updateTurns(turns, id, transform) {
  return turns.map((turn) => turn.id === id ? transform(turn) : turn);
}
