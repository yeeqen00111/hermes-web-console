# -*- coding: utf-8 -*-
"""hermes-mcp 的 MCP 客户端 —— 控制台访问问数服务的**唯一出口**。

为什么不再直读共享 SQLite（2026-10-20 改造）：
    旧方案是控制台和 hermes-mcp 共读同一个 SQLite 文件，代价是两端必须**逐字对齐**
    「表结构 + 密码加密格式」——任何一侧改了，另一侧就静默坏掉（典型症状：
    密码 InvalidToken，只能重填）。改走 MCP 之后，**MCP 是数据的唯一主人**，
    加密、只读校验、类型白名单都只在它那边有一份，控制台不再重复实现。

本模块的每条事实都来自 2026-10-20 的实测（探针脚本留在 %TEMP%/mcp-probe/，不在仓库里）：
  1. SDK 2.x 的 ``CallToolResult`` 字段是 **snake_case**：``is_error`` / ``structured_content``。
  2. 工具返回体是「**JSON 塞在 text 里**」，``structured_content`` 恒为 ``None`` → 必须自己 ``json.loads``。
  3. 失败有**两种**形态，都得处理：
       ① ``is_error=True``，正文只有一句通用文案（如 "Error executing tool query"）——**拿不到细节**
          （源码里明明抛了真实原因，被 MCP 框架层吞了；hermes-mcp 只读，改不了）
       ② ``is_error=False``，但正文里 ``ok=false``（如 remove_connection 删不存在的名字）
     本层只把形态①变成异常；形态②**交给调用方**判断——不同工具的 ok 语义不一样，
     在这里替它们决定就是替上游做决定。
  4. 每次调用开一个**短会话**：配置类操作是低频人工点击，不值得为此维护连接池/重连。

因此调用方要注意：**错误信息是"粗"的**（连不上 / 工具报错，仅此两类）。
给用户的可读报错要靠控制台自己的校验补，别指望 MCP 告诉你为什么失败。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from config import HERMESMCP_TIMEOUT, HERMESMCP_URL


class McpError(RuntimeError):
    """与问数服务交互失败：连不上、超时、协议异常，或工具执行报错。

    调用方统一 ``except McpError`` 即可，映射成 HTTP 502。
    """


def _text_of(result: Any) -> str:
    """把 CallToolResult 里的文本块拼起来（工具返回的 JSON 就在这儿）。"""
    parts = [
        getattr(block, "text", "") or ""
        for block in (getattr(result, "content", None) or [])
        if getattr(block, "type", None) == "text"
    ]
    return "\n".join(p for p in parts if p)


def _parse(result: Any, tool: str) -> Dict[str, Any]:
    """解析工具返回体。约定是 JSON；解析不出来说明契约变了，直接报错（别静默吞掉）。"""
    raw = _text_of(result).strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise McpError(f"问数服务返回的不是 JSON（工具 {tool}）：{raw[:300]}") from exc
    if not isinstance(parsed, dict):
        raise McpError(f"问数服务返回的不是对象（工具 {tool}）：{raw[:300]}")
    return parsed


async def _call_once(tool: str, arguments: Dict[str, Any]) -> Any:
    """开一个短会话，调一次工具，关掉。"""
    async with streamable_http_client(HERMESMCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool, arguments)


async def call_tool(
    tool: str,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """调用一个 MCP 工具，返回解析后的 dict。

    - 连不上 / 超时 / 协议异常 / 工具执行失败 → 抛 :class:`McpError`
    - 工具正常返回但业务上 ``ok=false``（如删不存在的连接）→ **正常返回**，由调用方判断
    """
    args = dict(arguments or {})
    limit = timeout if timeout is not None else HERMESMCP_TIMEOUT
    try:
        # py3.10 没有 asyncio.timeout()，用 wait_for（后端跑 conda py310 / python:3.10-slim）
        result = await asyncio.wait_for(_call_once(tool, args), timeout=limit)
    except asyncio.TimeoutError:
        raise McpError(f"调用问数服务超时（{limit:g}s）：{HERMESMCP_URL}") from None
    except McpError:
        raise
    except Exception as exc:  # 连接被拒、DNS 失败、握手异常……统一归成"连不上"
        raise McpError(f"连不上问数服务（{HERMESMCP_URL}）：{exc}") from exc

    if getattr(result, "is_error", False):
        detail = _text_of(result).strip() or "（无详情）"
        raise McpError(f"问数服务执行 {tool} 失败：{detail}")

    return _parse(result, tool)
