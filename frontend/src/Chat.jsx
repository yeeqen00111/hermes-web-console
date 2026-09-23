import { useEffect, useRef, useState } from "react";

// 最简对话面板：输入 → POST /api/chat（后端转上 dashboard /api/ws）→ SSE 流式渲染。
// 消息 = 已完成的气泡列表 + 进行中的 assistant 流；工具调用先渲染成折叠行。
export default function Chat() {
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [history, setHistory] = useState([]); // [{role, text}]
  const [stream, setStream] = useState("");   // 进行中 assistant 文本
  const [tools, setTools] = useState([]);     // [{name, state, detail}]
  const abortRef = useRef(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  // 一段 SSE chunk 的解析："event: x\ndata: {...}"
  function handleChunk(chunk) {
    const lines = chunk.split("\n");
    let ev = "message";
    let data = "";
    for (const line of lines) {
      if (line.startsWith("event:")) ev = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    let params = {};
    try {
      params = data ? JSON.parse(data) : {};
    } catch {
      /* keep {} */
    }
    const payload = params.payload ?? params;

    switch (ev) {
      case "message.delta":
        setStream((s) => s + (payload.text ?? ""));
        break;
      case "message.complete":
        setHistory((h) => [...h, { role: "assistant", text: payload.text ?? "" }]);
        setStream("");
        break;
      case "tool.start":
        setTools((t) => [{ name: payload.name ?? "tool", state: "运行中", detail: "" }, ...t]);
        break;
      case "tool.complete":
        setTools((t) =>
          t.map((x) =>
            x.name === (payload.name ?? x.name) ? { ...x, state: "完成", detail: payload.result ?? "" } : x
          )
        );
        break;
      case "error":
      case "session.error":
        setError(payload.message ?? (typeof payload === "string" ? payload : "Hermes 出错"));
        break;
      default:
        break;
    }
  }

  async function sendManual() {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setHistory((h) => [...h, { role: "user", text }]);
    setStream("");
    setTools([]);
    setSending(true);
    setError("");
    abortRef.current = new AbortController();
    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
        signal: abortRef.current.signal,
      });
      if (!resp.ok) {
        setError(`后端错误 HTTP ${resp.status}: ${await resp.text().catch(() => "")}`);
        return;
      }
      // SSE 流式读取
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          handleChunk(buf.slice(0, idx));
          buf = buf.slice(idx + 2);
        }
      }
    } catch (e) {
      if (e.name !== "AbortError") setError("请求失败：" + e.message);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="chat">
      <header>
        <h1>对话</h1>
        <p className="meta">经你的后端 → dashboard /api/ws</p>
      </header>
      <div className="chat-log">
        {history.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="bubble">{m.text}</div>
          </div>
        ))}
        {stream && (
          <div className="msg assistant">
            <div className="bubble">{stream}<span className="caret" /></div>
          </div>
        )}
        {sending && !stream && <p className="hint">思考中…</p>}
        {tools.map((t, i) => (
          <div key={i} className="tool-row">
            <span className={`dot ${t.state === "完成" ? "ok" : "run"}`} />
            {t.name}
            <span className="hint"> {t.state}</span>
          </div>
        ))}
        {error && <p className="error">{error}</p>}
      </div>
      <div className="chat-input-row">
        <input
          className="search"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendManual()}
          placeholder="说点什么…（Enter 发送）"
        />
        <button onClick={sendManual} disabled={sending || !input.trim()}>
          {sending ? "发送中…" : "发送"}
        </button>
      </div>
    </div>
  );
}