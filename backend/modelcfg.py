# -*- coding: utf-8 -*-
"""
模型配置封装 —— 你的系统只管「自定义模型配置」，其他（内置 provider/moa/auxiliary）不暴露。
全部经 dashboard REST（custom-endpoints CRUD + model/set），不改 Hermes。

实测语义（2026-09-23）：
  • endpoint id 由服务端从 name 派生；编辑时把 GET 拿到的 id 原样放回 body.id
  • api_key 三态：传值=写新 key；空串=清除；不发=不动（key 存 .env，回读只有 preview）
  • upsert 带 make_default=true = 保存即设为默认
  • GET 返回带 current:{provider,model,base_url} = 当前默认
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import hc, require_app_token
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/model-configs", tags=["model-configs"])

# 转发到 dashboard 的字段白名单（CustomEndpointUpdate 的子集）
_UPSERT_FIELDS = (
    "id", "name", "base_url", "model", "api_key", "api_mode",
    "context_length", "discover_models", "models", "model_details", "make_default",
)


class ModelConfigBody(BaseModel):
    """新增/编辑一个模型配置。api_key 缺省=不动；空串=清除。"""
    id: Optional[str] = None
    name: str
    base_url: str
    model: str
    api_key: Optional[str] = None
    api_mode: Optional[str] = None          # ""|chat_completions|codex_responses|anthropic_messages
    context_length: Optional[int] = None
    discover_models: bool = True
    models: Optional[List[str]] = None
    model_details: Optional[List[Dict[str, Any]]] = None
    make_default: bool = False              # 保存后设为默认


class ValidateBody(BaseModel):
    name: str = "probe"
    base_url: str
    model: str
    api_key: Optional[str] = None
    api_mode: Optional[str] = None


def _guard(resp, what: str) -> Dict[str, Any]:
    if resp.status_code >= 400:
        raise HTTPException(status_code=502,
                            detail=f"{what} HTTP {resp.status_code}: {resp.text[:400]}")
    return resp.json()


@router.get("", dependencies=[Depends(require_app_token)])
def list_model_configs():
    """列表 + 当前默认（前端摘要用）。"""
    data = _guard(hc.request("GET", "/api/providers/custom-endpoints"), "list")
    return {"configs": data.get("endpoints") or [], "current": data.get("current") or {}}


@router.post("", dependencies=[Depends(require_app_token)])
def upsert_model_config(body: ModelConfigBody):
    """新增/编辑（upsert）。api_key 缺省=不动，空串=清除。"""
    payload = {k: getattr(body, k) for k in _UPSERT_FIELDS if getattr(body, k) is not None}
    data = _guard(hc.request("POST", "/api/providers/custom-endpoints",
                             json=payload), "upsert")
    # dashboard 返回 endpoint_id 或完整 entry；统一带 id 回给前端
    out = {"ok": True, "endpoint_id": data if isinstance(data, str) else
           (data or {}).get("endpoint_id") or (data or {}).get("id") or body.id or body.name}
    return out


@router.post("/validate", dependencies=[Depends(require_app_token)])
def validate_model_config(body: ValidateBody):
    """表单「测试连接」：结果在 body（HTTP 200），ok=false 时 message 给原因。"""
    payload = {k: getattr(body, k) for k in
               ("name", "base_url", "model", "api_key", "api_mode")
               if getattr(body, k) is not None}
    return _guard(hc.request("POST", "/api/providers/custom-endpoints/validate",
                             json=payload), "validate")


@router.post("/{endpoint_id}/activate", dependencies=[Depends(require_app_token)])
def activate_model_config(endpoint_id: str):
    return _guard(hc.request(
        "POST", f"/api/providers/custom-endpoints/{endpoint_id}/activate"), "activate")


@router.post("/{endpoint_id}/default", dependencies=[Depends(require_app_token)])
def set_default_model(endpoint_id: str, model: str):
    """设为默认模型（不编辑配置本身）。provider 形态以 GET 列表的 id 为准。"""
    body = {"scope": "main", "provider": endpoint_id, "model": model}
    data = _guard(hc.request("POST", "/api/model/set", json=body), "model/set")
    # 昂贵模型需二次确认时透传 confirm_required
    if isinstance(data, dict) and data.get("confirm_required"):
        return {"ok": False, "confirm_required": True,
                "confirm_message": data.get("confirm_message", "")}
    return {"ok": True}


@router.delete("/{endpoint_id}", dependencies=[Depends(require_app_token)])
def delete_model_config(endpoint_id: str):
    return _guard(hc.request(
        "DELETE", f"/api/providers/custom-endpoints/{endpoint_id}"), "delete")