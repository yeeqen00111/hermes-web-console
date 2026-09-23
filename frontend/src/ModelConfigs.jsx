import { useCallback, useEffect, useRef, useState } from "react";

// 模型配置管理页：厂商（自定义端点）→ 模型 两级结构。
// 删除 = 勾选（支持全选）+ 批量删除，底层为 SQLite 隐藏清单（config.yaml 不动，
// 规避 models_discovered 自动回写）。已删除的模型直接从列表消失。
// 模型条目字段：模型ID（厂商真名）/ 显示名称（展示用）/ 最高token / 思考等级。
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
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [validateResult, setValidateResult] = useState(null);
  const [expanded, setExpanded] = useState({});
  const [modelFilter, setModelFilter] = useState("");
  const [checked, setChecked] = useState(new Set());   // "vendor::model" 勾选集合
  const [renaming, setRenaming] = useState(null);      // {vendorId, old, value}
  const [modal, setModal] = useState(null);            // {title, msg, confirmText, onConfirm}
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);

  const showToast = useCallback((type, msg) => {
    setToast({ type, msg });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), type === "ok" ? 3000 : 6000);
  }, []);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    try {
      const r = await api(`/api/model-configs${refresh ? "?refresh=true" : ""}`);
      if (!r.ok) { showToast("err", `加载失败 HTTP ${r.status}`); return; }
      const d = await r.json();
      setConfigs(d.configs ?? []);
      setCurrent(d.current ?? null);
    } catch (e) {
      showToast("err", "请求失败：" + e.message);
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => { load(); }, [load]);

  const isDefaultModel = (c, m) => !!c.is_current && !!current && current.model === m;
  const currentVendor = configs.find((c) => c.is_current);

  function toggleExpand(id) {
    setExpanded((e) => ({ ...e, [id]: !e[id] }));
    setModelFilter("");
  }

  function openCreate() {
    setForm(EMPTY_FORM);
    setValidateResult(null);
    setEditing({});
  }

  function openEdit(c) {
    setForm({
      id: c.manage_id,
      name: c.name ?? "", base_url: c.base_url ?? "", model: c.model ?? "",
      api_key: "", clearKey: false,
      api_mode: c.api_mode ?? "",
      context_length: c.context_length ?? "",
      discover_models: c.discover_models ?? true,
      make_default: false,
    });
    setValidateResult(null);
    setEditing(c);
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
    try {
      const r = await api("/api/model-configs", {
        method: "POST", body: JSON.stringify(buildPayload()),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { showToast("err", `保存失败 HTTP ${r.status}: ${d.detail ?? ""}`); return; }
      showToast("ok", form.id ? "已保存" : `已创建（id=${d.endpoint_id}）`);
      setEditing(null);
      await load(true);
    } finally {
      setBusy(false);
    }
  }

  async function handleSwitchModel(c, m) {
    if (isDefaultModel(c, m)) return;
    setBusy(true);
    try {
      const r = await api(
        `/api/model-configs/${encodeURIComponent(c.id)}/default?model=${encodeURIComponent(m)}`,
        { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (d.confirm_required) { showToast("err", `需要确认：${d.confirm_message}`); return; }
      if (!r.ok) { showToast("err", `切换失败 HTTP ${r.status}`); return; }
      showToast("ok", `默认模型已切换：${m}`);
      await load(true);
    } catch (e) {
      showToast("err", "切换失败：" + e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteVendor(c) {
    if (!window.confirm(`确定删除厂商「${c.name}」？`)) return;
    setBusy(true);
    try {
      const r = await api(`/api/model-configs/${encodeURIComponent(c.id)}`, { method: "DELETE" });
      if (!r.ok) { showToast("err", `删除失败 HTTP ${r.status}`); return; }
      showToast("ok", `已删除厂商「${c.name}」`);
      await load(true);
    } finally {
      setBusy(false);
    }
  }

  // ── 行内改名（编辑模型 ID）──
  const [renaming, setRenaming] = useState(null);   // {vendorId, old, value}
  const renameInput = useRef(null);

  async function handleRenameConfirm(c) {
    const nn = (renaming?.value ?? "").trim();
    if (!nn || nn === renaming.old || busy) { setRenaming(null); return; }
    setBusy(true);
    try {
      const r = await api(
        `/api/model-configs/${encodeURIComponent(c.id)}/models/${encodeURIComponent(renaming.old)}`,
        { method: "PUT", body: JSON.stringify({ name: nn }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { showToast("err", `重命名失败：${d.detail ?? `HTTP ${r.status}`}`); return; }
      showToast("ok", `已重命名：${renaming.old} → ${nn}`);
      setRenaming(null);
      await load(true);
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
        {toast && <div className={`toast ${toast.type}`}>{toast.msg}</div>}
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

        {validateResult && (
          <p className={validateResult.ok ? "ok-line" : "error"}>
            测试连接：{validateResult.ok ? "✅ 可用" : "❌ 失败"} {validateResult.message ?? ""}
            {validateResult.models?.length ? `（发现 ${validateResult.models.length} 个模型）` : ""}
          </p>
        )}

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
  const filter = modelFilter.trim().toLowerCase();

  const visibleFor = (c) => {
    const models = (c.models ?? []).filter((m) => !hiddenSet(c).has(m));
    if (!filter) return models;
    return models.filter((m) =>
      m.toLowerCase().includes(filter) ||
      String(c.name ?? "").toLowerCase().includes(filter));
  };

  function hiddenSet(c) {
    return new Set(c.hidden_models ?? []);
  }

  function toggleRow(vendorSlug, m) {
    const k = `${vendorSlug}::${m}`;
    setChecked((prev) => {
      const next = new Set(prev);
      next.has(k) ? next.delete(k) : next.add(k);
      return next;
    });
  }

  function toggleAllVendor(c, visible) {
    const keys = visible.map((m) => `${c.id}::${m}`);
    const allOn = keys.every((k) => checked.has(k));
    setChecked((prev) => {
      const next = new Set(prev);
      keys.forEach((k) => (allOn ? next.delete(k) : next.add(k)));
      return next;
    });
  }

  async function handleBatchDelete() {
    const items = [...checked].map((k) => {
      const [vendorId, ...rest] = k.split("::");
      return { vendorId, model: rest.join("::") };
    });
    if (items.length === 0) return;
    setBusy(true);
    let okCount = 0;
    const fails = [];
    try {
      const byVendor = {};
      items.forEach(({ vendorId, model }) => {
        (byVendor[vendorId] = byVendor[vendorId] || []).push(model);
      });
      for (const [vendorId, models] of Object.entries(byVendor)) {
        const r = await api(
          `/api/model-configs/${encodeURIComponent(vendorId)}/models/hide-batch`,
          { method: "POST", body: JSON.stringify({ models }) });
        if (r.ok) okCount += models.length;
        else fails.push(vendorId);
      }
      setModal(null);
      if (fails.length) showToast("err", `部分删除失败（${fails.join(", ")}）`);
      else showToast("ok", `已删除 ${okCount} 个模型`);
      setChecked(new Set());
      await load(true);
    } catch (e) {
      showToast("err", "批量删除失败：" + e.message);
    } finally {
      setBusy(false);
    }
  }

  const totalVisible = configs.reduce((n, c) => n + visibleFor(c).length, 0);
  const checkedCount = checked.size;

  return (
    <div className="page">
      {toast && <div className={`toast ${toast.type}`}>{toast.msg}</div>}
      {modal && (
        <div className="modal-mask" onClick={() => setModal(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>{modal.title}</h3>
            <p>{modal.msg}</p>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setModal(null)}>取消</button>
              <button className="danger-solid" onClick={modal.onConfirm} disabled={busy}>
                {busy ? "处理中…" : modal.confirmText}
              </button>
            </div>
          </div>
        </div>
      )}
      <header>
        <h1>模型配置</h1>
        <p className="meta">
          {current?.model
            ? `当前默认：${current.model}${currentVendor ? `（${currentVendor.name}）` : ""}`
            : "未设置默认模型"}
        </p>
        <button className="ghost" onClick={() => load(true)} disabled={loading}>
          {loading ? "加载中…" : "刷新"}
        </button>
      </header>

      <input
        className="search"
        type="search"
        placeholder="搜索模型…"
        value={modelFilter}
        onChange={(e) => setModelFilter(e.target.value)}
      />

      {checkedCount > 0 && (
        <button
          className="ghost batch-del"
          onClick={() => setModal({
            title: "删除所选模型",
            msg: `确定删除所选的 ${checkedCount} 个模型吗？`,
            confirmText: `删除所选（${checkedCount}）`,
            onConfirm: handleBatchDelete,
          })}
          disabled={busy}
        >
          删除所选（{checkedCount}）
        </button>
      )}

      {loading && <p className="hint">加载中…</p>}

      {!loading && totalVisible === 0 ? (
        <div className="state-block">
          <p>没有模型。</p>
          <p className="hint">点「＋ 新增模型配置」添加第一个厂商端点。</p>
        </div>
      ) : (
        <div className="vendors">
          {configs.map((c) => {
            const open = !!expanded[c.id];
            const visible = visibleFor(c);
            const allChecked = visible.length > 0 && visible.every((m) => checked.has(`${c.id}::${m}`));
            const someChecked = visible.some((m) => checked.has(`${c.id}::${m}`));
            return (
              <div className="vendor" key={c.id}>
                <div className="vendor-row" onClick={() => toggleExpand(c.id)}>
                  <input
                    type="checkbox"
                    className="row-check"
                    checked={allChecked}
                    ref={(el) => { if (el) el.indeterminate = someChecked && !allChecked; }}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => toggleAllVendor(c, visible)}
                  />
                  <span className={`arrow ${open ? "open" : ""}`}>▸</span>
                  <span className="mono v-name">{c.name}</span>
                  <span className="mono dim v-url">{c.base_url}</span>
                  {c.is_current && current && (
                    <span className="pill pill-on">● {current.model}</span>
                  )}
                  <span className="v-ops" onClick={(e) => e.stopPropagation()}>
                    <button className="link" onClick={() => openEdit(c)} disabled={busy}>编辑</button>
                    <button className="link danger" onClick={() => handleDeleteVendor(c)} disabled={busy}>删除</button>
                  </span>
                </div>

                {open && (
                  <div className="vendor-models" onClick={(e) => e.stopPropagation()}>
                    {visible.length === 0 ? (
                      <p className="hint">
                        该厂商还没有模型清单——「编辑」里手填，或保存后用「测试连接」自动发现。
                      </p>
                    ) : (
                      <table className="vm-table">
                        <thead>
                          <tr>
                            <th className="col-check">
                              <input
                                type="checkbox"
                                checked={allChecked}
                                ref={(el) => { if (el) el.indeterminate = someChecked && !allChecked; }}
                                onChange={() => toggleAllVendor(c, visible)}
                              />
                            </th>
                            <th>模型</th>
                          </tr>
                        </thead>
                        <tbody>
                          {visible.map((m) => {
                            const isDefault = isDefaultModel(c, m);
                            const isRenaming = renaming && renaming.vendorId === c.id && renaming.old === m;
                            return (
                              <tr key={m}>
                                <td className="col-check">
                                  <input
                                    type="checkbox"
                                    checked={checked.has(`${c.id}::${m}`)}
                                    onChange={() => toggleRow(c.id, m)}
                                  />
                                </td>
                                <td className="mono">
                                  {isRenaming ? (
                                    <input
                                      className="cell-input"
                                      value={renaming.value}
                                      autoFocus
                                      onChange={(e) => setRenaming({ ...renaming, value: e.target.value })}
                                      onKeyDown={(e) => {
                                        if (e.key === "Enter") handleRenameConfirm(c);
                                        if (e.key === "Escape") setRenaming(null);
                                      }}
                                    />
                                  ) : (
                                    m
                                  )}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}