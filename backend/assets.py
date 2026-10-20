# -*- coding: utf-8 -*-
"""智能问数 · 知识库 + SQL 模板（阶段 5，2026-10-20）

控制台只是 hermes-mcp 的 **MCP 客户端**——这两样数据都在 mcp 侧的 assets 库里
（`hermes_mcp_assets.db`），控制台不碰任何文件。

**过滤语义**（源码实测，决定前端过滤器怎么画）：
  - 不传 `connection` → 全部条目
  - 传某个数据源     → **该数据源的 + 全局（`'*'`）的**（全局始终包含）
所以前端过滤器只需要「全部 / 各数据源」，**不需要**单独的"全局"选项。

**本层要补的（MCP 不做的）**：
  1. `update_*` 对不存在的 id 会**抛异常且不给原因** → 先查一遍，才能给出 404。
  2. `create_sql_template` 会校验 SQL 只读、`params` 必须是 JSON——失败只有一句通用文案
     → 本地先拦，给出可读的 400。**权威判定仍在 MCP**，这里只是让它能被看懂。
  3. `delete_*` 删不存在返回 `{ok:false}`（不是错误）→ 404 由我们翻。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from config import require_app_token
from fastapi import APIRouter, Depends, HTTPException
from mcp_client import McpError, call_tool
from pydantic import BaseModel

knowledge_router = APIRouter(prefix="/api/knowledge-entries", tags=["knowledge"])
templates_router = APIRouter(prefix="/api/sql-templates", tags=["sql-templates"])

MAX_TEXT = 4000


async def _call(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """调 MCP，把连不上 / 工具报错统一翻成 502。"""
    try:
        return await call_tool(tool, args)
    except McpError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _text(value: Optional[str], field: str, *, required: bool, cap: int = MAX_TEXT) -> str:
    out = (value or "").strip()
    if required and not out:
        raise HTTPException(status_code=400, detail=f"{field}不能为空")
    if len(out) > cap:
        raise HTTPException(status_code=400, detail=f"{field}最多 {cap} 个字符")
    return out


# ─────────────────────────── 知识库 ───────────────────────────

class KnowledgeForm(BaseModel):
    """业务知识：术语 + 定义/口径。

    `connection` = 所属数据源名，或 **`'*'` = 全局**（默认）。全局条目对所有数据源都生效。
    """
    term: str = ""
    definition: str = ""
    connection: str = "*"
    anchor: str = ""
    note: str = ""


def _knowledge_args(form: KnowledgeForm) -> Dict[str, Any]:
    return {
        "term": _text(form.term, "术语", required=True),
        "definition": _text(form.definition, "定义/口径", required=True),
        "connection": (form.connection or "*").strip() or "*",
        "anchor": _text(form.anchor, "出处", required=False),
        "note": _text(form.note, "备注", required=False),
    }


async def _knowledge_ids(connection: str = "") -> set:
    args = {"connection": connection} if connection else {}
    data = await _call("list_knowledge_entries", args)
    return {e.get("id") for e in (data.get("entries") or [])}


@knowledge_router.get("", dependencies=[Depends(require_app_token)])
async def list_entries(connection: str = "") -> Dict[str, Any]:
    """列出知识条目。不传 `connection` = 全部；传了就返回**该数据源 + 全局**。"""
    args = {"connection": connection.strip()} if connection.strip() else {}
    data = await _call("list_knowledge_entries", args)
    entries: List[Dict[str, Any]] = data.get("entries") or []
    return {"entries": entries, "count": len(entries)}


@knowledge_router.get("/search", dependencies=[Depends(require_app_token)])
async def search_entries(connection: str = "", query: str = "", limit: int = 8) -> Dict[str, Any]:
    """「试一下」：输入一个问题，看会命中哪些口径（**这是验证知识库生效的唯一手段**）。

    检索是 mcp 侧的 FTS5 trigram 全文索引，对中文友好；`connection` 为空时按全局处理。
    """
    q = _text(query, "问题", required=True, cap=500)
    data = await _call("search_knowledge", {
        "connection": connection.strip() or "*",
        "query": q,
        "limit": max(1, min(int(limit), 50)),
    })
    hits = data.get("hits") or []
    return {"hits": hits, "count": len(hits)}


@knowledge_router.post("", dependencies=[Depends(require_app_token)])
async def create_entry(body: KnowledgeForm) -> Dict[str, Any]:
    data = await _call("create_knowledge_entry", _knowledge_args(body))
    return {"ok": True, "id": data.get("id")}


@knowledge_router.put("/{entry_id}", dependencies=[Depends(require_app_token)])
async def update_entry(entry_id: int, body: KnowledgeForm) -> Dict[str, Any]:
    """按 id 改。⚠️ MCP 对不存在的 id 抛异常且不给原因，所以先查一遍换 404。"""
    if entry_id not in await _knowledge_ids():
        raise HTTPException(status_code=404, detail=f"知识条目 {entry_id} 不存在")
    data = await _call("update_knowledge_entry", {"id": entry_id, **_knowledge_args(body)})
    return {"ok": True, "id": data.get("id", entry_id)}


@knowledge_router.delete("/{entry_id}", dependencies=[Depends(require_app_token)])
async def delete_entry(entry_id: int) -> Dict[str, Any]:
    data = await _call("delete_knowledge_entry", {"id": entry_id})
    if not data.get("ok"):
        raise HTTPException(status_code=404, detail=f"知识条目 {entry_id} 不存在")
    return {"ok": True, "id": entry_id}


# ─────────────────────────── SQL 模板 ───────────────────────────

class TemplateForm(BaseModel):
    """内置 SQL：用户说法 + 标准 SQL。

    `params` 是给**人看**的参数说明（JSON 对象字符串），例如 `{"year": "年份"}`。
    """
    question: str = ""
    sql: str = ""
    name: str = ""
    connection: str = "*"
    params: str = "{}"
    note: str = ""


_READONLY_HEADS = ("select", "with")


def _template_args(form: TemplateForm) -> Dict[str, Any]:
    sql = (form.sql or "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL 不能为空")
    # 只是**为了让人看懂**：MCP 的 validate_readonly 才是权威，但它报错只有通用文案。
    head = sql.lstrip("( \t\r\n").split(None, 1)[0].lower() if sql.strip() else ""
    if head not in _READONLY_HEADS:
        raise HTTPException(status_code=400, detail="SQL 模板必须是只读查询（以 SELECT 或 WITH 开头）")

    raw_params = (form.params or "{}").strip() or "{}"
    try:
        parsed = json.loads(raw_params)
    except ValueError as exc:
        raise HTTPException(status_code=400,
                            detail='参数说明必须是 JSON 对象，例如 {"year": "年份"}') from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="参数说明必须是 JSON 对象（不能是数组或标量）")

    return {
        "question": _text(form.question, "问题", required=True),
        "sql": sql,
        "name": _text(form.name, "名称", required=False),
        "connection": (form.connection or "*").strip() or "*",
        "params": json.dumps(parsed, ensure_ascii=False),
        "note": _text(form.note, "备注", required=False),
    }


async def _template_ids(connection: str = "") -> set:
    args = {"connection": connection} if connection else {}
    data = await _call("list_sql_templates", args)
    return {t.get("id") for t in (data.get("templates") or [])}


@templates_router.get("", dependencies=[Depends(require_app_token)])
async def list_templates(connection: str = "") -> Dict[str, Any]:
    """列出模板。不传 `connection` = 全部；传了就返回**该数据源 + 全局**。"""
    args = {"connection": connection.strip()} if connection.strip() else {}
    data = await _call("list_sql_templates", args)
    items: List[Dict[str, Any]] = data.get("templates") or []
    return {"templates": items, "count": len(items)}


@templates_router.post("", dependencies=[Depends(require_app_token)])
async def create_template(body: TemplateForm) -> Dict[str, Any]:
    data = await _call("create_sql_template", _template_args(body))
    return {"ok": True, "id": data.get("id")}


@templates_router.put("/{template_id}", dependencies=[Depends(require_app_token)])
async def update_template(template_id: int, body: TemplateForm) -> Dict[str, Any]:
    """按 id 改。⚠️ MCP 对不存在的 id 抛异常且不给原因，所以先查一遍换 404。"""
    if template_id not in await _template_ids():
        raise HTTPException(status_code=404, detail=f"SQL 模板 {template_id} 不存在")
    data = await _call("update_sql_template", {"id": template_id, **_template_args(body)})
    return {"ok": True, "id": data.get("id", template_id)}


@templates_router.delete("/{template_id}", dependencies=[Depends(require_app_token)])
async def delete_template(template_id: int) -> Dict[str, Any]:
    data = await _call("delete_sql_template", {"id": template_id})
    if not data.get("ok"):
        raise HTTPException(status_code=404, detail=f"SQL 模板 {template_id} 不存在")
    return {"ok": True, "id": template_id}
