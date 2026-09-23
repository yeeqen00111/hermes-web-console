import { useCallback, useEffect, useRef, useState } from "react";

// 模型配置管理页：厂商（自定义端点）→ 模型 两级结构。
// 添加/编辑 = 弹框表单（模型ID/显示名称/最高token/思考等级）；
// 删除 = 勾选批量或行内按钮（弹框确认），底层为 SQLite 隐藏清单（config.yaml 不动，
// 规避 models_discovered 自动回写）。已删除的模型直接从列表消失。
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

const EMPTY_MODEL_VALUES = {
  name: "",             // 模型 ID（厂商 API 真名）
  display_name: "",     // 显示名称（展示用）
  context_length: "",   // 最高 token
  reasoning_effort: "", // 思考等级
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
  const [editing, setEditing] = useState(null);          // null=列表, config=编辑, {} =新增
  const [form, setForm] = useState(EMPTY_FORM);
  const [validateResult, setValidateResult] = useState(null);
  const [expanded, setExpanded] = useState({});          // {vendorSlug: bool}
  const [modelFilter, setModelFilter] = useState("");
  const [checked, setChecked] = useState(new Set());     // "vendor::model" 勾选集合
  const [modal, setModal] = useState(null);              // {type:'confirm'|'model-form', ...}
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

  // 单行删除（走 SQLite 隐藏，可恢复）
  async function handleHideModel(c, m) {
    setBusy(true);
    try {
      const r = await api(
        `/api/model-configs/${encodeURIComponent(c.id)}/models/${encodeURIComponent(m)}/hide`,
        { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { showToast("err", `删除失败：${d.detail ?? `HTTP ${r.status}`}`); return; }
      showToast("ok", `已删除：${m}`);
      await load(true);
    } catch (e) {
      showToast("err", "删除失败：" + e.message);
    } finally {
      setBusy(false);
    }
  }

  // ── 添加/编辑模型（弹框表单）──
  function askAddModel(c) {
    setModal({
      type: "model-form", mode: "add", vendor: c, old: null,
      values: { ...EMPTY_MODEL_VALUES },
    });
  }

  function askEditModel(c, m) {
    const man = (c.manual_models ?? []).find((x) => x.model === m);
    setModal({
      type: "model-form", mode: "edit", vendor: c, old: m,
      values: {
        name: m,
        display_name: man?.display_name ?? "",
        context_length: man?.context_length ?? "",
        reasoning_effort: man?.reasoning_effort ?? "",
      },
    });
  }

  async function handleModelFormConfirm() {
    const { mode, vendor, old, values } = modal;
    const body = {
      name: values.name.trim(),
      display_name: values.display_name.trim() || null,
      context_length: values.context_length ? Number(values.context_length) : null,
      reasoning_effort: values.reasoning_effort || null,
    };
    setBusy(true);
    try {
      const url = mode === "edit"
        ? `/api/model-configs/${encodeURIComponent(vendor.id)}/models/${encodeURIComponent(old)}`
        : `/api/model-configs/${encodeURIComponent(vendor.id)}/models`;
      const r = await api(url, { method: "PUT", body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { showToast("err", `保存失败：${d.detail ?? `HTTP ${r.status}`}`); return; }
      showToast("ok", mode === "edit" ? `已保存：${old} → ${body.name}` : `已添加模型：${body.name}`);
      setModal(null);
      await load(true);
    } catch (e) {
      showToast("err", "保存失败：" + e.message);
    } finally {
      setBusy(false);
    }
  }

  // ── 批量删除（勾选制）──
  const rowKey = (vendorSlug, m) => `${vendorSlug}::${m}`;

  function toggleRow(vendorSlug, m) {
    const k = rowKey(vendorSlug, m);
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

  function visibleFor(c) {
    const models = (c.models ?? []).filter((m) => !hiddenSet(c).has(m));
    if (!modelFilter) return models;
    return models.filter((m) =>
      m.toLowerCase().includes(modelFilter.toLowerCase()) ||
      String(c.name ?? "").toLowerCase().includes(modelFilter.toLowerCase()));
  }

  function hiddenSet(c) {
    return new Set(c.hidden_models ?? []);
  }

  return (
    <div className="page">
      {toast && <div className={`toast ${toast.type}`}>{toast.msg}</div>}
      {modal && (
        <div className="modal-mask" onClick={() => setModal(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            {modal.type === "model-form" ? (
              <>
                <h3>{modal.mode === "add" ? "添加模型" : "编辑模型"}</h3>
                <p className="hint">厂商：{modal.vendor.name}</p>
                <div className="modal-form">
                  <label>模型 ID *
                    <input value={modal.values.name} autoFocus
                           onChange={(e) => setModal({ ...modal, values: { ...modal.values, name: e.target.value } })}
                           placeholder="厂商 API 认的真名" />
                  </label>
                  <label>显示名称
                    <input value={modal.values.display_name}
                           onChange={(e) => setModal({ ...modal, values: { ...modal.values, display_name: e.target.value } })}
                           placeholder="列表展示用（可中文）" />
                  </label>
                  <label>最高 token
                    <input type="number" value={modal.values.context_length ?? ""}
                           onChange={(e) => setModal({ ...modal, values: { ...modal.values, context_length: e.target.value } })}
                           placeholder="可空" />
                  </label>
                  <label>思考等级
                    <select value={modal.values.reasoning_effort ?? ""}
                            onChange={(e) => setModal({ ...modal, values: { ...modal.values, reasoning_effort: e.target.value } })}>
                      <option value="">不设置</option>
                      <option value="minimal">minimal</option>
                      <option value="low">low</option>
                      <option value="medium">medium</option>
                      <option value="high">high</option>
                      <option value="xhigh">xhigh</option>
                      <option value="max">max</option>
                      <option value="ultra">ultra</option>
                    </select>
                  </label>
                </div>
                <div className="modal-actions">
                  <button className="ghost" onClick={() => setModal(null)} disabled={busy}>取消</button>
                  <button onClick={handleModelFormConfirm} disabled={busy || !modal.values.name.trim()}>
                    {busy ? "处理中…" : (modal.mode === "add" ? "添加" : "保存")}
                  </button>
                </div>
              </>
            ) : (
              <>
                <h3>{modal.title}</h3>
                <p>{modal.msg}</p>
                <div className="modal-actions">
                  <button className="ghost" onClick={() => setModal(null)}>取消</button>
                  <button className="danger-solid" onClick={modal.onConfirm} disabled={busy}>
                    {busy ? "处理中…" : modal.confirmText}
                  </button>
                </div>
              </>
            )}
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
                            <th className="col-ops">操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          {visible.map((m) => {
                            const isDefault = isDefaultModel(c, m);
                            const man = (c.manual_models ?? []).find((x) => x.model === m);
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
                                  {man?.display_name ? (
                                    <>
                                      <div>{man.display_name}</div>
                                      <div className="dim" style={{ fontSize: 11 }}>{m}</div>
                                    </>
                                  ) : (
                                    m
                                  )}
                                </td>
                                <td className="ops">
                                  {!isDefault && (
                                    <button className="link danger" onClick={() => handleHideModel(c, m)} disabled={busy}>
                                      删除
                                    </button>
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