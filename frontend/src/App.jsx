import { useCallback, useEffect, useMemo, useState } from "react";
import Chat from "./Chat.jsx";

// 前端只调你的后端（vite proxy /api → localhost:8000），不碰 Hermes，不登录。
function api(path) {
  return fetch(path);
}

function isCustomProvider(p) {
  return p.custom || String(p.slug ?? "").toLowerCase().includes("custom");
}

const FRIENDLY_ERRORS = {
  0: "连不上后端，确认 uvicorn 已在 8000 端口运行",
  404: "后端没有这个接口，检查 backend/main.py 的路径",
  502: "后端连着 Hermes 但请求失败了，看后端日志",
  503: "后端未配置 Hermes 凭据（申请 HERMES_PASS）",
};

export default function App() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [providers, setProviders] = useState([]);
  const [endpoints, setEndpoints] = useState([]);
  const [search, setSearch] = useState("");
  const [view, setView] = useState("chat");

  const loadModels = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [m, c] = await Promise.all([api("/api/models"), api("/api/custom-endpoints")]);
      if (!m.ok || !c.ok) {
        setError(FRIENDLY_ERRORS[m.status] ?? FRIENDLY_ERRORS[c.status] ?? `加载失败 ${m.status}/${c.status}`);
        return;
      }
      const models = await m.json().catch(() => ({ providers: [] }));
      const custom = await c.json().catch(() => ({ endpoints: [] }));
      setProviders(models.providers ?? []);
      setEndpoints(custom.endpoints ?? []);
    } catch (e) {
      setError((FRIENDLY_ERRORS[0] ?? "") + `（${e.message}）`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadModels();
  }, [loadModels]);

  const q = search.trim().toLowerCase();
  const matchesModel = (m) => String(m ?? "").toLowerCase().includes(q);

  const visibleProviders = useMemo(() => {
    const custom = providers.filter(isCustomProvider);
    if (!q) return custom;
    return custom.filter(
      (p) =>
        String(p.slug ?? "").toLowerCase().includes(q) ||
        (p.models ?? []).some(matchesModel)
    );
  }, [providers, q]);

  const visibleEndpoints = useMemo(() => {
    if (!q) return endpoints;
    return endpoints.filter((e) =>
      [e.name, e.model, e.base_url].some((v) => String(v ?? "").toLowerCase().includes(q))
    );
  }, [endpoints, q]);

  const totalModels = visibleProviders.reduce((n, p) => n + (p.models ?? []).length, 0);
  const hasFilter = q.length > 0;
  const nothing = !hasFilter && visibleProviders.length === 0;

  return (
    <div className="app">
      <nav className="tabs">
        <button className={view === "chat" ? "tab active" : "tab"} onClick={() => setView("chat")}>
          对话
        </button>
        <button className={view === "list" ? "tab active" : "tab"} onClick={() => setView("list")}>
          模型列表
        </button>
      </nav>
      {view === "chat" ? (
        <Chat />
      ) : (
    <div className="page">
      <header>
        <h1>自定义模型</h1>
        <p className="meta">
          {visibleProviders.length} 个来源
          {!hasFilter && ` · ${totalModels} 个模型`}
          {!hasFilter && visibleEndpoints.length > 0 ? ` · ${visibleEndpoints.length} 个端点` : ""}
        </p>
        <button onClick={() => loadModels()} className="ghost">
          {loading ? "加载中…" : "刷新"}
        </button>
      </header>

      <input
        className="search"
        type="search"
        placeholder="搜索模型、来源或端点…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      {error && <p className="error">{error}</p>}

      {!loading && nothing ? (
        <div className="state-block">
          <p>{hasFilter ? "没有匹配的模型或端点，换个关键词试试。" : "还没有自定义模型。"}</p>
          {!hasFilter && (
            <p className="hint">在后端 /api/models 或 Hermes 的自定义端点里添加后，刷新可见。</p>
          )}
        </div>
      ) : (
        <>
          {visibleProviders.length > 0 && (
            <>
              <h2>模型</h2>
              <table>
                <thead>
                  <tr>
                    <th className="col-name">来源</th>
                    <th>模型</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleProviders.flatMap((p) => {
                    const slug = p.slug ?? p.name;
                    const models = (p.models ?? []).filter(matchesModel);
                    // 一个模型一行；来源是列不是分组
                    if (models.length === 0)
                      return [
                        <tr key={slug}>
                          <td className="mono">{slug}</td>
                          <td className="dim">（无模型）</td>
                        </tr>,
                      ];
                    return models.map((m) => (
                      <tr key={`${slug}:${m}`}>
                        <td className="mono">{slug}</td>
                        <td className="mdl">{m}</td>
                      </tr>
                    ));
                  })}
                </tbody>
              </table>
            </>
          )}

          {visibleEndpoints.length > 0 && (
            <>
              <h2>自定义端点</h2>
              <table>
                <thead>
                  <tr>
                    <th className="col-name">名称</th>
                    <th>base_url</th>
                    <th className="col-num">模型</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleEndpoints.map((e, i) => (
                    <tr key={i}>
                      <td className="mono">{e.name ?? "-"}</td>
                      <td className="mono dim">{e.base_url ?? "-"}</td>
                      <td className="num">{e.model ?? "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
      </div>
      )}
    </div>
  );
}