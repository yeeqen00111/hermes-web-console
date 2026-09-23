import { useCallback, useEffect, useState } from "react";

// 模型配置管理页：厂商（自定义端点）→ 模型 两级结构。
// 交互原则：浏览（chip 点击=选中）与操作（显式「设为默认」按钮）分离，防误触。
// 模型清单：已有模型只读（upsert 合并语义只增不删，界面不放假删除）；新增走 tag 输入。
const EMPTY_FORM = {
  id: null,
  name: "",
  base_url: "",
  model: "",
  api_key: "",
  clearKey: false,
  api_mode: "",
  context_length: "",
  discover_models: true,
  newModels: [],
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
  const [editing, setEditing] = useState(null);   // null=列表, config=编辑, {} =新增
  const [form, setForm] = useState(EMPTY_FORM);
  const [modelInput, setModelInput] = useState("");
  const [validateResult, setValidateResult] = useState(null);
  const [expanded, setExpanded] = useState({});   // {endpoint_id: bool}
  const [selected, setSelected] = useState(null); // {vendorId, model} 本地选中态，未生效
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

  // 当前默认模型判定：厂商级用 options 自带的 is_current；模型级用 current.model 精确匹配。
  // 注意 current.provider 是裸 'custom'，与厂商 slug 不相等——不能用 provider===id 判定（踩过坑）。
  const isDefaultModel = (c, m) => !!c.is_current && !!current && current.model === m;
  const currentVendor = configs.find((c) => c.is_current);

  function toggleExpand(id) {
    setExpanded((e) => ({ ...e, [id]: !e[id] }));
    setSelected(null);   // 收起/切换厂商时清掉未生效的选中态
  }

  function selectModel(c, m) {
    if (isDefaultModel(c, m)) return;    // 已是默认，无需选择
    setSelected({ vendorId: c.id, model: m });
  }

  function openCreate() {
    setForm(EMPTY_FORM);
    setModelInput("");
    setValidateResult(null);
    setEditing({});
  }

  function openEdit(c) {
    setForm({
      id: c.manage_id,                        // 编辑/删除走 custom-endpoints 体系，用它的 id
      name: c.name ?? "", base_url: c.base_url ?? "", model: c.model ?? "",
      api_key: "", clearKey: false,
      api_mode: c.api_mode ?? "",
      context_length: c.context_length ?? "",
      discover_models: c.discover_models ?? true,
      newModels: [],
      make_default: false,
    });
    setModelInput("");
    setValidateResult(null);
    setEditing(c);
  }

  function addModelTag() {
    const m = modelInput.trim();
    if (!m) return;
    setForm((f) => ({
      ...f,
      newModels: f.newModels.includes(m) ? f.newModels : [...f.newModels, m],
    }));
    setModelInput("");
  }

  function buildPayload() {
    const payload = {
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      model: form.model.trim(),
      api_mode: form.api_mode ?? "",
      context_length: form.context_length ? Number(form.context_length) : null,
      discover_models: !!form.discover_models,
      make_default: !!form.make_default,
    };
    if (form.id) payload.id = form.id;
    if (form.clearKey) payload.api_key = "";
    else if (form.api_key.trim()) payload.api_key = form.api_key.trim();
    if (form.newModels.length) payload.models = form.newModels;
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
      setValidateResult(await r.json());
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

  // 显式按钮触发：切换全局默认模型
  async function handleSwitchModel() {
    if (!selected) return;
    setBusy(true);
    setError(""); setNotice("");
    try {
      const r = await api(
        `/api/model-configs/${encodeURIComponent(selected.vendorId)}/default?model=${encodeURIComponent(selected.model)}`,
        { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (d.confirm_required) { setError(`需要确认：${d.confirm_message}`); return; }
      if (!r.ok) { setError(`切换失败 HTTP ${r.status}`); return; }
      setNotice(`默认模型已切换：${selected.model}`);
      setSelected(null);
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(c) {
    if (!c.manage_id) return;
    if (!window.confirm(`确定删除「${c.name}」？（id=${c.manage_id}）`)) return;
    setBusy(true);
    try {
      const r = await api(`/api/model-configs/${encodeURIComponent(c.manage_id)}`, { method: "DELETE" });
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
          <label>端点默认模型 *<input value={form.model} onChange={set("model")} placeholder="如 deepseek-v4-flash" /></label>
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
          <label className="chk">
            <input type="checkbox" checked={form.make_default} onChange={set("make_default")} /> 保存后设为默认
          </label>
        </div>

        {/* 模型清单：已有=只读（接口合并语义只增不删，不放假删除）；新增=tag 输入 */}
        <div className="models-editor">
          {isEdit && (editing.models ?? []).length > 0 && (
            <>
              <p className="hint">已有模型（只读——接口限制，删除需修改服务器配置文件）：</p>
              <ul className="chips readonly">
                {(editing.models ?? []).map((m) => <li key={m} className="chip">{m}</li>)}
              </ul>
            </>
          )}
          <p className="hint">新增模型（输入后回车添加，可连加多个）：</p>
          <input
            className="search"
            value={modelInput}
            onChange={(e) => setModelInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addModelTag(); } }}
            placeholder="输入模型名，回车添加"
          />
          {form.newModels.length > 0 && (
            <ul className="chips">
              {form.newModels.map((m) => (
                <li key={m} className="chip tag-edit">
                  {m}
                  <button className="tag-x" onClick={() => setForm((f) => ({ ...f, newModels: f.newModels.filter((x) => x !== m) }))}>×</button>
                </li>
              ))}
            </ul>
          )}
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
        <p className="hint">说明：端点默认模型是该厂商的标识模型；切换全局默认在列表页选中模型后点「设为默认」。</p>
      </div>
    );
  }

  // ── 列表视图（厂商 → 模型 两级）──
  return (
    <div className="page">
      <header>
        <h1>模型配置</h1>
        <p className="meta">
          {current?.model
            ? `当前默认：${current.model}${currentVendor ? `（${currentVendor.name}）` : ""}`
            : "未设置默认模型"}
        </p>
        <button className="ghost" onClick={() => load()} disabled={loading}>
          {loading ? "加载中…" : "刷新"}
        </button>
      </header>
      {notice && <p className="ok-line">{notice}</p>}
      {error && <p className="error">{error}</p>}

      {!loading && configs.length === 0 ? (
        <div className="state-block">
          <p>还没有模型配置。</p>
          <p className="hint">点击右上「＋ 新增厂商端点」。</p>
        </div>
      ) : (
        <div className="vendors">
          {configs.map((c) => {
            const open = !!expanded[c.id];
            const models = c.models ?? [];
            const sel = selected && selected.vendorId === c.id ? selected : null;
            return (
              <div className="vendor" key={c.id}>
                <div className="vendor-row" onClick={() => toggleExpand(c.id)}>
                  <span className={`arrow ${open ? "open" : ""}`}>▸</span>
                  <span className="mono v-name">{c.name}</span>
                  <span className="mono dim v-url">{c.base_url}</span>
                  <span className="pill">
                    {c.is_current
                      ? <span className="pill pill-on">● 使用中</span>
                      : <span className="pill">未激活</span>}
                  </span>
                  <span className="v-ops" onClick={(e) => e.stopPropagation()}>
                    <button className="link" onClick={() => openEdit(c)} disabled={busy || !c.manage_id}
                            title={c.manage_id ? "" : "该厂商在 legacy 配置段，暂不支持界面编辑"}>
                      编辑
                    </button>
                    <button className="link danger" onClick={() => handleDelete(c)}
                            disabled={busy || !c.manage_id}
                            title={c.manage_id ? "" : "该厂商在 legacy 配置段，暂不支持界面删除"}>
                      删除
                    </button>
                  </span>
                </div>

                {open && (
                  <div className="vendor-models" onClick={(e) => e.stopPropagation()}>
                    {models.length === 0 ? (
                      <p className="hint">
                        该厂商还没有模型清单——「编辑」里手填，或保存后用「测试连接」自动发现。
                      </p>
                    ) : (
                      <>
                        <ul className="chips selectable">
                          {models.map((m) => {
                            const isSelected = sel?.model === m;
                            return (
                              <li
                                key={m}
                                className={[
                                  "chip",
                                  isDefaultModel(c, m) ? "default" : "",
                                  isSelected ? "selected" : "",
                                ].join(" ").trim()}
                                onClick={() => selectModel(c, m)}
                                title={isDefaultModel(c, m) ? "当前默认" : "点击选中"}
                              >
                                <span className="chip-name">{m}</span>
                                {isDefaultModel(c, m) && <span className="chip-tag">默认</span>}
                                {isSelected && (
                                  <span className="chip-actions" onClick={(e) => e.stopPropagation()}>
                                    <button
                                      className="chip-btn primary"
                                      onClick={() => handleSwitchModel()}
                                      disabled={busy}
                                    >
                                      设为默认
                                    </button>
                                    <button className="chip-btn" onClick={() => setSelected(null)}>×</button>
                                  </span>
                                )}
                              </li>
                            );
                          })}
                        </ul>
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <button className="add-btn" onClick={openCreate}>＋ 新增厂商端点</button>
      <p className="hint">切换说明：选中模型后点「设为默认」= 修改全局默认模型（影响之后的新会话）；对话中的热切换在「对话」页顶栏（待上线）。</p>
    </div>
  );
}