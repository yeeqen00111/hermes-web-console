import { useCallback, useEffect, useState } from "react";

// 模型配置管理页：你的系统只管自定义模型配置（= Hermes custom endpoints）。
// 表格 + 新增/编辑表单 + 测试连接 + 设为默认 + 删除。
const EMPTY_FORM = {
  id: null,
  name: "",
  base_url: "",
  model: "",
  api_key: "",
  clearKey: false,     // 勾选 = 显式清除已存 key（api_key:""）
  api_mode: "",
  context_length: "",
  discover_models: true,
  models: "",
  make_default: false,
};

function api(path, opts = {}) {
  return fetch(path, {
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    ...opts,
  });
}

export default function ModelConfigs() {
  const [configs, setConfigs] = useState([]);
  const [current, setCurrent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState(null);   // null=列表, {} 或 config=表单
  const [form, setForm] = useState(EMPTY_FORM);
  const [validateResult, setValidateResult] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const r = await api("/api/model-configs");
      if (!r.ok) { setError(`加载失败 HTTP ${r.status}`); return; }
      const d = await r.json();
      setConfigs(d.configs ?? []);
      setCurrent(d.current ?? null);
    } catch (e) {
      setError("请求失败：" + e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function openCreate() {
    setForm(EMPTY_FORM);
    setValidateResult(null);
    setEditing({});
  }

  function openEdit(c) {
    setForm({
      id: c.id, name: c.name ?? "", base_url: c.base_url ?? "", model: c.model ?? "",
      api_key: "", clearKey: false,        // 编辑时 api_key 留空=不动
      api_mode: c.api_mode ?? "",
      context_length: c.context_length ?? "",
      discover_models: c.discover_models ?? true,
      models: (c.models ?? []).join(", "),
      make_default: false,
    });
    setValidateResult(null);
    setEditing(c);
  }

  function buildPayload() {
    const models = form.models.split(",").map((s) => s.trim()).filter(Boolean);
    const payload = {
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      model: form.model.trim(),
      api_mode: form.api_mode ?? "",
      context_length: form.context_length ? Number(form.context_length) : null,
      discover_models: !!form.discover_models,
      make_default: !!form.make_default,
    };
    if (form.id) payload.id = form.id;                       // 编辑：带上原 id
    if (form.clearKey) payload.api_key = "";                 // 显式清除
    else if (form.api_key.trim()) payload.api_key = form.api_key.trim();  // 新 key
    if (models.length) payload.models = models;
    return payload;
  }

  async function handleValidate() {
    setValidateResult(null);
    setBusy(true);
    try {
      const p = buildPayload();
      const r = await api("/api/model-configs/validate", {
        method: "POST", body: JSON.stringify({
          name: p.name, base_url: p.base_url, model: p.model,
          api_key: p.api_key, api_mode: p.api_mode,
        }),
      });
      const d = await r.json();
      setValidateResult(d);
    } catch (e) {
      setValidateResult({ ok: false, message: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function handleSave() {
    setBusy(true);
    setError("");
    try {
      const r = await api("/api/model-configs", {
        method: "POST", body: JSON.stringify(buildPayload()),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setError(`保存失败 HTTP ${r.status}: ${d.detail ?? ""}`); return; }
      setNotice(form.id ? "已保存" : `已创建（id=${d.endpoint_id}）`);
      setEditing(null);
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function handleSetDefault(c) {
    setBusy(true);
    setError(""); setNotice("");
    try {
      const r = await api(`/api/model-configs/${encodeURIComponent(c.id)}/default?model=${encodeURIComponent(c.model)}`,
        { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (d.confirm_required) { setError(`需要确认：${d.confirm_message}`); return; }
      if (!r.ok) { setError(`设默认失败 HTTP ${r.status}`); return; }
      setNotice(`已将「${c.name} / ${c.model}」设为默认`);
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(c) {
    if (!window.confirm(`确定删除「${c.name}」？（id=${c.id}）`)) return;
    setBusy(true);
    try {
      const r = await api(`/api/model-configs/${encodeURIComponent(c.id)}`, { method: "DELETE" });
      if (!r.ok) { setError(`删除失败 HTTP ${r.status}`); return; }
      setNotice(`已删除「${c.name}」`);
      await load();
    } finally {
      setBusy(false);
    }
  }

  const set = (k) => (e) =>
    setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));

  // ── 表单视图 ──
  if (editing) {
    const isEdit = !!form.id;
    return (
      <div className="page">
        <header>
          <h1>{isEdit ? "编辑模型配置" : "新增模型配置"}</h1>
          <button className="ghost" onClick={() => setEditing(null)} disabled={busy}>返回列表</button>
        </header>

        <div className="form">
          <label>名称 *<input value={form.name} onChange={set("name")} placeholder="如：火山方舟" /></label>
          <label>Base URL *<input value={form.base_url} onChange={set("base_url")} placeholder="https://…/v3" /></label>
          <label>默认模型 *<input value={form.model} onChange={set("model")} placeholder="如 deepseek-v4-flash" /></label>
          <label>
            API Key
            <input type="password" value={form.api_key} onChange={set("api_key")}
                   placeholder={editing.has_api_key ? `已存（${editing.api_key_preview}）——留空=不修改` : "未设置"} />
            {isEdit && editing.has_api_key && (
              <span className="hint">
                <input type="checkbox" checked={form.clearKey} onChange={set("clearKey")} /> 勾选=清除已存 Key
              </span>
            )}
          </label>
          <label>
            接口模式
            <select value={form.api_mode} onChange={set("api_mode")}>
              <option value="">自动</option>
              <option value="chat_completions">chat_completions</option>
              <option value="codex_responses">codex_responses</option>
              <option value="anthropic_messages">anthropic_messages</option>
            </select>
          </label>
          <label>上下文长度<input type="number" value={form.context_length ?? ""} onChange={set("context_length")} placeholder="可空" /></label>
          <label className="chk">
            <input type="checkbox" checked={form.discover_models} onChange={set("discover_models")} /> 自动发现模型
          </label>
          <label>模型清单（逗号分隔，可空）<input value={form.models} onChange={set("models")} placeholder="m1, m2, …" /></label>
          <label className="chk">
            <input type="checkbox" checked={form.make_default} onChange={set("make_default")} /> 保存后设为默认
          </label>
        </div>

        {validateResult && (
          <p className={validateResult.ok ? "ok-line" : "error"}>
            测试连接：{validateResult.ok ? "✅ 可用" : "❌ 失败"} {validateResult.message ?? ""}
            {validateResult.models?.length ? `（发现 ${validateResult.models.length} 个模型）` : ""}
          </p>
        )}
        {error && <p className="error">{error}</p>}

        <div className="form-actions">
          <button className="ghost" onClick={handleValidate} disabled={busy || !form.base_url || !form.model}>
            测试连接
          </button>
          <button onClick={handleSave} disabled={busy || !form.name || !form.base_url || !form.model}>
            {busy ? "处理中…" : "保存"}
          </button>
        </div>
      </div>
    );
  }

  // ── 列表视图 ──
  return (
    <div className="page">
      <header>
        <h1>模型配置</h1>
        {current && (
          <p className="meta">当前默认：{current.model}（{current.base_url}）</p>
        )}
        <button className="ghost" onClick={() => load()} disabled={loading}>
          {loading ? "加载中…" : "刷新"}
        </button>
      </header>
      {notice && <p className="ok-line">{notice}</p>}
      {error && <p className="error">{error}</p>}

      {!loading && configs.length === 0 ? (
        <div className="state-block">
          <p>还没有模型配置。</p>
          <p className="hint">点击右上「＋ 新增」添加第一个自定义模型端点。</p>
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th className="col-name">名称</th>
              <th>Base URL</th>
              <th className="col-num">模型</th>
              <th className="col-status">状态</th>
              <th className="col-ops">操作</th>
            </tr>
          </thead>
          <tbody>
            {configs.map((c) => (
              <tr key={c.id}>
                <td className="mono">{c.name}</td>
                <td className="mono dim">{c.base_url}</td>
                <td className="mdl">{c.model}</td>
                <td className="num">
                  {c.is_current
                    ? <span className="pill pill-on">● 使用中</span>
                    : <span className="pill">未激活</span>}
                </td>
                <td className="ops">
                  {!c.is_current && (
                    <button className="link" onClick={() => handleSetDefault(c)} disabled={busy}>设默认</button>
                  )}
                  <button className="link" onClick={() => openEdit(c)} disabled={busy}>编辑</button>
                  <button className="link danger" onClick={() => handleDelete(c)} disabled={busy}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <button className="add-btn" onClick={openCreate}>＋ 新增</button>
    </div>
  );
}