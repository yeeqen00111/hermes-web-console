import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "./api.js";

// 智能问数页（2026-10-12）：两个子 tab。
//   ① 数据库配置 —— **只管写配置**：把一条连接写进与 hermes-mcp 共享的 SQLite 注册表。
//      本页不连数据库、不测连通（那是问数端的事），所以没有「测试」按钮。
//   ② MCP 配置   —— hermes-mcp 是独立服务，用 HTTP 注册在 Hermes 上（config.yaml 的
//      mcp_servers.<name>.url）。这页只**代理** Hermes dashboard 的 /api/mcp/*：
//      列出来、添加、删除、启停、测试（是 Hermes 去连）。
//      没有"改地址"——上游没有改单条的接口，硬做只能先删后建；要换地址就删了重加。
//
// 两条边界（页面侧）：
//   - 凭据只在后端：密码永远不回显，只给「已配置 / 未配置」；token 连后端都拿不到（上游只返摘要）。
//   - 路径里只有占位符，没有写死的服务名（教训：/api/channels/feishu/restart）。
//
// 一期一个库：后端返回的是数组形状（以后放开多库不用改契约），这里只取第一条。
const TYPE_OPTIONS = [
  { id: "mysql", label: "MySQL", port: 3306, hint: "库名填 schema 名" },
  { id: "postgresql", label: "PostgreSQL", port: 5432, hint: "库名填 database" },
  { id: "sqlserver", label: "SQL Server", port: 1433, hint: "库名填 database" },
  { id: "oracle", label: "Oracle", port: 1521, hint: "库名填服务名（service_name）" },
];

const MAX_ROWS_CAP = 1000;   // 与后端一致
const DEFAULT_MAX_ROWS = 200;

// 能被问数端调用的工具里，这两个是**写入口**（新增/删除连接），不该出现在对话里
const WRITE_TOOLS = ["register_connection", "remove_connection"];
const QUERY_TOOLS = ["ask", "query", "get_schema_map", "describe_table", "list_schemas", "list_connections", "ping"];

const EMPTY_FORM = {
  name: "", type: "mysql", host: "", port: 3306,
  database: "", username: "", max_rows: DEFAULT_MAX_ROWS,
};

// ─────────────────────────── 数据库配置 ───────────────────────────

function Databases() {
  const [record, setRecord] = useState(null);   // 已保存的那条（null = 还没配过）
  const [form, setForm] = useState(EMPTY_FORM);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [password, setPassword] = useState("");   // 只装「本次输入的新密码」
  const [clearPw, setClearPw] = useState(false);  // 是否要清除已保存的密码
  const [pwTouched, setPwTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);     // {kind:'ok'|'err', text}

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const r = await api("/api/db-connections");
      if (!r.ok) { setLoadError(await errorText(r)); return; }
      const data = await r.json();
      const row = (data.connections || [])[0] || null;
      setRecord(row);
      setForm(row ? {
        name: row.name, type: row.type, host: row.host,
        port: row.port ?? TYPE_OPTIONS.find((t) => t.id === row.type)?.port ?? 3306,
        database: row.database, username: row.username,
        max_rows: row.max_rows ?? DEFAULT_MAX_ROWS,
      } : EMPTY_FORM);
      // 密码不回显：留空 = 不改
      setPassword("");
      setClearPw(false);
      setPwTouched(false);
    } catch (e) {
      setLoadError("请求失败：" + e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const typeMeta = TYPE_OPTIONS.find((t) => t.id === form.type);
  const hasPassword = !!record?.has_password;

  const requiredFilled = form.name.trim() && form.host.trim() && form.database.trim() && form.username.trim();
  const dirty =
    !record ||
    pwTouched || clearPw ||
    form.name !== record.name || form.type !== record.type || form.host !== record.host ||
    Number(form.port) !== record.port || form.database !== record.database ||
    form.username !== record.username || Number(form.max_rows) !== record.max_rows;

  const onTypeChange = (id) => {
    const next = TYPE_OPTIONS.find((t) => t.id === id);
    setForm((f) => ({ ...f, type: id, port: next ? next.port : f.port }));
  };

  const save = async () => {
    setSaving(true);
    setNotice(null);
    try {
      const body = {
        name: form.name.trim(), type: form.type, host: form.host.trim(),
        port: Number(form.port), database: form.database.trim(),
        username: form.username.trim(), max_rows: Number(form.max_rows),
      };
      // 密码三态：没动 → 不发字段；清除 → 空串；填了 → 新值
      if (clearPw) body.password = "";
      else if (pwTouched) body.password = password;

      const r = await api("/api/db-connections", { method: "PUT", body: JSON.stringify(body) });
      if (!r.ok) { setNotice({ kind: "err", text: "保存失败：" + (await errorText(r)) }); return; }
      setNotice({
        kind: "ok",
        text: "已保存。对话里问数时用的就是这条配置——它和问数服务共用同一份注册表。",
      });
      await load();
    } catch (e) {
      setNotice({ kind: "err", text: "保存失败：" + e.message });
    } finally {
      setSaving(false);
    }
  };

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
      <h3>{record ? "编辑数据源" : "添加数据源"}</h3>
      <p className="meta">
        这里只写配置：保存后配置就进了共享注册表，真正连库的是问数服务。
        请使用<b>只有 SELECT 权限</b>的账号——页面无法校验权限，只读靠数据库侧保证。
      </p>

      {notice && <div className={`notice ${notice.kind}`}>{notice.text}</div>}

      {record?.status && (
        <p className="meta">
          问数端最近一次使用结果：{record.status === "ok" ? "正常" : `失败（${record.last_error || "无详情"}）`}
        </p>
      )}

      <div className="field-row">
        <div className="field-label"><span className="field-name">名称 <em className="req">*</em></span></div>
        <div className="field-edit">
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                 placeholder="如：订单库（对话里会用这个名字指代这个库）" />
        </div>
        <div className="field-hint">建议把「数据内容」也写进名字，例如「电商订单库」——对话里选库只看得到名字。</div>
      </div>

      <div className="field-row">
        <div className="field-label"><span className="field-name">类型 <em className="req">*</em></span></div>
        <div className="field-edit">
          <select value={form.type} onChange={(e) => onTypeChange(e.target.value)}>
            {TYPE_OPTIONS.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>
        </div>
        <div className="field-hint">{typeMeta?.hint}</div>
      </div>

      <div className="field-row">
        <div className="field-label"><span className="field-name">主机 <em className="req">*</em></span></div>
        <div className="field-edit">
          <input value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })}
                 placeholder="IP 或主机名（问数服务要能访问到它）" />
        </div>
        <div className="field-hint">填的是<b>问数服务</b>所在网络里能访问到的主机——不是你本机能连的地址。</div>
      </div>

      <div className="field-row">
        <div className="field-label"><span className="field-name">端口 <em className="req">*</em></span></div>
        <div className="field-edit">
          <input type="number" min="1" max="65535" value={form.port}
                 onChange={(e) => setForm({ ...form, port: e.target.value })} />
        </div>
        <div className="field-hint">选类型时已带默认值，按需改。</div>
      </div>

      <div className="field-row">
        <div className="field-label"><span className="field-name">库名 <em className="req">*</em></span></div>
        <div className="field-edit">
          <input value={form.database} onChange={(e) => setForm({ ...form, database: e.target.value })}
                 placeholder={form.type === "oracle" ? "服务名（service_name）" : "数据库名"} />
        </div>
        <div className="field-hint">{typeMeta?.hint}</div>
      </div>

      <div className="field-row">
        <div className="field-label"><span className="field-name">账号 <em className="req">*</em></span></div>
        <div className="field-edit">
          <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })}
                 placeholder="只有 SELECT 权限的账号" />
        </div>
      </div>

      <div className={`field-row${clearPw ? " clearing" : ""}`}>
        <div className="field-label">
          <span className="field-name">密码</span>
          <span className={`chip ${hasPassword ? "on" : "off"}`}>{hasPassword ? "已配置" : "未配置"}</span>
        </div>
        <div className="field-edit">
          <input
            type="password"
            value={clearPw ? "" : password}
            disabled={clearPw}
            placeholder={clearPw ? "将清除（保存后删除）"
              : hasPassword ? "已配置 · 填这里可覆盖" : (record ? "未配置" : "首次保存必填")}
            onChange={(e) => { setClearPw(false); setPwTouched(true); setPassword(e.target.value); }}
          />
          <button type="button" className={`ghost tiny ${clearPw ? "" : "danger-soft"}`}
                  disabled={!hasPassword && !pwTouched}
                  onClick={() => { setClearPw((v) => !v); setPassword(""); }}>
            {clearPw ? "撤销" : "✕ 清除"}
          </button>
        </div>
        <div className="field-hint">
          密码存进共享注册表时是加密的（与问数服务同一把密钥），<b>页面和接口都不会回显</b>。
        </div>
      </div>

      <div className="field-row">
        <div className="field-label"><span className="field-name">行数上限</span></div>
        <div className="field-edit">
          <input type="number" min="1" max={MAX_ROWS_CAP} value={form.max_rows}
                 onChange={(e) => setForm({ ...form, max_rows: e.target.value })} />
        </div>
        <div className="field-hint">单次查询最多返回多少行（1–{MAX_ROWS_CAP}，默认 {DEFAULT_MAX_ROWS}）。</div>
      </div>

      <div className="field-row">
        <div className="field-label"><span className="field-name">只读</span></div>
        <div className="field-edit">
          <span className="chip on">强制只读</span>
        </div>
        <div className="field-hint">本页只配置只读连接，不提供改写开关。</div>
      </div>

      <div className="channel-actions">
        <button className="ghost" onClick={load} disabled={saving}>刷新</button>
        <button onClick={save} disabled={saving || !dirty || !requiredFilled}>
          {saving ? "保存中…" : "保存"}
        </button>
      </div>
      <p className="footnote">
        {record ? "改名会替换掉原来那条" : "保存后会新增一条"}；一期只支持<b>一个</b>数据源。
        改完立即生效——问数服务每次调用都直接读这份注册表。
      </p>
    </div>
  );
}

// ─────────────────────────── MCP 配置 ───────────────────────────

function McpServers() {
  const [data, setData] = useState(null);     // {servers, configured_name, configured_found}
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [testing, setTesting] = useState("");  // 正在测的服务名
  const [results, setResults] = useState({});  // name → {ok,error,tools}
  const [busy, setBusy] = useState("");        // 正在启停/删除的服务名
  const [notice, setNotice] = useState(null);
  const [form, setForm] = useState(null);      // {name, url} | null（只有"添加"）
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const r = await api("/api/mcp-servers");
      if (!r.ok) { setLoadError(await errorText(r)); return; }
      setData(await r.json());
    } catch (e) {
      setLoadError("请求失败：" + e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const runTest = async (name) => {
    setTesting(name);
    setNotice(null);
    try {
      const r = await api(`/api/mcp-servers/${encodeURIComponent(name)}/test`, { method: "POST" });
      // 先 await 出结果，再进 setState 回调（回调里不能 await）
      const payload = r.ok
        ? await r.json()
        : { ok: false, error: await errorText(r), tools: [] };
      setResults((s) => ({ ...s, [name]: payload }));
    } catch (e) {
      setResults((s) => ({ ...s, [name]: { ok: false, error: "请求失败：" + e.message, tools: [] } }));
    } finally {
      setTesting("");
    }
  };

  const toggle = async (name, enabled) => {
    setBusy(name);
    setNotice(null);
    try {
      const r = await api(`/api/mcp-servers/${encodeURIComponent(name)}/enabled`, {
        method: "PUT", body: JSON.stringify({ enabled }),
      });
      if (!r.ok) { setNotice({ kind: "err", text: "操作失败：" + (await errorText(r)) }); return; }
      setNotice({ kind: "ok", text: `已${enabled ? "启用" : "停用"}「${name}」。新会话/重连后生效。` });
      await load();
    } catch (e) {
      setNotice({ kind: "err", text: "操作失败：" + e.message });
    } finally {
      setBusy("");
    }
  };

  const remove = async (name) => {
    if (!window.confirm(`确定从 Hermes 删除「${name}」这条 MCP 注册吗？\n（对话里会立刻失去它的工具，且不可撤销。）`)) return;
    setBusy(name);
    setNotice(null);
    try {
      const r = await api(`/api/mcp-servers/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (!r.ok) { setNotice({ kind: "err", text: "删除失败：" + (await errorText(r)) }); return; }
      setNotice({ kind: "ok", text: `已删除「${name}」。` });
      await load();
    } catch (e) {
      setNotice({ kind: "err", text: "删除失败：" + e.message });
    } finally {
      setBusy("");
    }
  };

  // 只做「添加」：没有改地址。上游没有"改某一条"的接口，硬做只能先删后建，
  // 中途失败会留下空档；要换地址就在这一行删掉再添加。
  const openAdd = () => setForm({ name: "", url: "" });

  const submitForm = async () => {
    const name = form.name.trim();
    const url = form.url.trim();
    if (!name || !url) return;
    setSaving(true);
    setNotice(null);
    try {
      const r = await api("/api/mcp-servers", { method: "POST", body: JSON.stringify({ name, url }) });
      if (!r.ok) { setNotice({ kind: "err", text: "添加失败：" + (await errorText(r)) }); return; }
      setForm(null);
      setNotice({ kind: "ok", text: `已注册「${name}」。新会话/重连后生效。` });
      await load();
    } catch (e) {
      setNotice({ kind: "err", text: "添加失败：" + e.message });
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <p className="hint">加载中…</p>;
  if (loadError) {
    return (
      <div className="state-block">
        <p>{loadError}</p>
        <button className="ghost" onClick={load}>重试</button>
      </div>
    );
  }

  const servers = data?.servers || [];

  return (
    <div className="card">
      {form && (
        <div className="modal-mask" onClick={() => !saving && setForm(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>添加 MCP 服务</h3>
            <p className="hint">
              填 Hermes 能访问到的地址（Hermes 在容器里就跑不通常见的宿主机 IP，要用容器能解析的名字）。
            </p>
            <div className="modal-form">
              <label>名称 *
                <input value={form.name} autoFocus
                       placeholder="字母/数字/下划线/连字符，如 hermes-mcp"
                       onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </label>
              <label>地址 *
                <input value={form.url}
                       placeholder="http://hermes-mcp:9123/mcp"
                       onChange={(e) => setForm({ ...form, url: e.target.value })} />
              </label>
            </div>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setForm(null)} disabled={saving}>取消</button>
              <button onClick={submitForm} disabled={saving || !form.name.trim() || !form.url.trim()}>
                {saving ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}

      <h3>问数服务（MCP）</h3>
      <p className="meta">
        问数服务是<b>独立服务</b>，通过 HTTP 注册在 Hermes 上（配置在 Hermes 的 config.yaml）。
        本页只读改这条注册——不启动、不停止那个服务，也拿不到它的凭据。
      </p>

      <div className="channel-actions" style={{ justifyContent: "flex-start", marginTop: 0 }}>
        <button className="ghost" onClick={openAdd} disabled={saving}>＋ 添加 MCP 服务</button>
        <button className="ghost" onClick={load} disabled={loading}>刷新</button>
      </div>

      {!data?.configured_found && (
        <div className="notice err">
          没找到名为 <span className="mono">{data?.configured_name}</span> 的 MCP 服务。
          可能是名字改了（改后请在后端 .env 里同步 MCP_SERVER_NAME），或者还没在 Hermes 上注册。
        </div>
      )}
      {notice && <div className={`notice ${notice.kind}`}>{notice.text}</div>}

      {servers.length === 0 && <p className="hint">Hermes 上还没有注册任何 MCP 服务。</p>}

      {servers.map((s) => {
        const isQuery = s.name === data?.configured_name;
        const res = results[s.name];
        const tools = (res?.tools || []).map((t) => (typeof t === "string" ? { name: t } : t));
        const bad = tools.filter((t) => WRITE_TOOLS.includes(t.name));
        const missing = isQuery && res?.ok ? QUERY_TOOLS.filter((q) => !tools.some((t) => t.name === q)) : [];
        return (
          <div key={s.name} className="field-row" style={{ gridTemplateColumns: "1fr" }}>
            <div className="mcp-head">
              <span className="field-name mono">{s.name}</span>
              {isQuery && <span className="chip mark">问数</span>}
              <span className="chip off">{s.transport === "http" ? "HTTP" : "stdio"}</span>
              <span className={`chip ${s.enabled === false ? "off" : "on"}`}>
                {s.enabled === false ? "已停用" : "启用中"}
              </span>
              <span className="chip off">{s.auth ? `鉴权：${s.auth}` : "无鉴权"}</span>
              <span style={{ flex: 1 }} />
              <button className="ghost tiny" onClick={() => runTest(s.name)} disabled={!!testing}>
                {testing === s.name ? "测试中…" : "测试"}
              </button>
              <button className="ghost tiny danger-soft" onClick={() => remove(s.name)} disabled={!!busy || saving}>
                删除
              </button>
              <button type="button" role="switch" aria-checked={s.enabled !== false}
                      aria-label={`${s.enabled === false ? "启用" : "停用"} ${s.name}`}
                      className={`switch${s.enabled === false ? "" : " on"}`}
                      disabled={!!busy}
                      onClick={() => toggle(s.name, s.enabled === false)}>
                <span className="knob" />
              </button>
            </div>

            {s.transport === "http"
              ? <p className="meta mono">{s.url || "（未填地址）"}</p>
              : <p className="meta mono">{s.command} {(s.args || []).join(" ")}</p>}

            {res && (
              <div className={`notice ${res.ok ? "ok" : "err"}`} style={{ marginBottom: 0 }}>
                {res.ok
                  ? `连接正常，共 ${tools.length} 个工具`
                  : `连不上：${res.error || "服务未响应"}`}
              </div>
            )}
            {res?.ok && tools.length > 0 && (
              <ul className="mcp-tools">
                {tools.map((t) => (
                  <li key={t.name} className={WRITE_TOOLS.includes(t.name) ? "danger" : ""}
                      title={t.description || ""}>
                    {t.name}{WRITE_TOOLS.includes(t.name) ? "（可写配置，应移除）" : ""}
                  </li>
                ))}
              </ul>
            )}
            {bad.length > 0 && (
              <p className="field-hint" style={{ gridColumn: "auto", color: "#a4271d" }}>
                ⚠️ 该服务把 <span className="mono">{bad.map((t) => t.name).join("、")}</span> 暴露给了对话——
                这意味着在聊天里就能新增/删除数据源。建议只在受控环境使用。
              </p>
            )}
            {missing.length > 0 && (
              <p className="field-hint" style={{ gridColumn: "auto" }}>
                提示：没看到 {missing.join("、")}，问数可能不完整（该服务版本较旧？）。
              </p>
            )}
            {isQuery && !res && (
              <p className="field-hint" style={{ gridColumn: "auto" }}>
                点「测试」验证 Hermes 能否连上它、并查看它的工具清单。
              </p>
            )}
          </div>
        );
      })}

      <p className="footnote">
        测试是让 <b>Hermes</b> 去连一次问数服务（不是本页去连），能同时验证网络、Host 白名单与鉴权。
        添加/删除都改的是 Hermes 里那条注册，<b>新会话或重连后才生效</b>；
        要换地址就删掉再添加（上游没有"改某一条"的接口）。
        mcp 服务本身在哪儿跑、由谁拉起，不归这一页管。
      </p>
    </div>
  );
}

// ─────────────────────────── 外壳 ───────────────────────────

const TABS = [
  { id: "databases", label: "数据库配置", Component: Databases },
  { id: "mcp", label: "MCP 配置", Component: McpServers },
];

export default function DataQuery() {
  const [tab, setTab] = useState(TABS[0].id);
  const current = TABS.find((t) => t.id === tab) || TABS[0];
  const Current = current.Component;

  return (
    <div className="page dataquery">
      <header>
        <h1>智能问数</h1>
        <p className="meta">
          让对话能直接问数据库：先配好数据源，再把问数服务注册进 Hermes。
        </p>
      </header>

      <nav className="sub-tabs" aria-label="问数配置分类">
        {TABS.map((t) => (
          <button key={t.id} type="button" className={`sub-tab${t.id === tab ? " active" : ""}`}
                  aria-pressed={t.id === tab} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </nav>

      <Current />
    </div>
  );
}
