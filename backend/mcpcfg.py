# -*- coding: utf-8 -*-
"""
智能问数 · MCP 服务配置（2026-10-12 落地）

hermes-mcp 是**独立服务**，通过 HTTP 注册到 Hermes —— 就是 `config.yaml` 里的一条：

    mcp_servers:
      <服务名>:
        url: http://<host>:9123/mcp

本模块只做一件事：**代理 Hermes dashboard 的 /api/mcp/* 接口**，
让页面能看见、能测、能启停这条注册记录。这里**不连 hermes-mcp、不 import 它**，
也不运行它；"测试"是让 Hermes 去连，不是我们连。

路径约定（教训：/api/channels/feishu/restart）
=============================================
  - 前缀用连字符 `/api/mcp-servers`：上游是 `/api/mcp/servers`，前端与后端同源，
    照抄会撞上。
  - 路径里只放**占位符** `{name}`，绝不出现 `sql-coustm` 这种具体值；
    服务名走配置项（`config.MCP_SERVER_NAME`），改名只改 .env。
  - 不做"针对某个具体服务"的专有动作，只有 list / test / enabled 三个通用动作。

一期不改地址：上游没有"改单个 server"的接口，改地址只能删除后重建，
而重建要重填 bearer token（token 存在 Hermes 的 .env 里，列表接口不返回）。
那一版单独做，见 API.md。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from config import MCP_SERVER_NAME, hc, require_app_token
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/mcp-servers", tags=["mcp-servers"])


class EnabledToggle(BaseModel):
    """上游 MCPEnabledToggle 是 `{enabled, profile?}` 对象（不是裸布尔）。"""
    enabled: bool = True


class ServerForm(BaseModel):
    """新增一条注册的表单。**只有名称和地址**——鉴权不做（`auth` 固定发 "none"）。
    多一个可选字段就多一处要解释的东西；等真需要鉴权再一起加。"""
    name: str = ""
    url: str = ""


def _upstream_error(status: int, name: str = "") -> HTTPException:
    """上游错误统一映射：只带状态码与固定文案，绝不回显上游响应体。"""
    if status == 404:
        return HTTPException(status_code=404, detail=f"Hermes 上没有名为「{name}」的 MCP 服务")
    if status == 400:
        return HTTPException(status_code=400, detail="Hermes 拒绝了本次 MCP 配置请求")
    if status == 401 or status == 403:
        return HTTPException(status_code=502, detail="Hermes 登录失效，无法读取 MCP 配置")
    return HTTPException(status_code=502, detail=f"Hermes MCP 接口请求失败（HTTP {status}）")


def _ask(method: str, path: str, **kw):
    """转发给 dashboard。**连不上 dashboard 要给人话，不能抛裸 500**（别的模块没做这层，这里补上）。
    异常原文可能含地址与账号，一律不回显。"""
    import requests
    try:
        return hc.request(method, path, **kw)
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="连不上 Hermes dashboard（请检查后端 HERMES_BASE 与网络）")


@router.get("", dependencies=[Depends(require_app_token)])
async def list_servers() -> Dict[str, Any]:
    """列出 Hermes 上注册的全部 MCP 服务（含非问数的），并标出问数那条。

    用 MCP_SERVER_NAME 匹配，配错了页面能立刻看出来。
    """
    resp = _ask("GET", "/api/mcp/servers")
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code)
    try:
        data = resp.json()
        servers: List[Dict[str, Any]] = data.get("servers") or []
    except Exception:
        raise HTTPException(status_code=502, detail="Hermes MCP 列表返回格式异常")

    names = {s.get("name") for s in servers if isinstance(s, dict)}
    return {
        "servers": servers,
        "configured_name": MCP_SERVER_NAME,
        "configured_found": MCP_SERVER_NAME in names,
    }


@router.post("/{name}/test", dependencies=[Depends(require_app_token)])
async def test_server(name: str) -> Dict[str, Any]:
    """让 **Hermes** 去连一次并列出工具（连不上时上游返回 `ok:false` + 已脱敏的 error）。

    这一条同时验证三件事：网络能不能通、Host 白名单对不对、token 对不对——
    返回值里还带工具清单，页面据此检查有没有暴露写工具。
    """
    resp = _ask("POST", f"/api/mcp/servers/{name}/test")
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code, name)
    return resp.json()


@router.put("/{name}/enabled", dependencies=[Depends(require_app_token)])
async def set_enabled(name: str, body: EnabledToggle) -> Dict[str, Any]:
    """启停。停用不删除，配置留着，随时能再开回来。"""
    resp = _ask("PUT", f"/api/mcp/servers/{name}/enabled", json={"enabled": body.enabled})
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code, name)
    return resp.json()


# ── 新增 / 删除（2026-10-12 加；改地址已去掉，2026-10-13）────────────────
#
# 只做「建一条」「删一条」两件确定的事。改地址不做——上游没有「修改某一条」的接口，
# 硬做只能「先删后建」，中途失败会出现「删了但没建上」的空档，不值得。
# 要换地址就在页面上删掉再添加。

# 名字会被拼进上游 URL 的路径段，限死字符集（上游对注入形态的配置会直接 400）
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _clean_form(body: ServerForm) -> tuple[str, str]:
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="名称只能用字母、数字、下划线、连字符（1–64 位，字母或数字开头）")

    url = (body.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="地址不能为空")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="地址必须以 http:// 或 https:// 开头")
    if len(url) > 500 or any(c.isspace() for c in url):
        raise HTTPException(status_code=400, detail="地址不合法")
    return name, url


def _names_on_dashboard() -> set:
    """当前 dashboard 上已有的服务名（用于「重名/不存在」的本地预判，省一次上游写调用）。"""
    resp = _ask("GET", "/api/mcp/servers")
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code)
    try:
        servers = resp.json().get("servers") or []
    except Exception:
        raise HTTPException(status_code=502, detail="Hermes MCP 列表返回格式异常")
    return {s.get("name") for s in servers if isinstance(s, dict)}


@router.post("", dependencies=[Depends(require_app_token)])
async def create_server(body: ServerForm) -> Dict[str, Any]:
    """新增一条 MCP 注册。不带鉴权（auth 固定 none）。"""
    name, url = _clean_form(body)
    if name in _names_on_dashboard():
        raise HTTPException(status_code=409, detail=f"Hermes 上已经有名叫「{name}」的 MCP 服务")
    resp = _ask("POST", "/api/mcp/servers", json={"name": name, "url": url, "auth": "none"})
    if resp.status_code == 409:
        raise HTTPException(status_code=409, detail=f"Hermes 上已经有名叫「{name}」的 MCP 服务")
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code, name)
    return {"ok": True, "name": name, "url": url}


@router.delete("/{name}", dependencies=[Depends(require_app_token)])
async def remove_server(name: str) -> Dict[str, Any]:
    """删除一条注册（Hermes 那边也删掉，不是停用）。"""
    if name not in _names_on_dashboard():
        raise HTTPException(status_code=404, detail=f"Hermes 上没有名为「{name}」的 MCP 服务")
    resp = _ask("DELETE", f"/api/mcp/servers/{name}")
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code, name)
    return {"ok": True, "name": name}
