# Hermes Web Console — 部署文档

> 版本：v0.5 ｜ 单镜像 = 前端静态产物 + FastAPI 后端。所有凭据运行时经环境变量注入，**镜像内零秘密**。
>
> **给 Hermes 提交代码修改是禁止的**——本文所有操作只发生在控制台仓库与编排层。

## 1. 部署形态一览

仓库根目录有三份 compose（**拉已发布镜像 / 建镜像**那套）；`hermes-deploy/` 目录下另有三份（**带智能问数、全部本地构建**，见 §10）：

| 文件 | 场景 | 说明 |
|---|---|---|
| `docker-compose.yml` | 单跑控制台（部署机） | 拉**已发布镜像**，`HERMES_BASE` 指向已有 dashboard |
| `docker-compose.build.yml` | 从源码构建镜像（开发机/发版机） | `build` / `push` / `up`；镜像名与上者一致 |
| `docker-compose.with-hermes.yml` | 与 Hermes Agent **一体部署** | 同机起 `gateway` + `console`，host 网络直达 |

三者镜像名统一为 `ghcr.io/yeeqen00111/hermes-web-console:${TAG:-latest}`。

## 2. 前置要求

- Docker + Docker Compose v2
- 一个可用的 Hermes dashboard（地址 + basic 账号密码）
- 控制台默认无登录（`APP_AUTH` 不设）；**绑回环、勿直接公网暴露**

## 3. 配置 `.env`

复制 `.env.example` 为同目录 `.env` 并填写（`.env` 已被 `.gitignore` 与 `.dockerignore` 排除）：

| 变量 | 必填 | 说明 |
|---|---|---|
| `HERMES_BASE` | 是 | dashboard 地址。本机/远程 `http://<服务器IP>:8426`；**Linux 一体部署**（host 网络）用 `http://127.0.0.1:9119`；**Docker Desktop**（容器 bridge 网络）必须用 `http://host.docker.internal:9119` —— 容器里的 `127.0.0.1` 是容器自己，连不上宿主发布的端口 |
| `HERMES_USER` | 是 | dashboard basic 账号（默认 `admin`） |
| `HERMES_PASS` | 是 | dashboard basic 密码（compose 用 `:?` 强制，未设则拒绝启动） |
| `TAG` | 否 | 镜像标签，默认 `latest` |
| `PORT` | 否 | 宿主端口，默认 `8000` |
| `HERMES_UID` / `HERMES_GID` | 一体部署 | 对齐宿主 `~/.hermes` 属主（Linux：`$(id -u)` / `$(id -g)`） |
| `API_SERVER_KEY` | 一体部署 | 启用 OpenAI 兼容 API server 时的 key；不用则注释 compose 里那三行 |
| `APP_TOKEN` | 否 | 前端令牌；不设则每次后端启动随机生成 |
| `APP_USER` / `APP_PASS` / `APP_AUTH` | 否 | `/api/login` 前端账号；`APP_AUTH=1` 才启用校验（默认放行） |

## 4. 场景 A：单跑控制台（用已发布镜像）

```bash
# 同目录放好 .env
docker compose up -d
# 访问 http://<部署机>:8000
```

- 端口：`${PORT:-8000}:8000`
- 卷：`console-data:/data`，SQLite 落在此（容器重建不丢）；由 `HIDDEN_DB_PATH=/data/hidden_models.db` 指定
- `HERMES_BASE` 指向**已存在**的 dashboard

## 5. 场景 B：从源码构建镜像

```bash
docker compose -f docker-compose.build.yml build          # 构建
docker compose -f docker-compose.build.yml push           # 推 ghcr（需 write:packages 的 PAT）
docker compose -f docker-compose.build.yml up -d          # 本机直接跑
# 换标签：TAG=v0.3（或写进 .env）
```

构建过程（`Dockerfile`，两阶段）：
1. `node:24-alpine` 里 `npm ci && npm run build` → 纯静态 `dist/`
2. `python:3.10-slim` 装 `backend/requirements.txt`，拷 `backend/`，把 `dist/` 拷到 `/app/static`

`backend/main.py` 发现同级 `static/` 存在即挂到 `/`，`/api/*` 优先 → 容器内前端与 API **同源**，无需 Vite 代理/CORS。

## 6. 场景 C：与 Hermes Agent 一体部署

```bash
docker compose -f docker-compose.with-hermes.yml up -d
# Web Console: http://<host>:8000   Hermes dashboard: http://<host>:9119
```

- 两个服务均 `network_mode: host`（`ports` 失效，端口由进程直接监听）：控制台用 `127.0.0.1:9119` 直达 dashboard，不绕公网代理
- ⚠️ **Docker Desktop（Windows/Mac）上不要用本文件**：这里的 `host` 指 WSL2/VM 而不是宿主，`8000`/`9119` 都不会转发到 Windows。改用两个独立 compose（各自 bridge + `ports`），`HERMES_BASE=http://host.docker.internal:9119`
- 账号只有一组：`HERMES_USER`/`HERMES_PASS` 同时喂 dashboard basic auth 和控制台登录
- `gateway` 健康探针探 gateway control socket（container up ≠ gateway 活着）；`console` 依赖其 healthy
- ⚠️ **若本机 Hermes 已在跑（容器名 `hermes`），本文件会重建它**：生成中的会话中断、飞书等渠道断线十几秒。只想加控制台，用场景 A

## 7. 持久化与数据

| 数据 | 位置 | 说明 |
|---|---|---|
| 隐藏/自定义模型条目 | 卷 `console-data` → `/data/hidden_models.db` | 后端 SQLite；容器重建不丢 |
| Agent 身份文件（SOUL/MEMORY/USER） | Hermes 侧 `~/.hermes`（容器内 `/opt/data`） | 一体部署由 `gateway` 卷管理 |

- **勿把 SQLite 单文件直接挂进容器**：宿主上文件不存在时 Docker 会把它建成**目录**，sqlite 打不开。故用 `HIDDEN_DB_PATH` 指向卷内路径

## 8. 运维注意

- **飞书渠道配置不热生效**：保存后点页面「重启网关生效」（后端转发 `POST /api/gateway/restart`，单容器实测约 15–40s 回落再起来），或服务器 `docker restart hermes`
- **身份文件保存**：本进程首次写前自动触发一次整机备份（异步），备份失败即拒绝写入；乐观锁 409 需重新加载后再存（详见 `API.md` §5）
- **dashboard 重启 = 所有会话失效**（未配固定 secret 时），后端会在下次请求自动重登

## 9. 本机开发启动

```bash
# 后端（backend/ 目录，conda py310）
python -m uvicorn main:app --reload --port 8000
# 前端（frontend/ 目录）
npm install && npm run dev        # http://localhost:5173，/api 代理到 8000
```

依赖：后端 `fastapi uvicorn requests websockets PyYAML cryptography`（见 `backend/requirements.txt`）；前端 `npm install`。细节见 `README.md` 与 `API.md`。

---

## 10. 场景 D：带智能问数（`hermes-deploy/`，全部本地构建）

> v0.5 新增。控制台 + hermes-mcp（问数 MCP 服务）一起部署，**镜像全部从本仓库源码构建**（不依赖 ghcr）。
> 三份 compose 按需选一个，都在 `hermes-deploy/` 目录下：

| 文件 | 起什么 | 适用 |
|---|---|---|
| `docker-compose.mcp.yml` | `console` + `hermes-mcp`（**不含 Hermes**） | 只调「问数」：控制台能开，其他 tab 连不上 dashboard 是预期 |
| `docker-compose.with-hermes.yml` | `gateway` + `console` + `hermes-mcp` | Linux / 服务器一体部署（bridge 网络，非 host） |
| `docker-compose.win-all.yml` | 同上，**Windows / Docker Desktop 专用** | bridge + ports；数据落 `D:\WSL\docker\Volumes\`；不挂 `/opt/agentops` |

### 10.1 启动

```bash
cd hermes-deploy
# 先让位（旧容器占 9119 / 8000；宿主 8000 若被本机开发后端占用也要让开）
docker stop hermes hermes-web-console 2>/dev/null

# Windows / Docker Desktop：
docker compose -f docker-compose.win-all.yml up -d --build
# Linux 服务器：
docker compose -f docker-compose.with-hermes.yml up -d --build
# 只调问数（不起 Hermes）：
docker compose -f docker-compose.mcp.yml up -d --build
```

首次构建要拉基础镜像 + `npm ci` + `pip install`，**几分钟起**。访问：控制台 `http://localhost:8000`、dashboard `http://localhost:9119`。

### 10.2 两份 env 的分工（`hermes-deploy/`）

| 文件 | 给谁 | `HERMES_BASE` |
|---|---|---|
| `.env` | full stack（with-hermes / win-all）的 `env_file` | `http://gateway:9119` |
| `.env.mcp` | 只有 mcp.yml 的 `env_file` | `http://127.0.0.1:8426`（那份没有 dashboard） |

- 两者模板一致（见 `.env.example`），差异只在 `HERMES_BASE`——**改共享值（密码 / key）时两个文件都要改**。
- 机制：compose 的 `${...}` 插值读同目录 `.env`；容器里拿到哪些变量由各服务的 `env_file:` 决定。别手动 `--env-file`。

必填键：`HERMES_USER` / `HERMES_PASS`（dashboard basic + 控制台共用一组）、`HERMESMCP_DB_KEY`（**只有 hermes-mcp 用**：数据源密码的加密 key）。生产建议新生成 key，不要沿用开发期那把。

`HERMESMCP_URL`（控制台连问数服务的地址）**不用填**——三份 compose 的 `environment:` 里已按服务名给死 `http://hermes-mcp:9123/mcp`（`environment` 覆盖 `env_file`）。

### 10.3 控制台 ↔ hermes-mcp：走 MCP（**2026-10-20 改**）

```
console ──MCP(streamable HTTP)──▶ http://hermes-mcp:9123/mcp ──▶ 数据源
                                                    （hermes-mcp 是数据的唯一主人）
```

- **旧的"共读共享 SQLite"通道已废弃**：那要求两端逐字对齐表结构与 Fernet 格式，任一侧改了另一侧就静默坏掉（症状：密码 `InvalidToken`，只能重填）。现在控制台只通过 **MCP 工具**访问，**不碰任何文件**。
- `./shared/sqlite`（Windows 上是 `D:\WSL\docker\Volumes\hermes-shared`）现在**只有 hermes-mcp 挂**；控制台不再挂它，也不再读 `HERMESMCP_DB_KEY`。
- 地址必须是**服务名** `hermes-mcp`——同 network 内由 Docker DNS 解析。`localhost` 是控制台容器自己；宿主 IP 也不通（mcp 没往宿主映射端口）。
- 挂**目录**不挂单文件（SQLite 的 `-wal`/`-shm` 要同目录）——这条现在只对 hermes-mcp 自己有约束。

### 10.4 问数上线四步

1. `up -d --build` 起 stack（见 10.1）。
2. 打开控制台 → **问数 → MCP 配置 → ＋ 添加 MCP 服务**：名称 `sql-coustm`（与 `.env` 的 `MCP_SERVER_NAME` 一致），地址 **`http://hermes-mcp:9123/mcp`**。
   - host 必须是 **service 名**——同 network 内 Docker DNS 解析；`localhost` 是 Hermes 容器自己，宿主 IP 也不通（mcp 没往宿主映射端口）。
3. **问数 → 数据源 → ＋ 添加数据源**：加一条（先选类型，字段跟着类型变；`sqlite` 只填文件路径）。
4. 对话里直接问数。

### 10.5 构建网络（国内环境）

| 环节 | 走哪 | 在哪配置 |
|---|---|---|
| 基础镜像（`python:3.10-slim` / `node:24-alpine` / `hermes-agent`） | daocloud 加速 | `~/.docker/daemon.json` 的 `registry-mirrors` |
| `npm ci` | `registry.npmmirror.com` | `package-lock.json` 里写死的 resolved |
| `pip install` | 清华源 | 两个 Dockerfile 的 `PIP_INDEX_URL` ARG（`--build-arg` 可换回官方） |

### 10.6 已知坑（都踩过）

- **控制台镜像要装 `mcp`（MCP SDK）**：`backend/requirements.txt` 里已列。缺了**容器启动就崩**（`dbconn` → `mcp_client` → `import mcp` 抛 `ModuleNotFoundError`），而且**本地开发看不出来**（conda py310 里早就装了）。改依赖后必须 `up -d --build` 重建镜像。
- **控制台启动会慢一点**：MCP SDK 的 import 链不轻（空闲机亚秒级，机器负载高时能到数秒）。
- **hermes-mcp 的镜像要显式装 `uvicorn`**（`run_http.py` 要它，`pyproject.toml` 没声明）——已在 `hermes-mcp/Dockerfile` 补齐。
- **`python:3.10-slim` 没有 `curl`**：两个健康检查都用纯 Python socket 探测，别改回 curl。
- **两个 stack 别同时起**：共用 `console-data` 卷（项目名相同）。一个一个来。
- **mcp 服务没起 / dashboard 没起时**，"测试"必然失败——那是环境问题不是代码问题。
