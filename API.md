# Hermes Web Console — 前端调用接口文档

> 版本：v0.4 ｜ 本文只列出**前端实际调用**的接口。前端只调本后端 `/api/*`，凭据仅存于后端环境变量。

## 0. 通用约定

| 项 | 值 |
|---|---|
| 开发基址 | `http://localhost:8000`（前端 dev 用相对路径 `/api/...`，Vite 代理转发） |
| 生产基址 | 同源部署，前端仍用 `/api/...` |
| 数据格式 | 除 SSE 外均为 `application/json; charset=utf-8` |
| 鉴权 | 所有 `/api/*` 都过 `require_app_token`；默认（`APP_AUTH` 未设）放行，无登录页。⚠️ **前端目前不带任何 `Authorization` 头**——`APP_AUTH=1` 是后端能力，前端尚未接（`POST /api/login` 是遗留路由，未启用） |
| 错误体 | 业务错误统一 `{ "detail": "<文案>" }` |

常见状态码：`400` 参数非法 ｜ `401` 令牌不对（仅 `APP_AUTH=1` 时） ｜ `404` 不存在 ｜ `409` 冲突/忙碌 ｜ `410` 运行态已失效 ｜ `413` 内容过大 ｜ `502` 依赖异常 ｜ `503` 上游 503 原样透传（§2 两条历史接口），或 `APP_AUTH=1` 但后端未配 `HERMES_PASS`。

---

## 1. 对话（SSE）

### `POST /api/chat`
发起一轮对话，返回 SSE 流（`text/event-stream`）。

请求体：
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `text` | string | 是 | 1–100000，去首尾空白后非空 |
| `stored_session_id` | string | 否 | 续用历史会话，`^[A-Za-z0-9][A-Za-z0-9._-]*$` |
| `profile` | string | 否 | 1–128 |
| `model` | string | 否 | **仅新建会话时生效**（即无 `stored_session_id`）；`^\S+$`，禁止以 `--` 开头 |
| `provider` | string | 否 | 必须与 `model` 同时提供；同上校验 |

SSE 帧：
```
event: <事件名>
data: {"type":"<事件名>","session_id":"...","stored_session_id":"...","profile":"...","payload":{...}}
```
- 每 15s 无数据发保活注释 `: keep-alive`
- 浏览器断开仅解除订阅，不停止本轮生成

前端消费的事件：
| 事件 | payload 关键字段 | 说明 |
|---|---|---|
| `session.ready` | `session_id` / `stored_session_id` / `profile` | 流首帧，回填会话标识（后端合成） |
| `message.delta` | `text` | 文本增量 |
| `tool.start` / `tool.complete` | `tool_id`, `name` | 工具调用 |
| `session.info` | `model`, `provider` | 会话实际运行的模型 |
| `message.complete` | `status`(`complete\|error\|interrupted`), `text`, `error` | 终态 |
| `chat.error` | `message`, `status`(`unknown\|error`) | 终态错误（后端合成） |
| `error` | `message` | 提示（非终态） |

⚠️ **后端还会推另外 4 个事件**（上游原样透传，表格里没列是因为前端当前忽略）：`message.start`、`message.interim`、`reasoning.delta`（思考流增量）、`session.title`（上游自动起标题）。**换个人写前端**要知道它们会到达，别当成异常；要看思考过程就用 `reasoning.delta`。

**终止事件**：`message.complete`、`chat.error`。

错误：`409` 该会话仍在生成（本条未发送）；`410` 运行态已失效；`502` 无法连接依赖。

---

## 2. 会话

### `GET /api/sessions`
历史会话列表。
- Query：`limit`（1–100，默认 20）、`offset`（≥0，默认 0）、`profile`（可选）
- 后端**固定追加** `order=recent`、`archived=exclude`、`min_messages=1`（前端无需也无法覆盖）——所以**归档会话和空会话不会出现在列表里**，这是产品行为，不是筛选器。
- `200`：`{ "sessions": [ { "id", "profile", "title", "preview", ... } ], "total": int, "offset": int }`

### `GET /api/sessions/{session_id}/messages`
会话历史消息（前端 `limit=50` 分页）。
- Path：`session_id`
- Query：`limit`（1–500，默认 50）、`offset`（≥0，默认 0）、`profile`（可选）
- 后端**固定追加** `order=latest`、`include_compacted=true`（前端无需也无法覆盖）——返回的是**最新的一段**，压缩前的历史可能不在范围内（前端底部提示语说的就是这件事）。
- `200`：
```json
{
  "session_id": "...", "profile": "...",
  "messages": [{
    "id": "...", "role": "user|assistant|tool",
    "content": "...", "display_content": "...", "display_kind": "...",
    "hidden": false, "name": "...",
    "tool_calls": [{ "id": "...", "function": { "name": "..." } }],
    "tool_call_id": "...", "tool_name": "...",
    "reasoning": "...", "reasoning_content": "...", "reasoning_details": "...",
    "codex_message_items": "..."
  }],
  "pagination": { "order": "latest", "returned": 50, "offset": 0, "limit": 50 }
}
```
**字段语义（前端解析依赖，别想当然）**：
- `display_content`：**存在即优先**（前端用 `Object.hasOwn` 判断，不是"非空"判断）——有它就用它，没有才回落到 `content`。
- `display_kind`：展示类型。`hidden` / `system` / `tool` 三种取值会被前端**当成隐藏条目过滤掉**（和显式 `hidden: true` 同效）；其他值照常展示。
- `name`：`tool_name` 的兜底来源（`tool_name ?? name`），OpenAI 风格的工具消息用 `name`。
- `codex_message_items`：codex 系模型的正式答案载体，存在时用于恢复正文。
- `hasMore` 由前端按 `pagination.returned >= pagination.limit` 推导，不在响应里。

### `GET /api/sessions/{session_id}/state`
确认会话生成状态。
- Query：`profile`（可选）
- `200`：`{ "stored_session_id", "session_id"?, "profile", "status": "running|idle|unknown|complete|error|interrupted", "message"? }`
- `502`

### `POST /api/sessions/{session_id}/interrupt`
停止本服务进程内正在生成的回合；终态经原 SSE 流收尾。
- Query：`profile`（可选）
- `200`：`{ "ok": true, ... }`（`status` 可能为 `"not_interrupted"`：上游没有可中断任务）
- `409` 无生成中内容；`410` 运行态已失效

### `POST /api/sessions/{session_id}/model`
会话级切换模型，不改全局默认。
- Query：`profile`（可选）
- 请求体：`{ "model": str, "provider": str, "confirm_expensive_model": bool=false }`
- `200`：
```json
{ "ok": true, "model": "...", "provider": "...", "value": "...",
  "warning": "", "confirm_required": false, "confirm_message": "",
  "deferred": false, "scope": "session" }
```
  - `confirm_required=true`：未真正切换，需带 `confirm_expensive_model=true` 重发
  - `deferred=true`：生成中，本轮结束后生效
- `410` / `502`

---

## 3. 模型配置

> 概念只有一个：**一个厂商端点 + 其模型清单**。

### `GET /api/model-configs`
> ⚠️ **列表来源**：后端把 config.yaml 的 `custom_providers` 段**和** `providers` 段**合并**返回，两段的 `id` 都是 `"custom:" + name.strip().lower()`。所谓"只显示自定义"是靠配置文件里本来就只有自定义厂商，**不是前端过滤**。
- Query：`refresh`（兼容用，可选）
- `200`：
```json
{
  "configs": [{
    "id": "custom:<name.lower()>",
    "manage_id": "custom:<name.lower()>",
    "name": "...", "base_url": "...",
    "items": [{ "model", "display_name", "source": "manual|config|default",
                "hidden", "context_length", "reasoning_effort", "is_default" }],
    "models": ["..."],
    "hidden_models": ["..."],
    "model": "...", "has_api_key": true,
    "discover_models": true, "is_current": false, "api_mode": ""
  }],
  "current": { "provider": "...", "model": "...", "base_url": "..." }
}
```

### `POST /api/model-configs/validate`
「测试连接」。
- 请求体：`{ "name"?, "base_url", "model", "api_key"?, "api_mode"? }`
- `200`：结果对象，含 `ok`（`false` 时 `message` 给原因）
- `502`

### `POST /api/model-configs`
新增/编辑厂商端点（`id` 找不到即新增）。
- 请求体：
| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string? | `custom:<name.lower()>`，编辑时原样回传 |
| `name` | string | 必填 |
| `base_url` | string | 必填 |
| `model` | string | 必填，该厂商默认模型 |
| `api_key` | string? | **三态**：传值=换新；空串=清除；不发=不动 |
| `api_mode` | string? | `""|chat_completions|codex_responses|anthropic_messages` |
| `context_length` | int? | |
| `discover_models` | bool? | |
| `make_default` | bool | 是否同时设为全局默认 |
- `200`：`{ "ok": true, "endpoint_id": "custom:<name.lower()>" }`

### `POST /api/model-configs/{vendor_id}/default?model=<model>`
把指定模型设为全局默认。
- Query：`model`（必填）
- `200`：`{ "ok": true }`，或 `{ "ok": false, "confirm_required": true, "confirm_message": "" }`

### `DELETE /api/model-configs/{vendor_id}`
删除整个厂商端点。
- `200`：`{ "ok": true }`；`404`

### `POST /api/model-configs/{vendor_id}/models/{model_id}/hide`
从可选列表隐藏（= 删除）一个模型。
- `200`：`{ "ok": true, "hidden": true }`

### `POST /api/model-configs/{vendor_id}/models`
添加单模型。
- 请求体：`{ "name", "display_name"?, "context_length"?, "reasoning_effort"? }`
- `reasoning_effort` 合法值：`minimal|low|medium|high|xhigh|max|ultra`
- `200`：`{ "ok": true, "model": "<name>" }`；`400` 档位非法；`409` 重名

### `PUT /api/model-configs/{vendor_id}/models/{model_id}`
编辑 / 重命名模型（旧名隐藏，默认模型跟随改名）。
- 请求体：`{ "name"（新名）, "display_name"?, "context_length"?, "reasoning_effort"? }`
- `200`：`{ "ok": true, "model": "<新名>" }`；`400`

### `POST /api/model-configs/{vendor_id}/models/hide-batch`
批量隐藏（= 批量删除）。
- 请求体：`{ "models": ["..."] }`
- `200`：`{ "ok": true, "count": n }`

---

## 4. 消息渠道（飞书 / 钉钉）

> **一套接口、两个平台共用。** 读卡 `GET`、保存 `PUT`、检查 `POST .../test` 三条是**同一条接口**：把路径里的 `{platform_id}` 换成 `feishu` 或 `dingtalk` 即可，**请求体与返回结构完全相同**（上游 `MessagingPlatformUpdate` 没有平台专属字段）。差异只有三处：① `env` / `clear_env` 里的**键名**不同；② 卡片 `env_vars[]` 的条目不同；③ 错误文案里的平台名不同（"飞书" / "钉钉"）。
>
> `env` 里的**空串不清除**，清除必须用 `clear_env`。

### 平台白名单与可写键

- `platform_id` 只放行 `feishu`（飞书）、`dingtalk`（钉钉）；**白名单外一律 `404`，且不打上游**。加平台 = 后端加一个 id + 前端加一组字段表。
- 可写键**不是固定清单**：保存时后端现打上游卡片、取 `env_vars[].key` 作为 allowlist。实测（2026-10-09，对真实 dashboard）：

| 平台 | 可写键 | 必填 |
|---|---|---|
| 飞书 | `FEISHU_APP_ID` `FEISHU_APP_SECRET` `FEISHU_ENCRYPT_KEY` `FEISHU_VERIFICATION_TOKEN` `FEISHU_DOMAIN` `FEISHU_ALLOWED_USERS` | 前两个 |
| 钉钉 | `DINGTALK_CLIENT_ID` `DINGTALK_CLIENT_SECRET` `DINGTALK_ALLOWED_USERS` `DINGTALK_WEBHOOK_URL` | 前两个 |

  这张表会**随上游漂**（Hermes 加字段时卡片和校验跟着变）——**以卡片 `env_vars[].key` 为准，别把它当契约**。越权键 → `400`，文案里会列出**当前**可用键。
- **钉钉没有**域名 / 加密密钥 / 验证令牌这类字段：它只有 Stream 出站长连，没有 HTTP 回调，也就不需要回调地址和验签。`DINGTALK_WEBHOOK_URL` 是**出站**用途（定时 / 主动推送），不填不影响正常对话。

### `GET /api/channels/{platform_id}`
读平台卡片（unscoped，即"正在跑的那份网关"）。`200`：卡片完整结构，两个平台同构

```json
{
  "id": "dingtalk", "name": "DingTalk", "description": "...", "docs_url": "...",
  "enabled": true, "configured": true, "gateway_running": true,
  "state": "connected|disabled|not_configured|pending_restart|gateway_stopped|startup_failed",
  "error_code": null, "error_message": null, "updated_at": "...",
  "home_channel": { "channel_id": "..." }, "ingress_url": null,
  "env_vars": [{
    "key": "DINGTALK_CLIENT_SECRET", "required": true, "is_set": true,
    "redacted_value": "abc...xyz", "description": "...", "prompt": "...",
    "help": "", "url": "https://open-dev.dingtalk.com", "is_password": true, "advanced": false
  }]
}
```

- ⚠️ **卡片里没有 `hot_served`**——它在 `PUT` 的响应里（见下）。
- `env_vars[]` 既是"可写键清单"，也是表单元数据（`required` / `is_password` / `description` / `prompt` / `url`）；`is_set` 是前端"已配置"徽标的依据。**明文永不返回**，只有 `redacted_value`。
- `404` 平台不在目录（本层判定，不打上游）；`502` 上游格式异常

### `PUT /api/channels/{platform_id}`
- 请求体：`{ "enabled"?: bool, "env"?: { "KEY": "value" }, "clear_env"?: ["KEY"] }`——两个平台**同一个模型**，无平台专属字段
- 语义：`env` 里的**空串会被上游跳过**（空串 ≠ 清除）；清除必须走 `clear_env`。凭据落 `.env`，`enabled` 落 `config.yaml` 的 `platforms.<id>.enabled`
- `200`：`{ "ok": true, "platform": "dingtalk", "hot_served": false }` —— **`hot_served` 只在这个响应里**；`false` 表示需重启网关生效（走下面那条固定接口）
- `400` 越权键；`409` 上游拒绝（含多路复用端口冲突）/ `404` / `502`

### `POST /api/channels/{platform_id}/test`
「检查状态」。⚠️ **不是探活**：上游只按 enabled → 必填键齐 → 网关在跑 → `state` 给结论，**不发任何网络请求**——所以它**不会**验证凭据是否有效、能不能连上开放平台。
- `200`：`{ "ok": bool, "state": "...", "message": "..." }`（`message` 是上游英文文案）

### `POST /api/channels/feishu/restart`
重启网关让新配置生效（期间渠道短暂断线）。

> **路径里的 `feishu` 是固定段，不是平台参数。** 这是渠道这块**唯一的特例**：上面三条按 `{platform_id}` 换平台，这一条**只有一个 URL、与平台无关**。接口语义就是「重启网关」——前端无论当前在飞书 tab 还是钉钉 tab 都只调这一条，**不要**按平台拼第二条（`POST /api/channels/dingtalk/restart` 不存在，返回 `404`）。
> 它是**网关级**操作：一次调用重启整个网关，所有渠道（飞书 / 钉钉 / 微信 / api_server…）和正在进行的对话都会短暂断线（单容器实测回落约 15~40s 自动恢复）。上游没有单平台重启的口子。

- `200`：`{ "ok": true, "pid": ..., "name": "..." }`

---

## 5. Agent 身份文件

> 逻辑名 `soul|memory|user`（实际路径不出响应）。前端分别用三个名字各调一次。

### `GET /api/agent-files/{name}`
- `200`：`{ "name", "exists", "content", "byteSize", "truncated", "binary" }`
  - 文件不存在不是错误：`exists=false`、`content=""`、`byteSize=0`
- `404` 未知名字

### `PUT /api/agent-files/{name}`
保存文件内容（乐观锁 + 写前首次自动备份，备份失败即拒写）。
- 请求体：`{ "content": str, "base_byteSize": int }`（`base_byteSize` = 读取时拿到的 `byteSize`）
- `200`：`{ "ok": true, "byteSize": n }`
- `409`：`{ "detail": "...", "current_byteSize": n }`（文件已被更新，草稿需保留、重新加载后再存）
- `413`：内容 >8MB，或文件 >512KB 已截断无法安全覆盖（阈值是后端常量：读上限 `PREVIEW_MAX_BYTES = 512KB`、写上限 `WRITE_MAX_BYTES = 8MB`；读取时的 `truncated` 由读上限判出）
- `404` 未知名字；`400` / `403` / `502`

**前端约定**：`truncated` 或 `binary` 为真时前端将该文件置为只读、禁用保存。
