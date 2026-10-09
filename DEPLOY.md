# Hermes Web Console — 部署文档

> 版本：v0.3.x ｜ 单镜像 = 前端静态产物 + FastAPI 后端。所有凭据运行时经环境变量注入，**镜像内零秘密**。

## 1. 部署形态一览

仓库根目录有三个 compose 文件，对应三种场景，**按需选一个**：

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
| `HERMES_BASE` | 是 | dashboard 地址。本机/远程 `http://<服务器IP>:8426`；一体部署用 `http://127.0.0.1:9119` |
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

依赖：后端 `fastapi uvicorn requests websockets PyYAML`；前端 `npm install`。细节见 `README.md` 与 `API.md`。
