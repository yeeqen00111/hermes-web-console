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
def list_model_configs(refresh: bool = False):
    """两接口合并（2026-09-23 定案）：
    - /api/model/options 的 custom:* providers = 主源（厂商+模型全集+is_current），实测覆盖 legacy custom_providers 段（商汤/火山都在）
    - /api/providers/custom-endpoints = 补充管理信息（has_api_key/preview），按 host 匹配
    refresh=true 打破 picker 的 1h 缓存——删除/保存等写操作后的刷新必须带，否则界面显示旧数据。
    """
    options = _guard(hc.request("GET", f"/api/model/options?refresh={'true' if refresh else 'false'}"), "options")
    eps = _guard(hc.request("GET", "/api/providers/custom-endpoints"), "list")
    ep_list = eps.get("endpoints") or []
    current = eps.get("current") or {}

    def host(url: str) -> str:
        return str(url or "").split("://", 1)[-1].split("/")[0].lower()

    configs = []
    for p in (options.get("providers") or []):
        slug = str(p.get("slug") or "")
        if "custom" not in slug.lower():
            continue
        ep = next((e for e in ep_list
                   if e.get("base_url") and host(e["base_url"]) and host(e["base_url"]) in slug), None)
        configs.append({
            "id": slug,                                    # 切换/设默认用（model/set 认 picker slug）
            "manage_id": (ep or {}).get("id"),             # 编辑/删除用（custom-endpoints 体系；可能缺）
            "name": p.get("name") or slug,
            "base_url": (ep or {}).get("base_url") or str(p.get("api_url") or ""),
            "models": p.get("models") or [],
            "model": (ep or {}).get("model") or (p.get("models") or [""])[0],
            "has_api_key": (ep or {}).get("has_api_key"),
            "api_key_preview": (ep or {}).get("api_key_preview"),
            "is_current": bool(p.get("is_current")),
            "api_mode": (ep or {}).get("api_mode"),
        })
    return {"configs": configs, "current": current}


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


@router.delete("/{vendor_id}/models/{model_id}", dependencies=[Depends(require_app_token)])
def delete_vendor_model(vendor_id: str, model_id: str):
    """从厂商模型清单删除一项（真删除，走 config/raw 原文通道）。

    为什么不用 load_config 展开态：GET /api/config 是规范化后的视图（实测会把
    legacy 段的模型清单清洗掉，导致误报 404）。config/raw 读磁盘原文 → 改 →
    整体替换回写，语义保真。默认模型不允许删（先切换）。
    """
    import yaml

    raw = _guard(hc.request("GET", "/api/config/raw"), "raw-read")
    text = raw.get("yaml") or ""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise HTTPException(status_code=500, detail="服务器 config.yaml 不是映射结构")
    cps = doc.get("custom_providers")
    if not isinstance(cps, list):
        raise HTTPException(status_code=404,
                            detail="服务器 config.yaml 没有 custom_providers 段（该厂商可能不在 legacy 段）")
    # vendor_id 形如 custom:<host>；legacy entry 按 name.lower() 对应（实测一致）
    bare = vendor_id.removeprefix("custom:").lower()
    target = next((e for e in cps
                   if isinstance(e, dict) and str(e.get("name", "")).lower() == bare), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"custom_providers 里找不到厂商 {vendor_id}")
    models = target.get("models")
    if not isinstance(models, dict) or model_id not in models:
        raise HTTPException(status_code=404, detail=f"厂商 {target.get('name')} 的清单里没有 {model_id}")
    if str(target.get("model")) == model_id:
        raise HTTPException(status_code=400,
                            detail=f"{model_id} 是该厂商的默认模型，请先切换到其他模型再删除")

    models.pop(model_id)
    new_text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
    return _guard(hc.request("PUT", "/api/config/raw",
                             json={"yaml_text": new_text}), "raw-write")


@router.delete("/{endpoint_id}", dependencies=[Depends(require_app_token)])
def delete_model_config(endpoint_id: str):
    return _guard(hc.request(
        "DELETE", f"/api/providers/custom-endpoints/{endpoint_id}"), "delete")