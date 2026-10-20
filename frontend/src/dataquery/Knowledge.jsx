import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../api.js";
import Modal from "./Modal.jsx";
import { GLOBAL, useConnectionNames } from "./useConnections.js";

// 智能问数 · 知识库（阶段 5，2026-10-20）
//
// 这里存的是**业务术语与口径**（"活跃客户"到底指什么）。hermes-mcp 在 `ask` 时会把
// 命中的口径**注入到 NL→SQL 的提示里**，所以改这里 = 改所有人问出来的数字含义。
//
// 两个语义要点：
//   - `connection = '*'` 是**全局**，对所有数据源生效（新建时的默认值）
//   - 过滤器选某个数据源时，后端返回的是**该数据源的 + 全局的**（MCP 的 list 就是
//     `connection IN (x, '*')`），所以过滤器只需要「全部 / 各数据源」，没有单独的"全局"
//
// 「试一下」用的是 MCP 的 search_knowledge（FTS5 trigram 全文检索）——
// **这是验证一条口径有没有生效的唯一手段**，别省掉。

const EMPTY = { term: "", definition: "", connection: GLOBAL, anchor: "", note: "" };

export default function Knowledge() {
  const names = useConnectionNames();
  const [filter, setFilter] = useState("");        // "" = 全部
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState(null);
  const [editing, setEditing] = useState(null);    // null | {mode:'new'} | {mode:'edit', row}
  const [deleting, setDeleting] = useState(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState(null);          // null = 还没试过

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const url = filter
        ? `/api/knowledge-entries?connection=${encodeURIComponent(filter)}`
        : "/api/knowledge-entries";
      const r = await api(url);
      if (!r.ok) { setLoadError(await errorText(r)); return; }
      setRows((await r.json()).entries || []);
    } catch (e) {
      setLoadError("请求失败：" + e.message);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  const remove = async (row) => {
    setDeleting(null);
    setNotice(null);
    try {
      const r = await api(`/api/knowledge-entries/${row.id}`, { method: "DELETE" });
      if (!r.ok) { setNotice({ kind: "err", text: `删除失败：${await errorText(r)}` }); return; }
      setNotice({ kind: "ok", text: `已删除「${row.term}」。` });
      await load();
    } catch (e) {
      setNotice({ kind: "err", text: "删除失败：" + e.message });
    }
  };

  const trySearch = async () => {
    setHits(null);
    setNotice(null);
    try {
      const url = `/api/knowledge-entries/search?query=${encodeURIComponent(query.trim())}`
        + (filter ? `&connection=${encodeURIComponent(filter)}` : "");
      const r = await api(url);
      if (!r.ok) { setNotice({ kind: "err", text: "检索失败：" + (await errorText(r)) }); return; }
      setHits((await r.json()).hits || []);
    } catch (e) {
      setNotice({ kind: "err", text: "检索失败：" + e.message });
    }
  };

  const owner = (c) => (c === GLOBAL ? "全局" : c);

  if (loading) return <p className="hint">加载中…</p>;
  if (loadError) {
    return (
      <div className="state-block">
        <p>{loadError}</p>
        <button className="ghost" onClick={load}>重试</button>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3>知识库</h3>
        <div className="head-actions">
          <select value={filter} onChange={(e) => { setFilter(e.target.value); setHits(null); }}
                  aria-label="按数据源过滤">
            <option value="">全部数据源</option>
            {names.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <button onClick={() => { setNotice(null); setEditing({ mode: "new" }); }}>＋ 添加术语</button>
        </div>
      </div>
      <p className="meta">
        存的是<b>业务术语和口径</b>——问数时这些定义会被注入到生成 SQL 的提示里。
        选某个数据源时，列表会连<b>全局</b>条目一起显示。
      </p>

      {notice && <div className={`notice ${notice.kind}`}>{notice.text}</div>}

      <div className="try-box">
        <input value={query} onChange={(e) => setQuery(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") trySearch(); }}
               placeholder="试一下：输入一个问题，看会命中哪些口径" disabled={!filter} />
        <button className="ghost" onClick={trySearch} disabled={!filter || !query.trim()}>检索</button>
        {!filter && <span className="hint">先在上面选一个数据源（全局条目只在这里生效）</span>}
      </div>

      {hits !== null && (
        <div className="try-result">
          {hits.length === 0
            ? <p className="hint">没有命中任何口径。</p>
            : <ul>{hits.map((h) => (
              <li key={h.id}><b>{h.term}</b> — {h.definition}
                {h.connection === GLOBAL && <span className="chip mark">全局</span>}</li>
            ))}</ul>}
        </div>
      )}

      {rows.length === 0 ? (
        <p className="hint">
          还没有知识条目。把用户嘴里的「黑话」定义清楚（比如"活跃客户"到底是多久没下单），
          问数的准确率会明显提高。
        </p>
      ) : (
        <table className="ds-table">
          <thead><tr><th>术语</th><th>定义 / 口径</th><th>所属</th><th className="ops">操作</th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td className="ds-name">{row.term}</td>
                <td className="ds-clip" title={row.definition}>{row.definition}</td>
                <td>{row.connection === GLOBAL
                  ? <span className="chip mark">全局</span>
                  : owner(row.connection)}</td>
                <td className="ops">
                  <button className="ghost" onClick={() => { setNotice(null); setEditing({ mode: "edit", row }); }}>编辑</button>
                  <button className="ghost danger" onClick={() => { setNotice(null); setDeleting(row); }}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editing && (
        <KnowledgeForm mode={editing.mode} record={editing.row} names={names}
                       onClose={() => setEditing(null)}
                       onSaved={async (t) => { setEditing(null); setNotice({ kind: "ok", text: t }); await load(); }} />
      )}

      {deleting && (
        <Modal title="删除知识条目" onClose={() => setDeleting(null)}
               actions={<>
                 <button onClick={() => setDeleting(null)}>取消</button>
                 <button className="danger-solid" onClick={() => remove(deleting)}>确认删除</button>
               </>}>
          <p>确定删除「<b className="mono">{deleting.term}</b>」吗？</p>
          <p className="meta">删除后问数将不再使用这条口径，立即生效。</p>
        </Modal>
      )}
    </div>
  );
}

function KnowledgeForm({ mode, record, names, onClose, onSaved }) {
  const isNew = mode === "new";
  const [form, setForm] = useState(() => (isNew ? { ...EMPTY } : {
    term: record.term || "", definition: record.definition || "",
    connection: record.connection || GLOBAL,
    anchor: record.anchor || "", note: record.note || "",
  }));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const canSave = form.term.trim() !== "" && form.definition.trim() !== "";

  const submit = async () => {
    setSaving(true);
    setError("");
    try {
      const body = JSON.stringify({
        term: form.term.trim(), definition: form.definition.trim(),
        connection: form.connection, anchor: form.anchor.trim(), note: form.note.trim(),
      });
      const r = isNew
        ? await api("/api/knowledge-entries", { method: "POST", body })
        : await api(`/api/knowledge-entries/${record.id}`, { method: "PUT", body });
      if (!r.ok) { setError(await errorText(r)); return; }
      onSaved(isNew ? `已添加「${form.term.trim()}」。` : `已保存「${form.term.trim()}」。`);
    } catch (e) {
      setError("请求失败：" + e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={isNew ? "添加知识条目" : "编辑知识条目"} wide onClose={() => !saving && onClose()}
           actions={<>
             <button onClick={onClose} disabled={saving}>取消</button>
             <button onClick={submit} disabled={saving || !canSave}>{saving ? "保存中…" : "保存"}</button>
           </>}>
      {error && <div className="notice err">{error}</div>}
      <div className="modal-form">
        <label>
          术语
          <input value={form.term} onChange={(e) => set("term", e.target.value)} placeholder="例如：活跃客户" />
        </label>
        <label>
          所属
          <select value={form.connection} onChange={(e) => set("connection", e.target.value)}>
            <option value={GLOBAL}>全局（对所有数据源生效）</option>
            {names.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label className="span2">
          定义 / 口径
          <textarea rows={3} value={form.definition} onChange={(e) => set("definition", e.target.value)}
                    placeholder="例如：近 90 天内至少下过 1 单且没有退款的客户" />
        </label>
        <label>
          出处
          <input value={form.anchor} onChange={(e) => set("anchor", e.target.value)}
                 placeholder="表名 / 文档链接（可选）" />
        </label>
        <label>
          备注
          <input value={form.note} onChange={(e) => set("note", e.target.value)} placeholder="可选" />
        </label>
      </div>
    </Modal>
  );
}
