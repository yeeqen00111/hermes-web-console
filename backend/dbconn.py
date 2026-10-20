# -*- coding: utf-8 -*-
"""智能问数 · 数据源配置（2026-10-12 落地；**2026-10-20 起改走 MCP**）

**本模块只管写配置，不连数据库。**

    控制台后端 ──MCP(streamable HTTP)──▶ hermes-mcp ──▶ 数据库

**MCP 是数据的唯一主人**：密码加密、只读校验都在它那边有一份，本层不重复实现。
旧通道（控制台与 hermes-mcp 共读同一个 SQLite 文件）已废弃——它要求两端**逐字对齐**
表结构与 Fernet 格式，任何一侧改了另一侧就静默坏掉（症状：密码 `InvalidToken`，只能重填）。

本层只做 **MCP 不做的三件事**：
  1. **完整性校验**——MCP 的 `register_connection` 只强求 `name` + `type`，缺字段照样
     `ok:true`，要等有人用它才炸；而且炸了也**不给原因**（只回一句 `Error executing tool ...`）。
     所以这里是**唯一能给用户可读报错**的地方。类型规则见 `conn_types.py`。
  2. **HTTP 语义**——MCP 删不存在的连接返回 `ok:false`（**不是**错误），404 / 409 得我们翻。
  3. **类型元数据**——MCP 既不校验类型，也没有"列出类型"的工具。

路由（路径里只有 `{name}` 占位符，不写死任何值——教训 /api/channels/feishu/restart）：
    GET    /api/db-connections          列出全部
    GET    /api/db-connections/types    类型目录（前端表单 + 本层校验共用一份）
    POST   /api/db-connections          新增（重名 409）
    PUT    /api/db-connections/{name}   覆盖一条已存在的（不存在 404；**不支持改名**）
    DELETE /api/db-connections/{name}   删除（不存在 404）
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Set, Tuple

from config import require_app_token
from conn_types import catalog, get_type
from fastapi import APIRouter, Depends, HTTPException
from mcp_client import McpError, call_tool
from pydantic import BaseModel

router = APIRouter(prefix="/api/db-connections", tags=["db-connections"])

MAX_ROWS_HARD_CAP = 1000
DEFAULT_MAX_ROWS = 200

_NAME_RE = re.compile(r"^[A-Za-z0-9_一-龥-]{1,64}$")
_HOST_RE = re.compile(r"^[^\s/\\]{1,255}$")      # 主机名或 IP：不许空白、不许路径分隔符


class ConnectionForm(BaseModel):
    """新增/编辑表单。

    - `name`：`POST` 必填；`PUT /{name}` 忽略它（身份在路径里）
    - `read_only`：**服务端恒置 true**，传什么都一样（本层不提供改写开关）
    - `password` **三态**（与模型配置的 api_key 一致，**与 Hermes 渠道的 env 语义不同**）：
      不发该字段 = 保持原值 ｜ `""` = 清除 ｜ 非空 = 换新（由 MCP 加密入库）
    """
    name: str = ""
    type: str = ""
    host: str = ""
    port: Optional[int] = None
    database: str = ""
    username: str = ""
    read_only: bool = True
    max_rows: Optional[int] = None
    password: Optional[str] = None


# ── 参数校验（MCP 不做的那部分）──

def _clean_name(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="名称只能用中英文、数字、下划线、连字符（1–64 位）")
    return name


def _clean(form: ConnectionForm) -> Tuple[Dict[str, Any], str]:
    """按类型校验并归一化，返回 `(字段, 类型分组)`。

    **类型决定要填哪些字段**——这是 MCP 完全不管的部分：
      关系库 → host / port / database / username 都要
      sqlite → **只要 database（文件路径）**，host/port/username 一律清空
    """
    kind = (form.type or "").strip().lower()
    meta = get_type(kind)
    if meta is None:
        available = "、".join(t["label"] for t in catalog()["types"])
        raise HTTPException(status_code=400,
                            detail=f"不支持的数据源类型：{kind or '(空)'}（可用：{available}）")

    database = (form.database or "").strip()
    if not database:
        what = "文件路径" if meta["group"] == "file" else "库名（Oracle 填服务名）"
        raise HTTPException(status_code=400, detail=f"{what}不能为空")

    max_rows = form.max_rows if form.max_rows is not None else DEFAULT_MAX_ROWS
    if not isinstance(max_rows, int) or not (1 <= max_rows <= MAX_ROWS_HARD_CAP):
        raise HTTPException(status_code=400, detail=f"行数上限需在 1–{MAX_ROWS_HARD_CAP} 之间")

    # read_only 恒为 true：本层只写只读配置，不提供改写开关
    fields: Dict[str, Any] = {"type": kind, "database": database,
                              "max_rows": max_rows, "read_only": True}

    if meta["group"] == "file":
        # sqlite：只要路径。显式清空另外三个，免得残留上一次填的值
        fields.update(host="", port=None, username="")
        return fields, meta["group"]

    host = (form.host or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="主机不能为空")
    if not _HOST_RE.match(host):
        raise HTTPException(status_code=400, detail="主机名不合法")

    # 注意 `is not None`：端口 0 是「没填」，该走默认值，不能原样透传
    port = form.port if form.port is not None else meta["default_port"]
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise HTTPException(status_code=400, detail="端口需在 1–65535 之间")

    username = (form.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="账号不能为空（请用只有 SELECT 权限的账号）")

    fields.update(host=host, port=port, username=username)
    return fields, meta["group"]


# ── MCP 调用 ──

async def _call(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """调 MCP，把连不上 / 工具报错统一翻成 502。"""
    try:
        return await call_tool(tool, args)
    except McpError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _existing_names() -> Set[str]:
    """当前已注册的名字集合——用来自己判 404 / 409（MCP 只会 upsert，不替你拦）。"""
    data = await _call("list_connections", {})
    return {c.get("name") for c in (data.get("connections") or []) if c.get("name")}


async def _register(name: str, fields: Dict[str, Any], password: Optional[str]) -> None:
    """写入 MCP。密码**三态照原样映射**（MCP 的语义与我们完全一致）：
    不发 = 保持 ｜ `""` = 清除 ｜ 有值 = 加密存。"""
    args: Dict[str, Any] = {"name": name, **fields}
    if password is not None:
        args["password"] = password
    res = await _call("register_connection", args)
    if not res.get("ok", True):
        raise HTTPException(status_code=502, detail=f"问数服务未能保存「{name}」")


def _conn_out(rec: Dict[str, Any]) -> Dict[str, Any]:
    """把 MCP 返回的连接记录映射成对外形状。

    - **永不回显密码**：MCP 只给 `has_password` 布尔，照传。
    - ⚠️ MCP 的 `list_connections` **不返回** `id` / `created_at` / `updated_at`
      （见其 `describe_connection`），所以这三个字段从对外契约里去掉了。
    - `status` / `last_error` **原样透传**：MCP 侧目前**恒为 null**（`set_status` 无调用点），
      这里不加工也不隐藏——将来它接上了，前端加个徽标就能用，后端一行不用改。
    """
    return {
        "name": rec.get("name") or "",
        "type": rec.get("type") or "",
        "host": rec.get("host") or "",
        "port": rec.get("port"),
        "database": rec.get("database") or "",
        "username": rec.get("username") or "",
        "read_only": bool(rec.get("read_only", True)),
        "max_rows": rec.get("max_rows") or DEFAULT_MAX_ROWS,
        "has_password": bool(rec.get("has_password")),
        "status": rec.get("status"),
        "last_error": rec.get("last_error"),
    }


# ── 端点 ──

@router.get("", dependencies=[Depends(require_app_token)])
async def list_connections() -> Dict[str, Any]:
    """列出全部数据源（走 MCP）。"""
    data = await _call("list_connections", {})
    out = [_conn_out(c) for c in (data.get("connections") or [])]
    return {"connections": out, "count": len(out)}


@router.get("/types", dependencies=[Depends(require_app_token)])
async def list_types() -> Dict[str, Any]:
    """数据源类型目录：前端拿它渲染表单，本层拿它做校验——**一份事实两处用**。"""
    return catalog()


@router.post("", dependencies=[Depends(require_app_token)])
async def create_connection(body: ConnectionForm) -> Dict[str, Any]:
    """新增一条。**重名 409**——MCP 那边是 upsert，不会替你拦。"""
    name = _clean_name(body.name)
    fields, group = _clean(body)
    if group == "relational" and not body.password:
        raise HTTPException(status_code=400, detail="首次保存必须填写密码")
    if name in await _existing_names():
        raise HTTPException(status_code=409, detail=f"名称「{name}」已存在")
    await _register(name, fields, body.password)
    return {"ok": True, "name": name}


@router.put("/{name}", dependencies=[Depends(require_app_token)])
async def update_connection(name: str, body: ConnectionForm) -> Dict[str, Any]:
    """覆盖一条**已存在**的（不存在 404）。

    **不支持改名**：MCP 按 name upsert，所谓"改名"实际是删旧的 + 建新的，
    中途失败会留下空档——和当初拒绝"改 MCP 地址"是同一条理由。要改名就删了重建。
    """
    name = _clean_name(name)
    fields, _ = _clean(body)
    if name not in await _existing_names():
        raise HTTPException(status_code=404, detail=f"数据源「{name}」不存在")
    await _register(name, fields, body.password)
    return {"ok": True, "name": name}


@router.delete("/{name}", dependencies=[Depends(require_app_token)])
async def delete_connection(name: str) -> Dict[str, Any]:
    """删除一条。⚠️ MCP 删不存在的返回 `ok:false`（不是错误），404 得我们翻。"""
    name = _clean_name(name)
    res = await _call("remove_connection", {"name": name})
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail=f"数据源「{name}」不存在")
    return {"ok": True, "name": name}
