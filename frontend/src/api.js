// 前端四个页面共用的接口小工具（2026-10-12 去重）。
// 之前 api() 在 Channels / Identity / ModelConfigs 里各抄了一份，加问数页时又抄了第四份——
// 现在只留这一份，谁要谁 import。
//
// 约定：前端只调自己后端的 /api/*（相对路径，dev 由 Vite 代理转发）；凭据只在后端，
// 前端不带任何 Authorization 头。

/** 统一的后端调用：有 body 才带 Content-Type（GET 不带）。 */
export function api(path, opts = {}) {
  return fetch(path, {
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    ...opts,
  });
}

/** 从错误响应里取后端给的中文文案（统一错误体 {detail}）；取不到就退回 HTTP 码。 */
export async function errorText(r) {
  try {
    const d = await r.json();
    return d?.detail || `HTTP ${r.status}`;
  } catch {
    return `HTTP ${r.status}`;
  }
}
