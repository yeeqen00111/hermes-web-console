import { useCallback, useEffect, useState } from "react";

// 消息渠道页 —— 飞书（2026-09-28 定案）：只管理「正在跑的那份网关」的飞书配置
// （后端 /api/channels 无 profile 维度，单网关模式）。
//
// 密钥/字段三态语义（与后端及 Hermes PUT 对齐）：
//   - 没动   → 保存时不发该键（保持原样）
//   - 输入   → 进 env 覆盖保存；但留空 = 空串，Hermes 会跳过（不生效）——要清除必须点「✕ 清除」
//   - 点 ✕   → 进 clear_env 真清除（可再点撤销）
// 密钥字段只显示「是否已配置」，不回显原文（上游只回 redacted_value）。
// 保存后单容器模式需在服务器执行 docker restart hermes 才生效（后端返回 hot_served 指示）

const FEISHU_FIELDS = [
  { key: "FEISHU_APP_ID", label: "App ID", required: true, secret: false,
    hint: "飞书开放平台应用的 App ID（cli_ 开头）" },
  { key: "FEISHU_APP_SECRET", label: "App Secret", required: true, secret: true,
    hint: "飞书开放平台应用的 App Secret" },
  { key: "FEISHU_ENCRYPT_KEY", label: "加密密钥", required: false, secret: true,
    hint: "事件订阅的加密密钥（Encrypt Key，订阅事件时一般需要）" },
  { key: "FEISHU_VERIFICATION_TOKEN", label: "验证令牌", required: false, secret: true,
    hint: "事件订阅的验证令牌（Verification Token）" },
  { key: "FEISHU_DOMAIN", label: "域名", required: false, secret: false,
    hint: "开放平台域名，默认 https://open.feishu.cn" },
  { key: "FEISHU_ALLOWED_USERS", label: "允许用户", required: false, secret: false,
    hint: "逗号分隔的允许使用本机器人的飞书用户，留空 = 不限制" },
];

const STATE_BADGES = {
  connected: { label: "已连接", cls: "ok" },
  disabled: { label: "已停用", cls: "off" },
  not_configured: { label: "未配置", cls: "off" },
  pending_restart: { label: "待重启生效", cls: "warn" },
  gateway_stopped: { label: "网关未运行", cls: "err" },
  startup_failed: { label: "网关启动失败", cls: "err" },
};

function api(path, opts = {}) {
  return fetch(path, {
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    ...opts,
  });
}

async function errorText(r) {
  try {
    const d = await r.json();
    return d?.detail || `HTTP ${r.status}`;
  } catch {
    return `HTTP ${r.status}`;
  }
}

export default function Channels() {
  const [card, setCard] = useState(null);          // 后端返回的 feishu 平台卡
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [draft, setDraft] = useState({});          // key → 输入值（存在键 = 已修改，保存进 env）
  const [clearSet, setClearSet] = useState(new Set()); // key → 要清除（进 clear_env）
  const [enabled, setEnabled] = useState(null);    // null=未改开关；true/false=保存时写
  const [showSecret, setShowSecret] = useState("");    // 哪个密钥框切明文（key 为 '' 表示全隐藏）
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);      // {kind:'ok'|'err', text} 保存结果提示
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState(null);          // {ok, message} 检查状态结果
  const [restarting, setRestarting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const r = await api("/api/channels/feishu");
      if (!r.ok) { setLoadError(await errorText(r)); return; }
      const data = await r.json();
      setCard(data);
      setDraft({});
      setClearSet(new Set());
      setEnabled(null);
      setShowSecret("");
    } catch (e) {
      setLoadError("请求失败：" + e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const envMeta = (key) => (card?.env_vars || []).find((e) => e.key === key);

  const toggleClear = (key) => {
    setClearSet((s) => {
      const n = new Set(s);
      if (n.has(key)) n.delete(key); else n.add(key);
      return n;
    });
    setDraft((d) => { const nd = { ...d }; delete nd[key]; return nd; });
  };

  const dirty = Object.keys(draft).length > 0 || clearSet.size > 0 || enabled !== null;

  const save = async () => {
    setSaving(true);
    setNotice(null);
    try {
      const body = {};
      if (Object.keys(draft).length) body.env = draft;
      if (clearSet.size) body.clear_env = [...clearSet];
      if (enabled !== null) body.enabled = enabled;
      const r = await api("/api/channels/feishu", { method: "PUT", body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) {
        setNotice({ kind: "err", text: "保存失败：" + (await errorText(r)) });
        return;
      }
      setNotice({
        kind: "ok",
        text: d.hot_served
          ? `已保存。`
          : `已保存。点「重启网关生效」加载新配置（或服务器执行 docker restart hermes）。`,
      });
      await load();
    } catch (e) {
      setNotice({ kind: "err", text: "保存失败：" + e.message });
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      const r = await api("/api/channels/feishu/test", { method: "POST" });
      const d = await r.json();
      if (!r.ok) { setTest({ ok: false, message: await errorText(r) }); return; }
      setTest({ ok: !!d.ok, message: d.message || (d.ok ? "连接正常" : "状态未就绪") });
    } catch (e) {
      setTest({ ok: false, message: "请求失败：" + e.message });
    } finally {
      setTesting(false);
    }
  };

  const restartGateway = async () => {
    const okay = window.confirm(
      "重启网关会让 feishu / weixin / api_server 等渠道短暂断线（约半分钟到一分钟），正在进行的会话会被中断。确定重启吗？"
    );
    if (!okay) return;
    setRestarting(true);
    setNotice(null);
    setTest(null);
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const readCard = async () => {
      try {
        const c = await api("/api/channels/feishu");
        if (!c.ok) return null;
        return await c.json();
      } catch {
        return null;
      }
    };
    try {
      const r = await api("/api/channels/feishu/restart", { method: "POST" });
      if (!r.ok) { setNotice({ kind: "err", text: "重启请求失败：" + (await errorText(r)) }); return; }
      setNotice({ kind: "ok", text: "重启指令已下发，等待网关掉线后再恢复…" });

      // 阶段一：先观察到掉线（gateway_running=false 或 state≠connected），证明真重启。
      // 只认 card（messaging 源），不认 /api/status 缓存。超时约 60s。
      let dropped = false;
      for (let t = 0; t < 20; t++) {
        await sleep(3000);
        const card = await readCard();
        if (card && (!card.gateway_running || card.state !== "connected")) {
          dropped = true;
          setNotice({ kind: "ok", text: "网关已掉线，等待恢复…" });
          break;
        }
      }
      setCard(await readCard() || undefined);
      // 阶段二：等到 connected 才算成功。超时约 120s → 服务器 docker restart 兜底。
      for (let t = 0; t < 40; t++) {
        await sleep(3000);
        const card = await readCard();
        if (card && card.gateway_running && card.state === "connected") {
          setCard(card);
          setNotice({
            kind: "ok",
            text: dropped ? "网关已重启，飞书渠道已连接。" : "网关已在运行，飞书渠道已连接（未观察到掉线）。",
          });
          return;
        }
        if (card) setCard(card); // 顺带刷新展示状态
      }
      setNotice({
        kind: "err",
        text: dropped
          ? "网关掉线后未恢复——请在服务器执行 docker restart hermes。"
          : "未观察到网关重启（可能被冷却合并），配置是否生效不确定；如需生效请在服务器执行 docker restart hermes。",
      });
    } catch (e) {
      setNotice({ kind: "err", text: "重启失败：" + e.message });
    } finally {
      setRestarting(false);
    }
  };

  const current = (enabled ?? card?.enabled);
  const badge = STATE_BADGES[card?.state] || { label: card?.state || "未知", cls: "off" };

  return (
    <div className="page channels">
      <header>
        <h1>消息渠道</h1>
        <p className="meta">飞书 / Lark · 正在运行的网关上的飞书配置（单网关模式，无需选择 profile）。</p>
      </header>

      {loading ? (
        <p className="hint">加载中…</p>
      ) : !card ? (
        <div className="state-block">
          <p>{loadError || "无法加载飞书渠道信息。"}</p>
          <button className="ghost" onClick={load}>重试</button>
        </div>
      ) : (
        <div className="channel-card">
          {card.docs_url && (
            <p className="meta">
              <a href={card.docs_url} target="_blank" rel="noreferrer">飞书开放平台接入文档 ↗</a>
            </p>
          )}

          <div className="channel-head">
            <span className={`status-badge ${badge.cls}`}>{badge.label}</span>
            {card.gateway_running ? (
              <span className="meta">网关运行中</span>
            ) : (
              <span className="meta">网关未运行</span>
            )}
            <button className="ghost" onClick={runTest} disabled={testing}>
              {testing ? "检查中…" : "检查状态"}
            </button>
          </div>
          {test && (
            <div className={`test-result ${test.ok ? "ok" : "err"}`}>
              {test.ok ? "✓ " : "✗ "}{test.message}
            </div>
          )}

          {notice && (
            <div className={`channels-notice ${notice.kind}`}>{notice.text}</div>
          )}

          <div className="field-row toggle-row">
            <span className="field-name">启用飞书渠道</span>
            <span className={`chip ${current ? "on" : "off"}`}>{current ? "启用中" : "已停用"}</span>
            <button
              type="button"
              role="switch"
              aria-checked={!!current}
              className={`switch${current ? " on" : ""}`}
              onClick={() => setEnabled(enabled === null ? !card.enabled : !enabled)}
            >
              <span className="knob" />
            </button>
            {enabled !== null && <span className="field-hint">开关已改，保存后生效</span>}
          </div>

          {FEISHU_FIELDS.map((f) => {
            const meta = envMeta(f.key);
            const isSet = !!meta?.is_set;
            const cleared = clearSet.has(f.key);
            const edited = draft[f.key] !== undefined;
            const shown = cleared ? "" : (edited ? draft[f.key] : "");
            return (
              <div className={`field-row${cleared ? " clearing" : ""}`} key={f.key}>
                <div className="field-label">
                  <span className="field-name">
                    {f.label} {f.required && <em className="req">*</em>}
                  </span>
                  <span className={`chip ${isSet ? "on" : "off"}`}>{isSet ? "已配置" : "未配置"}</span>
                </div>
                <div className="field-edit">
                  <input
                    type={f.secret && showSecret !== f.key ? "password" : "text"}
                    value={shown}
                    disabled={cleared}
                    placeholder={
                      cleared ? "将清除（保存后删除）"
                        : isSet ? "已配置 · 修改这里可覆盖"
                          : f.required ? "必填" : "可选"
                    }
                    onChange={(e) => {
                      setClearSet((s) => { const n = new Set(s); n.delete(f.key); return n; });
                      setDraft((d) => ({ ...d, [f.key]: e.target.value }));
                    }}
                  />
                  {f.secret && !cleared && (
                    <button
                      type="button"
                      className="ghost tiny"
                      onClick={() => setShowSecret(showSecret === f.key ? "" : f.key)}
                    >
                      {showSecret === f.key ? "隐藏" : "显示"}
                    </button>
                  )}
                  <button
                    type="button"
                    className={`ghost tiny ${cleared ? "" : "danger-soft"}`}
                    disabled={!isSet && !edited && !cleared}
                    onClick={() => toggleClear(f.key)}
                  >
                    {cleared ? "撤销" : "✕ 清除"}
                  </button>
                </div>
                <div className="field-hint">
                  {f.hint}
                  {!cleared && isSet && edited && draft[f.key] === "" && " — 留空不会清除，要清除请点「✕ 清除」。"}
                </div>
              </div>
            );
          })}

          <div className="channel-actions">
            <button
              className="ghost"
              onClick={load}
              disabled={loading || restarting}
            >
              刷新
            </button>
            <button className="restart" onClick={restartGateway} disabled={saving || restarting || dirty}
                    title={dirty ? "有未保存的改动，先保存再重启" : "重启网关让新配置生效"}>
              {restarting ? "重启中…" : "重启网关生效"}
            </button>
            <button onClick={save} disabled={saving || restarting || !dirty}>
              {saving ? "保存中…" : "保存"}
            </button>
          </div>
          <p className="footnote">
            字段三态：未动 = 保持原样；输入 = 覆盖保存；点「✕ 清除」 = 删除该配置。
            密钥字段只显示是否已配置，不回显原文。保存后需「重启网关生效」（或服务器 docker restart hermes）。
          </p>
        </div>
      )}
    </div>
  );
}