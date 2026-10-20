import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../api.js";
import Modal from "./Modal.jsx";

// 智能问数 · 数据源（2026-10-20 改造）
//
// 后端已改走 MCP（hermes-mcp 是数据的唯一主人），所以这一页比原来多了三件事：
//   ① 从「一条」变成「多条」——表格 + 弹窗增删改
//   ② 类型从 4 种扩到 9 种（含 sqlite），**且类型决定填哪些字段**
//      → 类型清单不再写死在前端，而是从 `GET /api/db-connections/types` 拉（前后端共用一份）
//   ③ 数据源不再有 id：MCP 用 name 定位，所以**编辑态名称只读**（改名 = 删了重建）
//
// 三个刻意的"不做"：
//   - **不做「测试连接」**（用户 2026-10-20 定）：控制台不连数据库
//   - **不显示 status / last_error**：MCP 侧目前恒为 null（`set_status` 无调用点），是死列
//   - **不做改名**：MCP 按 name upsert，"改名"实际是删旧的+建新的、中途失败会留空档

const MAX_ROWS_CAP = 1000;
const DEFAULT_MAX_ROWS = 200;

const EMPTY_FORM = {
  name: "", type: "", host: "", port: "", database: "",
  username: "", max_rows: DEFAULT_MAX_ROWS,
};

/** 「连接目标」列：关系库显示 `主机:端口/库名`，sqlite 显示文件路径。 */
function targetOf(row) {
  if (row.type === "sqlite") return row.database || "—";
  const port = row.port ? `:${row.port}` : "";
  const db = row.database ? `/${row.database}` : "";
  return `${row.host || "—"}${port}${db}`;
}

export default function DataSources() {
  const [catalog, setCatalog] = useState({ types: [], field_labels: {} });
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState(null);      // {kind:'ok'|'err', text}
  const [editing, setEditing] = useState(null);    // null | {mode:'new'} | {mode:'edit', row}
  const [deleting, setDeleting] = useState(null);  // row | null

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [rTypes, rList] = await Promise.all([
        api("/api/db-connections/types"),
        api("/api/db-connections"),
      ]);
      if (!rTypes.ok) { setLoadError(await errorText(rTypes)); return; }
      if (!rList.ok) { setLoadError(await errorText(rList)); return; }
      setCatalog(await rTypes.json());
      setRows((await rList.json()).connections || []);
    } catch (e) {
      setLoadError("请求失败：" + e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const remove = async (row) => {
    setDeleting(null);
    setNotice(null);
    try {
      const r = await api(`/api/db-connections/${encodeURIComponent(row.name)}`, { method: "DELETE" });
      if (!r.ok) { setNotice({ kind: "err", text: `删除失败：${await errorText(r)}` }); return; }
      setNotice({ kind: "ok", text: `已删除「${row.name}」。` });
      await load();
    } catch (e) {
      setNotice({ kind: "err", text: "删除失败：" + e.message });
    }
  };

  const labelOf = (id) => catalog.types.find((t) => t.id === id)?.label || id || "—";

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
        <h3>数据源</h3>
        <button onClick={() => { setNotice(null); setEditing({ mode: "new" }); }}>＋ 添加数据源</button>
      </div>
      <p className="meta">
        这里只写配置，真正连库的是问数服务。请使用<b>只有 SELECT 权限</b>的账号——
        页面无法校验权限，只读靠数据库侧保证。
      </p>

      {notice && <div className={`notice ${notice.kind}`}>{notice.text}</div>}

      {rows.length === 0 ? (
        <p className="hint">还没有数据源。点右上角「＋ 添加数据源」——加完之后，对话里就能直接问它。</p>
      ) : (
        <table className="ds-table">
          <thead>
            <tr><th>名称</th><th>类型</th><th>连接目标</th><th className="ops">操作</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.name}>
                <td className="ds-name">{row.name}</td>
                <td>{labelOf(row.type)}</td>
                <td className="ds-target" title={targetOf(row)}>{targetOf(row)}</td>
                <td className="ops">
                  <button className="ghost" onClick={() => { setNotice(null); setEditing({ mode: "edit", row }); }}>
                    编辑
                  </button>
                  <button className="ghost danger" onClick={() => { setNotice(null); setDeleting(row); }}>
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editing && (
        <ConnForm
          mode={editing.mode}
          record={editing.row}
          catalog={catalog}
          onClose={() => setEditing(null)}
          onSaved={async (text) => { setEditing(null); setNotice({ kind: "ok", text }); await load(); }}
        />
      )}

      {deleting && (
        <Modal
          title="删除数据源"
          onClose={() => setDeleting(null)}
          actions={<>
            <button onClick={() => setDeleting(null)}>取消</button>
            <button className="danger-solid" onClick={() => remove(deleting)}>确认删除</button>
          </>}
        >
          <p>确定删除「<b className="mono">{deleting.name}</b>」吗？</p>
          <p className="meta">
            删除后对话里将无法再查询它，<b>立即生效且不可恢复</b>（问数服务侧没有回收站）。
          </p>
        </Modal>
      )}
    </div>
  );
}

// ─────────────────────────── 新增 / 编辑弹窗 ───────────────────────────

function ConnForm({ mode, record, catalog, onClose, onSaved }) {
  const isNew = mode === "new";
  const [form, setForm] = useState(() => isNew
    ? { ...EMPTY_FORM, type: catalog.types[0]?.id || "" }
    : {
      name: record.name, type: record.type,
      host: record.host || "", port: record.port ?? "",
      database: record.database || "", username: record.username || "",
      max_rows: record.max_rows ?? DEFAULT_MAX_ROWS,
    });
  // 密码三态：没动 → 不发字段；勾了清除 → 空串；填了 → 新值
  const [password, setPassword] = useState("");
  const [pwTouched, setPwTouched] = useState(false);
  const [clearPw, setClearPw] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const meta = catalog.types.find((t) => t.id === form.type);
  const isFile = meta?.group === "file";
  const hintOf = (k) => meta?.hints?.[k];

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const onTypeChange = (id) => {
    const next = catalog.types.find((t) => t.id === id);
    setForm((f) => ({
      ...f,
      type: id,
      port: next?.default_port ?? "",
      // 换类型时清掉不适用的字段，免得把上一次填的带过去
      ...(next?.group === "file" ? { host: "", port: "", username: "" } : {}),
    }));
  };

  const canSave =
    form.name.trim() !== "" &&
    form.database.trim() !== "" &&
    (isFile || (form.host.trim() !== "" && form.username.trim() !== "")) &&
    (!isNew || isFile || password !== "");

  const submit = async () => {
    setSaving(true);
    setError("");
    const body = {
      type: form.type,
      database: form.database.trim(),
      max_rows: Number(form.max_rows) || DEFAULT_MAX_ROWS,
    };
    if (!isFile) {
      body.host = form.host.trim();
      body.port = form.port === "" ? null : Number(form.port);   // 空 = 让后端按类型补默认端口
      body.username = form.username.trim();
    }
    if (clearPw) body.password = "";
    else if (pwTouched) body.password = password;

    try {
      const r = isNew
        ? await api("/api/db-connections", {
          method: "POST",
          body: JSON.stringify({ ...body, name: form.name.trim() }),
        })
        : await api(`/api/db-connections/${encodeURIComponent(record.name)}`, {
          method: "PUT",
          body: JSON.stringify(body),
        });
      if (!r.ok) { setError(await errorText(r)); return; }
      onSaved(isNew ? `已添加「${form.name.trim()}」。` : `已保存「${record.name}」。`);
    } catch (e) {
      setError("请求失败：" + e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={isNew ? "添加数据源" : `编辑数据源：${record.name}`}
      wide
      onClose={() => !saving && onClose()}
      actions={<>
        <button onClick={onClose} disabled={saving}>取消</button>
        <button onClick={submit} disabled={saving || !canSave}>{saving ? "保存中…" : "保存"}</button>
      </>}
    >
      {error && <div className="notice err">{error}</div>}

      <div className="modal-form">
        <label>
          名称
          <input value={form.name} disabled={!isNew} onChange={(e) => set("name", e.target.value)}
                 placeholder="例如：订单库" />
        </label>
        <label>
          类型
          <select value={form.type} onChange={(e) => onTypeChange(e.target.value)}>
            {catalog.types.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
        </label>

        {isFile ? (
          <label className="span2">
            文件路径
            <input value={form.database} onChange={(e) => set("database", e.target.value)}
                   placeholder="D:/data/xxx.db" />
            <em className="hint">{hintOf("database")}</em>
          </label>
        ) : (
          <>
            <label>
              主机
              <input value={form.host} onChange={(e) => set("host", e.target.value)}
                     placeholder="IP 或主机名" />
            </label>
            <label>
              端口
              <input type="number" value={form.port} onChange={(e) => set("port", e.target.value)}
                     placeholder={String(meta?.default_port ?? "")} />
            </label>
            <label>
              库名
              <input value={form.database} onChange={(e) => set("database", e.target.value)} />
              <em className="hint">{hintOf("database")}</em>
            </label>
            <label>
              账号
              <input value={form.username} onChange={(e) => set("username", e.target.value)}
                     placeholder="只有 SELECT 权限的账号" />
            </label>
            <label className="span2">
              密码
              <input type="password" value={password} disabled={clearPw}
                     onChange={(e) => { setPassword(e.target.value); setPwTouched(true); }}
                     placeholder={isNew ? "必填"
                       : record.has_password ? "留空 = 不修改（不会清空）" : "尚未设置"} />
              <em className="hint">
                已保存的密码<b>永不回显</b>。{!isNew && "留空表示保持不变。"}
              </em>
            </label>
            {!isNew && record.has_password && (
              <label className="chk span2">
                <input type="checkbox" checked={clearPw}
                       onChange={(e) => setClearPw(e.target.checked)} />
                清除已保存的密码
              </label>
            )}
          </>
        )}

        <label>
          行数上限
          <input type="number" min="1" max={MAX_ROWS_CAP} value={form.max_rows}
                 onChange={(e) => set("max_rows", e.target.value)} />
          <em className="hint">单次查询最多返回 1–{MAX_ROWS_CAP} 行</em>
        </label>
      </div>

      {!isNew && (
        <p className="hint">
          名称是这条数据源的身份，改不了——问数服务按名称定位。要改名请删掉重建。
        </p>
      )}
    </Modal>
  );
}
