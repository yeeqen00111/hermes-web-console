# syntax=docker/dockerfile:1
# Hermes Web Console —— 单镜像（前端静态产物 + FastAPI 后端）2026-09-29
#
# 阶段1：Node 只存在于构建期（vite build 出纯静态文件），不进最终镜像
# 阶段2：Python 运行镜像 = 后端源码 + 前端 dist（挂到 /app/static，main.py 自动识别）
#
# 镜像里零秘密：HERMES_BASE/USER/PASS 一律运行时注入（docker-compose 读 .env），
# 公开推送到 ghcr.io 不会泄露任何配置（.dockerignore 已挡掉 .env / *.db / CLAUDE.md）。

FROM node:24-alpine AS web
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.10-slim
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
# backend/main.py 找的是「backend 的父目录下 static/」→ 即 /app/static
COPY --from=web /build/dist /app/static

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
# 容器里前端与 /api 同源（同一个 uvicorn），无需 Vite 代理
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
