import { useCallback, useEffect, useState } from "react";

// 身份配置页（2026-09-28）—— 编辑**默认 Agent** 的三个身份 / 记忆文件：
//   soul   SOUL.md    身份设定（Hermes 自带默认模板，Agent 不会自行改写）
//   memory MEMORY.md  长期记忆（**Agent 自己也会往里写**）
//   user   USER.md    用户画像
// 后端 /api/agent-files 锁死 default profile，前端全程不出现 profile 概念；绝对路径也不出后端。
//
// 文案（副标题 / 输入框上方提示 / 空态占位 / 示例）出自 agent-identity-files-design.md §3.1，
// 改文案请先改那份文档，别只改这里。
//
// 并发保护：读取时拿到的 byteSize 当版本号，保存回传 base_byteSize；不一致后端回 409 →
// 这里**绝不清空草稿**，只提示 + 给「重新加载」按钮（点了才用最新内容覆盖草稿）。
// ⚠️ 409 后刻意**不更新本地 base**——更新了下次保存就会带着陈旧内容静默覆盖掉 Agent 刚写进去的记忆，
// 那正是乐观锁要防的事。要拿新 base 只能走「重新加载」。
//
// 「新会话生效」：三个文件都在会话开始时进 system prompt（见设计 §4 第 1 条），当前会话不受影响。

const SOUL_EXAMPLE = `# Agent 身份
- 直接、简洁，回复长度匹配问题分量
- 不客套、不自夸；不确定就直说
- 深话只在被追问或值得时说`;

const MEMORY_EXAMPLE = `# 项目记忆
- 2026-09，在做 Hermes 图表面板（React + FastAPI）
- 技术栈：Python 3.10 / Vite / dashboard REST
- 已知坑：……`;

const USER_EXAMPLE = `# 用户画像
- 后端工程师，熟悉 Python / Go
- 回复先给结论再展开
- 中文沟通`;

const CARDS = [
  {
    name: "soul",
    title: "身份 SOUL",
    subtitle: "告诉 Agent 它是谁、怎么表现 —— 性格、语气、行为准则",
    prompt:
      "写你希望 Agent 长期保持的行为方式：回复语气（直率/礼貌/简洁）、回复长度习惯、做事准则、绝对不做什么。Hermes 自带默认版本，按需增删。Agent 不会自行改写这里。",
    placeholder: "例：# Agent 身份\n直接、简洁，回复长度匹配问题分量；不客套、不确定就直说。",
    example: SOUL_EXAMPLE,
  },
  {
    name: "memory",
    title: "记忆 MEMORY",
    subtitle: "Agent 每次会话都会加载的长期记忆 —— 项目、技术、决策、踩坑",
    prompt:
      "写你希望 Agent 每次都能记住的环境事实：正在做的项目、技术栈、已定决策、踩过的坑、重要约束。建议短条目分段写（Agent 会定期做压缩整理）。",
    warning: "对话中 Agent 会自行向这里追加内容，你手写的条目可能被后续覆盖。",
    placeholder: "例：# 项目记忆\n- 2026-09，在做 Hermes 图表面板\n- 技术栈：Python 3.10 / Vite",
    example: MEMORY_EXAMPLE,
  },
  {
    name: "user",
    title: "用户画像 USER",
    subtitle: "让 Agent 了解在跟谁说话 —— 你的背景、偏好、沟通习惯",
    prompt:
      "写关于你自己的信息，Agent 会据此调整回复：怎么称呼你、用什么语言、期望的回复详细度、你熟悉/不熟的领域、讨厌什么。可以留空。",
    placeholder: "例：# 用户画像\n- 后端工程师，熟悉 Python / Go\n- 回复先给结论再展开",
    example: USER_EXAMPLE,
  },
];

const CARDS_BY_NAME = Object.fromEntries(CARDS.map((c) => [c.name, c]));
const NAMES = CARDS.map((c) => c.name);
const EFFECT_HINT = "修改在下一轮新会话生效，当前会话不受影响。";

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

export default function Identity() {
  const [files, setFiles] = useState({});      // name → {loading,exists,content,byteSize,truncated,binary,error}
  const [drafts, setDrafts] = useState({});    // name → 草稿（键不存在 = 未改动过）
  const [saving, setSaving] = useState("");    // 正在保存的 name
  const [notice, setNotice] = useState(null);  // {kind:'ok'|'err', name, text}
  const [savedAt, setSavedAt] = useState({});  // name → 本次会话内保存成功的时间
  const [conflict, setConflict] = useState({}); // name → 上游当前 byteSize（409 后）

  const load = useCallback(async () => {
    setFiles(Object.fromEntries(NAMES.map((n) => [n, { loading: true }])));
    setDrafts({});
    setConflict({});
    setSavedAt({});
    setNotice(null);
    const results = await Promise.all(
      NAMES.map(async (name) => {
        try {
          const r = await api(`/api/agent-files/${name}`);
          if (!r.ok) return [name, { loading: false, error: await errorText(r) }];
          return [name, { loading: false, ...(await r.json()) }];
        } catch (e) {
          return [name, { loading: false, error: "请求失败：" + e.message }];
        }
      })
    );
    setFiles(Object.fromEntries(results));
  }, []);

  const hasDrafts = Object.keys(drafts).length > 0;

  const refresh = () => {
    if (hasDrafts && !window.confirm("有未保存的改动，刷新会丢弃它们。确定刷新？")) return;
    load();
  };

  useEffect(() => { load(); }, [load]);

  // 重新加载单个文件：点了才用磁盘最新内容覆盖草稿（409 的恢复路径）
  const reloadOne = async (name) => {
    if (drafts[name] !== undefined &&
        !window.confirm("草稿将被磁盘上的最新内容覆盖（建议先把草稿复制出去）。确定重新加载？")) return;
    setConflict((c) => { const n = { ...c }; delete n[name]; return n; });
    setFiles((f) => ({ ...f, [name]: { ...f[name], loading: true, error: "" } }));
    try {
      const r = await api(`/api/agent-files/${name}`);
      if (!r.ok) throw new Error(await errorText(r));
      const data = await r.json();
      setFiles((f) => ({ ...f, [name]: { loading: false, ...data } }));
      setDrafts((d) => { const n = { ...d }; delete n[name]; return n; });
      setSavedAt((s) => { const n = { ...s }; delete n[name]; return n; });
      setNotice(null);
    } catch (e) {
      setFiles((f) => ({ ...f, [name]: { ...f[name], loading: false, error: e.message } }));
    }
  };

  const setDraft = (name, value) => setDrafts((d) => ({ ...d, [name]: value }));

  const clearDraft = (name) => {
    if (!window.confirm("清空编辑区？保存后该文件内容将变成空（点「重新加载」可放弃）。")) return;
    setDraft(name, "");
  };

  const insertExample = (name) => {
    if (drafts[name] !== undefined &&
        !window.confirm("当前编辑区有内容，插入示例会替换掉它。确定？")) return;
    setDraft(name, CARDS_BY_NAME[name].example);
  };

  const save = async (name) => {
    const file = files[name];
    const content = drafts[name];
    if (content === undefined || content === file.content) return;
    // 占位示例防误存：内容与示例一模一样 → 确认一次，别把模板当正文存进文件
    if (content.trim() === CARDS_BY_NAME[name].example.trim() &&
        !window.confirm("内容与「填写建议 / 示例」完全相同，确认要原样保存进文件吗？")) return;

    setSaving(name);
    setNotice(null);
    try {
      const r = await api(`/api/agent-files/${name}`, {
        method: "PUT",
        body: JSON.stringify({ content, base_byteSize: file.byteSize }),
      });
      if (r.status === 409) {
        const d = await r.json().catch(() => ({}));
        setConflict((c) => ({ ...c, [name]: d.current_byteSize }));
        setNotice({ kind: "err", name, text: d.detail || "文件已被更新，请重新加载后再保存。" });
        return;   // 草稿原样保留
      }
      if (!r.ok) {
        setNotice({ kind: "err", name, text: "保存失败：" + (await errorText(r)) });
        return;   // 草稿原样保留
      }
      const d = await r.json();
      setFiles((f) => ({ ...f, [name]: { ...f[name], exists: true, content, byteSize: d.byteSize, truncated: false, error: "" } }));
      setDrafts((dr) => { const n = { ...dr }; delete n[name]; return n; });
      setConflict((c) => { const n = { ...c }; delete n[name]; return n; });
      setSavedAt((s) => ({ ...s, [name]: new Date().toLocaleTimeString("zh-CN", { hour12: false }) }));
      setNotice({ kind: "ok", name, text: `已保存（${d.byteSize} 字节）。${EFFECT_HINT}` });
    } catch (e) {
      setNotice({ kind: "err", name, text: "保存失败：" + e.message });
    } finally {
      setSaving("");
    }
  };

  return (
    <div className="page identity">
      <header>
        <h1>身份配置</h1>
        <p className="meta">
          默认 Agent（default）的身份 / 记忆文件。三个文件都会在会话开始时读进它的 system prompt。
        </p>
        <button className="ghost" onClick={refresh}>刷新</button>
      </header>

      {notice && (
        <div className={`id-notice ${notice.kind}`}>
          {notice.name ? `【${CARDS_BY_NAME[notice.name].title}】` : ""}{notice.text}
        </div>
      )}

      {CARDS.map((card) => {
        const file = files[card.name] || { loading: true };
        const draft = drafts[card.name];
        const value = draft ?? file.content ?? "";
        const dirty = draft !== undefined && draft !== file.content;
        const readOnly = !!file.truncated || !!file.binary;
        const busy = saving === card.name;

        return (
          <section className="id-card" key={card.name}>
            <div className="channel-head">
              <h2>{card.title}</h2>
              {file.loading ? (
                <span className="status-badge">读取中…</span>
              ) : file.error ? (
                <span className="status-badge err">读取失败</span>
              ) : (
                <span className={`status-badge ${file.exists ? "ok" : "off"}`}>
                  {file.exists ? `${file.byteSize} 字节` : "未创建"}
                </span>
              )}
              {dirty && <span className="status-badge warn">未保存</span>}
              {readOnly && <span className="status-badge err">只读</span>}
              {savedAt[card.name] && <span className="id-saved">已保存 {savedAt[card.name]}</span>}
            </div>
            <p className="meta">{card.subtitle}</p>

            {file.loading ? (
              <p className="hint">加载中…</p>
            ) : file.error ? (
              <div className="state-block">
                <p>{file.error}</p>
                <button className="ghost" onClick={() => reloadOne(card.name)}>重试</button>
              </div>
            ) : (
              <>
                <p className="id-prompt">{card.prompt}</p>
                {card.warning && <p className="id-prompt warn">⚠️ {card.warning}</p>}
                <p className="id-effect">{EFFECT_HINT}</p>

                {readOnly && (
                  <div className="id-conflict">
                    {file.truncated
                      ? `文件超过 512KB（${file.byteSize} 字节），只展示了一部分——保存会丢内容，已停用保存。请在服务器上直接编辑。`
                      : "该文件不是文本，无法在此编辑。"}
                  </div>
                )}
                {conflict[card.name] !== undefined && (
                  <div className="id-conflict">
                    <span>
                      文件已被更新（当前 {conflict[card.name]} 字节，多半是 Agent 刚写过）。
                      你的草稿还在，但<strong>必须重新加载后才能保存</strong>。
                    </span>
                    <button className="ghost tiny" onClick={() => reloadOne(card.name)}>
                      重新加载（覆盖草稿）
                    </button>
                  </div>
                )}

                <textarea
                  className="id-editor"
                  rows={12}
                  spellCheck={false}
                  readOnly={readOnly}
                  value={value}
                  placeholder={card.placeholder}
                  onChange={(e) => setDraft(card.name, e.target.value)}
                />

                <details className="id-example">
                  <summary>填写建议 / 示例</summary>
                  <pre>{card.example}</pre>
                  <button
                    type="button"
                    className="ghost tiny"
                    disabled={readOnly}
                    onClick={() => insertExample(card.name)}
                  >
                    插入示例
                  </button>
                </details>

                <div className="channel-actions">
                  <button
                    className="ghost"
                    disabled={busy || readOnly || value === ""}
                    onClick={() => clearDraft(card.name)}
                    title="把编辑区清空（保存后文件变为空）"
                  >
                    清空
                  </button>
                  <button
                    onClick={() => save(card.name)}
                    disabled={busy || readOnly || !dirty}
                  >
                    {busy ? "保存中…" : "保存"}
                  </button>
                </div>
              </>
            )}
          </section>
        );
      })}

      <p className="footnote">
        保存走 dashboard 的原子写；写前会做一次实时备份，并校验文件是否在你编辑期间被 Agent 改过
        （不一致会拒绝保存，不会覆盖）。备份是整机快照，首次保存时才触发。
      </p>
    </div>
  );
}
