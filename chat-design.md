可以，下面区分 **现有链路** 和 **拟新增接口**，所有请求仍只经过 dashboard。

## 1. 总体路径

```text
前端 React
  │ /api/*（开发时经 Vite 代理）
  ▼
自研 FastAPI 后端
  ├─ dashboard REST：历史列表、历史消息、重命名、删除
  └─ dashboard /api/ws：创建、恢复、发送、停止、切换模型
       ▼
     Hermes
```

浏览器不直连 Hermes，也不持有 Hermes 的登录凭据。

## 2. 各功能调用路径

**下表是目标设计：`POST /api/chat` 已存在但需要扩展，其余表内自研后端接口拟新增。**

| 前端操作 | → 自研后端 | → Hermes dashboard |
|---|---|---|
| 新对话首次发送 | `POST /api/chat`，传 `text` | WS `session.create` → `prompt.submit` |
| 已有对话继续发送 | `POST /api/chat`，传 `text、stored_session_id` | WS `session.resume` 或复用活会话 → `prompt.submit` |
| 获取历史列表 | `GET /api/sessions?limit=20&offset=0` | REST `GET /api/sessions` |
| 打开历史消息 | `GET /api/sessions/{id}/messages` | REST `GET /api/sessions/{id}/messages` |
| 重命名、置顶、归档 | `PATCH /api/sessions/{id}` | REST `PATCH /api/sessions/{id}` |
| 删除对话 | `DELETE /api/sessions/{id}` | REST `DELETE /api/sessions/{id}` |
| 停止生成 | `POST /api/sessions/{id}/interrupt` | 后端找到活会话 → WS `session.interrupt` |
| 切换当前会话模型 | `POST /api/sessions/{id}/model` | WS `config.set`，明确指定会话范围 |

其中 `{id}` 表示**历史存储 ID**；后端负责转换为 WS 操作需要的运行时 ID。

## 3. 发送与恢复的具体顺序

```text
前端                     自研后端                      dashboard
 │ POST /api/chat           │                              │
 │ {text, 历史ID可选} ──────►│                              │
 │                          │ 确保已登录，持有 cookie       │
 │                          │ POST /api/auth/ws-ticket ───►│
 │                          │◄──────── ticket ─────────────│
 │                          │ WS /api/ws?ticket=... ──────►│
 │                          │                              │
 │                          │ 新对话：session.create ──────►│
 │                          │ 旧对话：session.resume ──────►│
 │                          │◄── 运行时ID、历史ID等 ────────│
 │◄── SSE：回传会话标识 ─────│                              │
 │                          │ prompt.submit                │
 │                          │ {运行时ID, text} ────────────►│
 │                          │◄── message.delta ────────────│
 │◄── SSE：文字增量 ─────────│                              │
 │                          │◄── message.complete ─────────│
 │◄── SSE：最终文本及状态 ───│                              │
```

已有可用连接和活会话时，复用连接，不必每轮重新取 ticket、恢复会话。

**两个关键点：**
- `session.create/resume` 等是 `/api/ws` 上的 **JSON-RPC 方法**，不是独立 HTTP 路径。
- 浏览历史只调用 REST；继续发送时才恢复会话，**不把页面里的全部历史重新塞进 prompt**。

现有发送入口可对照 `frontend/src/Chat.jsx:71` → `backend/main.py:61` → `backend/chat.py:128`；目前最后一步仍是每轮创建会话，首期就从这里改。
