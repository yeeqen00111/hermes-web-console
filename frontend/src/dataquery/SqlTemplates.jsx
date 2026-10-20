import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../api.js";
import Modal from "./Modal.jsx";
import { useConnectionNames } from "./useConnections.js";

// 智能问数 · 内置 SQL 模板（阶段 5，2026-10-20）
//
// 「用户这么说 → 就用这条 SQL」的沉淀。和知识库同一套形状：列表 + 弹窗 + 按数据源过滤。
// 与知识库的三点不同：
//   1. 主键是 **id**（MCP 分配的整数），所以 `question` 可以随便改。
//   2. **SQL 必须是只读语句**（SELECT / WITH 开头）——MCP 会校验，但报错没有细节，
//      所以后端先拦一层给可读的 400。
//   3. **没有「试一下」**：MCP 没给模板的检索工具（知识库才有 search_knowledge）。
//
// **没有全局模板**（2026-10-20 用户定）：每条都必须挂在一个具体数据源上，后端也拒收 `'*'`。

const EMPTY = { question: "", sql: "", name: "", connection: "", params: "{}", note: "" };

export default function SqlTemplates() {
  const names = useConnectionNames();
  const [filter, setFilter] = useState("");        // "" = 全部
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState(null);
  const [editing, setEditing] = useState(null);
  const [deleting, setDeleting] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const url = filter
        ? `/api/sql-templates?connection=${encodeURIComponent(filter)}`
        : "/api/sql-templates";
      const r = await api(url);
      if (!r.ok) { setLoadError(await errorText(r)); return; }
      setRows((await r.json()).templates || []);
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
      const r = await api(`/api/sql-templates/${row.id}`, { method: "DELETE" });
      if (!r.ok) { setNotice({ kind: "err", text: `删除失败：${await errorText(r)}` }); return; }
      setNotice({ kind: "ok", text: "已删除。" });
      await load();
    } catch (e) {
      setNotice({ kind: "err", text: "删除失败：" + e.message });
    }
  };

  const oneLine = (sql) => (sql || "").replace(/\s+/g, " ").trim();

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
        <h3>内置 SQL</h3>
        <div className="head-actions">
          <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="按数据源过滤">
            <option value="">全部数据源</option>
            {names.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <button onClick={() => { setNotice(null); setEditing({ mode: "new" }); }}>＋ 添加模板</button>
        </div>
      </div>
      <p className="meta">
        把「用户这么说 → 就用这条 SQL」沉淀下来，问数时会优先匹配。每条都属于一个数据源。
      </p>

      {notice && <div className={`notice ${notice.kind}`}>{notice.text}</div>}

      {rows.length === 0 ? (
        <p className="hint">
          还没有 SQL 模板。常被问到的问题可以固化成模板——比每次让模型现写更稳。
        </p>
      ) : (
        <table className="ds-table">
          <thead><tr><th>问题</th><th>名称</th><th>SQL</th><th>所属</th><th className="ops">操作</th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td className="ds-name">{row.question}</td>
                <td>{row.name || "—"}</td>
                <td className="ds-clip mono" title={row.sql}>{oneLine(row.sql)}</td>
                <td>{row.connection}</td>
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
        <TemplateForm mode={editing.mode} record={editing.row} names={names}
                      onClose={() => setEditing(null)}
                      onSaved={async (t) => { setEditing(null); setNotice({ kind: "ok", text: t }); await load(); }} />
      )}

      {deleting && (
        <Modal title="删除 SQL 模板" onClose={() => setDeleting(null)}
               actions={<>
                 <button onClick={() => setDeleting(null)}>取消</button>
                 <button className="danger-solid" onClick={() => remove(deleting)}>确认删除</button>
               </>}>
          <p>确定删除「<b>{deleting.question}</b>」这条模板吗？</p>
          <p className="meta">删除后问数不再匹配它，立即生效。</p>
        </Modal>
      )}
    </div>
  );
}

function TemplateForm({ mode, record, names, onClose, onSaved }) {
  const isNew = mode === "new";
  const [form, setForm] = useState(() => (isNew
    ? { ...EMPTY, connection: names[0] || "" }
    : {
      question: record.question || "", sql: record.sql || "", name: record.name || "",
      connection: record.connection || "",
      params: record.params || "{}", note: record.note || "",
    }));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const canSave = form.question.trim() !== "" && form.sql.trim() !== "" && form.connection !== "";

  const submit = async () => {
    setSaving(true);
    setError("");
    try {
      const body = JSON.stringify({
        question: form.question.trim(), sql: form.sql.trim(), name: form.name.trim(),
        connection: form.connection, params: form.params.trim() || "{}", note: form.note.trim(),
      });
      const r = isNew
        ? await api("/api/sql-templates", { method: "POST", body })
        : await api(`/api/sql-templates/${record.id}`, { method: "PUT", body });
      if (!r.ok) { setError(await errorText(r)); return; }
      onSaved(isNew ? "已添加模板。" : "已保存模板。");
    } catch (e) {
      setError("请求失败：" + e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={isNew ? "添加 SQL 模板" : "编辑 SQL 模板"} wide onClose={() => !saving && onClose()}
           actions={<>
             <button onClick={onClose} disabled={saving}>取消</button>
             <button onClick={submit} disabled={saving || !canSave}>{saving ? "保存中…" : "保存"}</button>
           </>}>
      {error && <div className="notice err">{error}</div>}
      <div className="modal-form">
        <label>
          问题（用户的说法）
          <input value={form.question} onChange={(e) => set("question", e.target.value)}
                 placeholder="例如：武汉的客户一共有多少位？" />
        </label>
        <label>
          名称
          <input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="可选，方便自己认" />
        </label>
        <label>
          所属
          <select value={form.connection} onChange={(e) => set("connection", e.target.value)}>
            <option value="" disabled>请选择数据源</option>
            {names.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          {names.length === 0 && <em className="hint">还没有数据源，先去「数据源」页添加一个。</em>}
        </label>
        <label>
          参数说明
          <input value={form.params} onChange={(e) => set("params", e.target.value)}
                 placeholder='{"year": "年份"}' />
        </label>
        <label className="span2">
          SQL（必须只读）
          <textarea className="mono" rows={6} value={form.sql} onChange={(e) => set("sql", e.target.value)}
                    placeholder="SELECT ... / WITH ... SELECT ..." />
          <em className="hint">只允许 SELECT / WITH 开头的只读查询。</em>
        </label>
        <label className="span2">
          备注
          <input value={form.note} onChange={(e) => set("note", e.target.value)} placeholder="可选" />
        </label>
      </div>
    </Modal>
  );
}
