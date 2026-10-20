# Hermes Web Console — 前端调用接口文档

> 版本：v0.5 ｜ 本文只列出**前端实际调用**的接口。前端只调本后端 `/api/*`，凭据仅存于后端环境变量。

## 0. 通用约定

| 项 | 值 |
|---|---|
| 开发基址 | `http://localhost:8000`（前端 dev 用相对路径 `/api/...`，Vite 代理转发） |
| 生产基址 | 同源部署，前端仍用 `/api/...` |
| 数据格式 | 除 SSE 外均为 `application/json; charset=utf-8` |
| 鉴权 | **前端不带任何 `Authorization` 头**（无登录页）。后端默认放行；`APP_AUTH` / `POST /api/login` 是后端遗留能力，前端未使用，不在本文档范围 |
| 错误体 | 业务错误统一 `{ "detail": "<文案>" }` |

常见状态码：`400` 参数非法 ｜ `404` 不存在 ｜ `409` 冲突/忙碌 ｜ `410` 运行态已失效 ｜ `413` 内容过大 ｜ `502` 依赖异常（连不上 Hermes 等） ｜ `503` 上游 503 原样透传（§2 两条历史接口）。

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

---

## 6. 智能问数

> 「问数」tab 四个子页。**2026-10-20 起控制台改走 MCP**：原来"控制台与 hermes-mcp
> 共读同一个 SQLite 文件"的通道**已废弃**——那要求两端逐字对齐表结构与密码加密格式，
> 任何一侧改了另一侧就静默坏掉。现在 **MCP 是数据的唯一主人**，加密与校验只在它那边有一份。
>
> - **数据源 / 知识库 / SQL 模板**：控制台是 hermes-mcp 的 **MCP 客户端**——只管配置，
>   不连数据库、不测连通（所以没有"测试连接"接口）；真正连库的是 hermes-mcp。
>   ⚠️ **知识库和模板都没有"全局"**（2026-10-20 产品决定）：`connection` **必填**、
>   必须是真实存在的数据源，后端**拒收 `'*'`**。
> - **MCP 配置**：不同的事——它**代理 Hermes dashboard** 的 `/api/mcp/*`，
>   把问数服务注册到 Hermes 上。

### 6.1 数据源

#### `GET /api/db-connections`
列出全部数据源。
- `200`：`{ "connections": [...], "count": n }`
- 单条字段：
```json
{
  "name": "订单库", "type": "mysql",
  "host": "10.0.0.5", "port": 3306, "database": "shop", "username": "reporter",
  "read_only": true, "max_rows": 200,
  "has_password": true, "status": null, "last_error": null
}
```
- ⚠️ **永不回显密码**（明文、密文都不给），只有 `has_password` 布尔
- ⚠️ **没有 `id` / `created_at` / `updated_at`**：上游 `list_connections` 不回传它们。
  定位一条数据源靠 **`name`**（所以名称是它的身份，见下）
- ⚠️ `status` / `last_error` **目前恒为 `null`**——问数服务侧还没有写入它们
  （源码里 `set_status()` 定义了但零调用点）。后端**原样透传**，页面不展示
- `502` 连不上问数服务

#### `GET /api/db-connections/types`
类型目录：**前端表单与后端校验共用这一份**（不在前端写死）。
- `200`：`{ "types": [{ "id", "label", "group", "default_port", "hints" }], "field_labels": {...} }`
- 类型集 = 关系库 8 种（`mysql` / `postgresql` / `sqlserver` / `oracle` /
  `oceanbase_mysql` / `oceanbase_oracle` / `opengauss` / `dameng`）+ `sqlite`
  - ⚠️ `api`（把 HTTP 接口当表查）**本轮不做**：① 表单是另一套字段；
    ② 上游读接口**不回传** `api_url` 等字段，编辑不了
- `group` 决定表单显示哪些字段：`relational` → 主机/端口/库名/账号/密码；`file`（sqlite）→ **只要文件路径**

#### `POST /api/db-connections`
新增一条。
- 请求体：`{ "name", "type", "database", "host"?, "port"?, "username"?, "max_rows"?, "password"? }`
- **必填字段按类型**：

| 类型组 | 必填 |
|---|---|
| 关系库 | `name` / `type` / `host` / `database` / `username`，且**首次必须给 `password`** |
| `sqlite` | `name` / `type` / `database`（= 数据库文件路径）；`host`/`port`/`username` 一律被清空 |

- `200`：`{ "ok": true, "name": "..." }`
- `400` 校验失败（**含类型不在白名单**——上游不校验类型、非法值会照收，所以由我们拦）；
  `409` 重名；`502` 连不上问数服务

#### `PUT /api/db-connections/{name}`
覆盖一条**已存在**的。
- Path：`name` 是这条的身份（body 里不用再给）
- 请求体：同 `POST`，去掉 `name`
- `200`：`{ "ok": true, "name": "..." }`；`400` / **`404` 不存在** / `502`
- ⚠️ **不支持改名**：上游按 `name` upsert，所谓"改名"实际是"删旧的 + 建新的"、
  中途失败会留下空档。要改名就删了重建

#### `DELETE /api/db-connections/{name}`
- `200`：`{ "ok": true, "name": "..." }`；**`404` 不存在**；`502`
- ⚠️ **立即生效、不可恢复**（问数服务侧没有回收站）

**密码三态**（与模型配置的 `api_key` 一致）：不发 `password` = 保持原值 ｜ `""` = 清除 ｜ 非空 = 换新。
> 与 Hermes 渠道的 `env` 语义**相反**（那边空串是"跳过"，不清除）。

### 6.2 知识库（标签 + 知识切片）

> 存的是**业务标签和知识切片**（"活跃客户"到底指什么）。问数时命中的切片会被**注入到
> 生成 SQL 的提示里**——所以改这里 = 改所有人问出来的数字含义。
> ⚠️ **没有全局条目**：`connection` **必填**、必须是**真实存在**的数据源，拒收 `'*'`。

#### `GET /api/knowledge-entries?connection=<数据源名>`
- `connection` 可选：传了返回该数据源的条目；不传返回全部
- `200`：`{ "entries": [{ "id", "connection", "term", "definition", "anchor", "note", "created_at", "updated_at" }], "count": n }`
  - `term` = 标签，`definition` = 知识切片（**接口字段名不变**，只是界面叫法改了）
- `502` 连不上问数服务

#### `GET /api/knowledge-entries/search?connection=<数据源名>&query=<问题>&limit=8`
「**试一下**」：输入一个问题，看会命中哪些切片（问数侧是 FTS5 trigram 全文检索，对中文友好）。
**这是验证一条切片有没有生效的唯一手段。**
- `connection` **必填**；`query` 必填（≤500 字符）；`limit` 1–50，默认 8
- `200`：`{ "hits": [...], "count": n }`
- `400` 缺问题 / 缺数据源；`502`

#### `POST /api/knowledge-entries`
- 请求体：`{ "term", "definition", "connection", "anchor"?, "note"? }`
- `200`：`{ "ok": true, "id": n }`（`id` 由问数服务分配）
- `400`：标签/切片为空、**数据源不存在**、`connection` 为空或为 `'*'`；`502`

#### `PUT /api/knowledge-entries/{entry_id}`
- 按 id 改（id 是问数服务分配的整数）。请求体同 `POST`
- `200`：`{ "ok": true, "id": n }`；`400`；**`404` 不存在**（上游对未知 id 抛异常且不给原因，由本层先查再翻）

#### `DELETE /api/knowledge-entries/{entry_id}`
- `200`：`{ "ok": true, "id": n }`；**`404` 不存在**

### 6.3 SQL 模板

> 「用户这么说 → 就用这条 SQL」的沉淀，问数时会优先匹配。同样**没有全局**。

#### `GET /api/sql-templates?connection=<数据源名>`
- `connection` 可选：传了返回该数据源的；不传返回全部
- `200`：`{ "templates": [{ "id", "connection", "question", "sql", "name", "params", "note", ... }], "count": n }`

#### `POST /api/sql-templates`
- 请求体：`{ "question", "sql", "connection", "name"?, "params"?, "note"? }`
  - `sql` **必须是只读查询**（`SELECT` / `WITH` 开头）——本层先拦、给可读的 400；**权威校验在问数服务**
  - `params` 是给**人看**的参数说明，必须是 **JSON 对象**字符串（如 `{"year": "年份"}`）
  - `connection` 必须是**真实存在**的数据源
- `200`：`{ "ok": true, "id": n }`；`400`（SQL 非只读 / params 非 JSON 对象 / 数据源不存在）；`502`

#### `PUT /api/sql-templates/{template_id}` · `DELETE /api/sql-templates/{template_id}`
- 同知识库：`200` `{ "ok": true, "id": n }`；`404` 不存在；`400` / `502`

### 6.4 MCP 服务配置（代理 Hermes dashboard）

> ⚠️ 我们的路径是 **`/api/mcp-servers`**（连字符）；上游是 `/api/mcp/servers`。前端与后端同源，照抄上游路径会撞，所以特意改了连字符。

#### `GET /api/mcp-servers`
- `200`：
```json
{
  "servers": [{
    "name": "sql-coustm", "transport": "http", "url": "http://hermes-mcp:9123/mcp",
    "command": null, "args": [], "env": {}, "auth": null, "enabled": true, "tools": null
  }],
  "configured_name": "sql-coustm",
  "configured_found": true
}
```
- `servers` 来自上游摘要（`env` 上游已打码）；`configured_name` 是后端 `MCP_SERVER_NAME`，页面据此标记"问数那条"
- `502`：连不上 dashboard / dashboard 登录失效 / 上游格式异常

#### `POST /api/mcp-servers`
新增一条注册（**一期不做鉴权字段**，`auth` 固定发 `"none"`）。
- 请求体：`{ "name": "sql-coustm", "url": "http://hermes-mcp:9123/mcp" }`
- 校验：名称 `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`（会被拼进上游 URL 路径）；URL 必须 `http(s)://` 开头、不含空白、≤500 字符
- `200`：`{ "ok": true, "name": ..., "url": ... }`
- `400` 校验失败（本地拦，不打上游）；`409` 重名；`502` 连不上 dashboard

#### `DELETE /api/mcp-servers/{name}`
- `200`：`{ "ok": true, "name": ... }`；`404` 该服务不存在（本地预检，**不打上游写接口**）；`502`

#### `POST /api/mcp-servers/{name}/test`
让 **Hermes 去连一次**并列出工具（**不是控制台去连**）。
- `200`：`{ "ok": bool, "tools": [{ "name", "description" }], "error": "...", "prompts": n, "resources": n }`
- ⚠️ 上游把"连不上"也报成 **HTTP 200 + `ok:false`**——页面看 `ok` 字段，不看状态码
- `404` 服务不存在

#### `PUT /api/mcp-servers/{name}/enabled`
- 请求体：`{ "enabled": true }`（**对象，不是裸布尔**——上游模型如此，发裸布尔会被判 422）
- `200`：上游结果；`404` / `502`
- 生效时机：**新会话或重连后**，当前对话不受影响

> **没有"改地址"接口**：上游没有修改单条注册的 API，"改地址"只能先删后建、中途失败会留下空档，所以不做——要换地址就在页面上删掉重加。
