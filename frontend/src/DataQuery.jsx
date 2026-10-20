import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "./api.js";
import DataSources from "./dataquery/DataSources.jsx";

// 智能问数页：两个子 tab。
//   ① 数据源    —— 见 ./dataquery/DataSources.jsx（**2026-10-20 起走后端 MCP**：多库 CRUD。
//      后端是 hermes-mcp 的 MCP 客户端，控制台不再直读共享 SQLite）
//   ② MCP 配置  —— hermes-mcp 是独立服务，用 HTTP 注册在 Hermes 上（config.yaml 的
//      mcp_servers.<name>.url）。这页只**代理** Hermes dashboard 的 /api/mcp/*：
//      列出来、添加、删除、启停、测试（是 Hermes 去连）。
//      没有"改地址"——上游没有改单条的接口，硬做只能先删后建；要换地址就删了重加。
//
// 页面侧边界：
//   - 凭据只在后端：密码永远不回显，只给「已配置 / 未配置」；token 连后端都拿不到（上游只返摘要）。
//   - 路径里只有占位符，没有写死的服务名（教训：/api/channels/feishu/restart）。

// 能被问数端调用的工具里，这两个是**写入口**（新增/删除连接），不该出现在对话里
const WRITE_TOOLS = ["register_connection", "remove_connection"];
const QUERY_TOOLS = ["ask", "query", "get_schema_map", "describe_table", "list_schemas", "list_connections", "ping"];

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
  { id: "datasources", label: "数据源", Component: DataSources },
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
