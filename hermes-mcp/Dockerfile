# syntax=docker/dockerfile:1
# hermes-mcp —— 只读问数 MCP 服务（HTTP 传输）2026-10-12
#
# 单一运行镜像：装依赖 + 源码。没有前端、没有构建期秘密——
# HERMESMCP_DB_KEY / HERMESMCP_LLM_* 一律运行时由 compose 注入（.dockerignore 已挡掉 .env 和 *.db）。
#
# 对外形态：**HTTP(streamable) MCP 服务**，端点 `http://<service_name>:9123/mcp`。
# 在 Hermes 上注册时填 `http://hermes-mcp:9123/mcp`（同 network 内按 service 名解析）。
#
# 构建上下文是 hermes-mcp/ 目录本身（compose 里 build.context: ../hermes-mcp），
# 所以用的是同目录的 .dockerignore。

FROM python:3.10-slim
WORKDIR /app

# 先只拷贝打包元数据再装依赖 → 源码改动不会让依赖层失效
COPY pyproject.toml README.md ./
COPY src/ ./src/
# [mysql] 是 pyproject 里现成的 extra（装 pymysql）；基础依赖含 mcp/SQLAlchemy/httpx
# 依赖源：国内构建机直连 pypi.org 很慢甚至超时，默认走清华镜像（全量 PyPI 镜像）。
# 要换回官方源：构建时传 --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
# uvicorn 必须显式装：scripts/run_http.py 要它，但 pyproject 的 dependencies 里没有
# （本机 py310 环境是别处装的，容器里没有就会 ImportError）。这里补装、不动仓库文件。
RUN pip install --no-cache-dir --index-url="$PIP_INDEX_URL" ".[mysql]" uvicorn

COPY scripts/ ./scripts/

ENV PYTHONUNBUFFERED=1
EXPOSE 9123

# 与开发期同一条命令；host/port/allowed_hosts 都来自环境变量
CMD ["python", "scripts/run_http.py"]
