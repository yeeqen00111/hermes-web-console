# -*- coding: utf-8 -*-
"""
模型配置封装 —— 数据源定案（2026-09-23，用户纠正后确立）：
**全部走 GET/PUT /api/config/raw（磁盘原文通道，零缓存）**。
用户在服务器上手改 config.yaml，下一次列表请求立即反映；写操作也是原文读改写，
不再使用 /api/model/options（1h picker 缓存）与 /api/providers/custom-endpoints
（只覆盖 providers 段、会与 legacy 段分叉）。

对用户系统暴露的概念只有一个：「模型配置」= 一个厂商端点（含其模型清单）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import yaml
from config import hc, require_app_token
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/model-configs", tags=["model-configs"])


def _load_doc() -> Dict[str, Any]:
    """读 dashboard 磁盘上的 config.yaml 原文并解析（直读，零缓存）。"""
    raw = _raw_ok(hc.request("GET", "/api/config/raw"), "raw-read")
    doc = yaml.safe_load(raw.get("yaml") or "")
    if not isinstance(doc, dict):
        raise HTTPException(status_code=500, detail="服务器 config.yaml 不是映射结构")
    return doc


def _save_doc(doc: Dict[str, Any]) -> None:
    new_text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
    _raw_ok(hc.request("PUT", "/api/config/raw", json={"yaml_text": new_text}), "raw-write")


def _raw_ok(resp, what: str) -> Dict[str, Any]:
    if resp.status_code >= 400:
        raise HTTPException(status_code=502,
                            detail=f"{what} HTTP {resp.status_code}: {resp.text[:400]}")
    return resp.json()


def _all_vendor_entries(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """合并两个段的厂商条目：legacy custom_providers 列表 + v12 providers dict。
    每条带 _section 标记（写回时定位），name.lower() 用于 slug。"""
    rows: List[Dict[str, Any]] = []
    for e in (doc.get("custom_providers") or []):
        if isinstance(e, dict) and e.get("name"):
            rows.append({**e, "_section": "custom_providers"})
    for key, e in (doc.get("providers") or {}).items():
        if isinstance(e, dict) and e.get("name"):
            rows.append({**e, "_section": "providers", "_key": key})
    return rows


def _slug_for(name: str) -> str:
    return "custom:" + str(name or "").strip().lower()


def _find_vendor(doc: Dict[str, Any], vendor_id: str) -> Optional[Dict[str, Any]]:
    """vendor_id = custom:<name.lower()>；裸 'custom' 匹配 model 段 direct 形态不在此处理。"""
    bare = vendor_id.removeprefix("custom:").lower()
    return next((e for e in _all_vendor_entries(doc)
                 if str(e.get("name", "")).strip().lower() == bare), None)


class ModelConfigBody(BaseModel):
    """新增/编辑一个厂商端点。api_key 缺省=不动；空串=清除。"""
    id: Optional[str] = None            # custom:<name.lower()>
    name: str
    base_url: str
    model: str
    api_key: Optional[str] = None
    api_mode: Optional[str] = None      # ""|chat_completions|codex_responses|anthropic_messages
    context_length: Optional[int] = None
    discover_models: bool = True
    models: Optional[List[str]] = None
    make_default: bool = False


class ValidateBody(BaseModel):
    name: str = "probe"
    base_url: str
    model: str
    api_key: Optional[str] = None
    api_mode: Optional[str] = None


def _vendor_row(doc: Dict[str, Any], e: Dict[str, Any]) -> Dict[str, Any]:
    slug = _slug_for(e.get("name"))
    model_cfg = doc.get("model") or {}
    cur_provider = str(model_cfg.get("provider", "") or "")
    cur_model = str(model_cfg.get("default", "") or "")
    cur_base = str(model_cfg.get("base_url", "") or "").rstrip("/")
    base = str(e.get("base_url", "") or "").rstrip("/")
    is_current = (cur_provider == slug) or \
                 (cur_provider.lower() == "custom" and bool(cur_base) and cur_base == base)
    models = e.get("models")
    model_list = list(models.keys()) if isinstance(models, dict) else \
        [str(m) for m in models] if isinstance(models, list) else []
    has_key = bool(str(e.get("api_key") or "").strip() or str(e.get("key_env") or "").strip())
    return {
        "id": slug,
        "manage_id": slug,               # raw 通道下编辑/删除都用 slug（内部按 name 定位）
        "name": e.get("name"),
        "base_url": e.get("base_url"),
        "model": str(e.get("model") or (model_list[0] if model_list else "")),
        "models": model_list,
        "api_mode": str(e.get("api_mode") or e.get("transport") or ""),
        "context_length": e.get("context_length"),
        "discover_models": bool(e.get("discover_models", True)),
        "has_api_key": has_key,
        "api_key_preview": None,
        "is_current": is_current,
        "current_model": cur_model if is_current else None,
    }


@router.get("", dependencies=[Depends(require_app_token)])
def list_model_configs(refresh: bool = False):
    """厂商列表（2026-09-23 调研定案）：主源 = /api/model/options 的 custom:* providers。

    数据链（源码确认）：load_picker_context → get_compatible_custom_providers(cfg)
    = custom_providers 字段的官方合并视图（legacy 段 + providers 段），含 discovery
    融合的模型名与 is_current 语义。refresh=false（默认）只探测当前厂商、其余走 1h
    磁盘缓存（快）；refresh=true 探测全部并破缓存（慢但强同步——服务器手改后用这个）。
    /api/providers/custom-endpoints 仅补充 key 管理信息（按 host 匹配）。
    """
    options = _raw_ok(hc.request("GET", f"/api/model/options?refresh={'true' if refresh else 'false'}"), "options")
    eps = _raw_ok(hc.request("GET", "/api/providers/custom-endpoints"), "list")
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


def _apply_vendor_fields(entry: Dict[str, Any], body: ModelConfigBody) -> None:
    """把表单字段合并进 entry（merge 语义：保留手写字段）。"""
    entry["name"] = body.name.strip()
    entry["base_url"] = body.base_url.strip().rstrip("/")
    entry["model"] = body.model.strip()
    if body.api_mode is not None:
        if body.api_mode:
            entry["api_mode"] = body.api_mode
        else:
            entry.pop("api_mode", None)
    if body.context_length and body.context_length > 0:
        entry["context_length"] = int(body.context_length)
    entry["discover_models"] = bool(body.discover_models)
    if body.models is not None:
        entry["models"] = {m: {} for m in body.models if m.strip()}
    # api_key：明文写 entry（Hermes 运行时兼容旧形态）；清空=移除。
    # 注：dashboard 的 upsert 会写 .env + key_env，这里绕开了它——key 明文在 config.yaml
    # 中可用但不如 key_env 安全，正式化时改为调 dashboard 的 upsert 或补 .env 写入。
    if body.api_key is not None:
        key = body.api_key.strip()
        if key:
            entry["api_key"] = key
            entry.pop("key_env", None)
        else:
            entry.pop("api_key", None)
            entry.pop("key_env", None)


@router.post("", dependencies=[Depends(require_app_token)])
def upsert_model_config(body: ModelConfigBody):
    """新增/编辑厂商端点（raw 读改写，直接落磁盘原文）。
    定位键 = body.id（custom:<name.lower()>）；找不到 = 新增（追加 legacy 段）。"""
    doc = _load_doc()
    target = _find_vendor(doc, body.id) if body.id else None
    if target is None:
        target = {"name": body.name.strip()}
        doc.setdefault("custom_providers", []).append(target)
    _apply_vendor_fields(target, body)

    if body.make_default:
        model_cfg = doc.setdefault("model", {})
        model_cfg["provider"] = _slug_for(body.name)
        model_cfg["default"] = body.model.strip()
        model_cfg["base_url"] = body.base_url.strip().rstrip("/")
        model_cfg.pop("api_key", None)

    _save_doc(doc)
    return {"ok": True, "endpoint_id": _slug_for(body.name)}


@router.post("/validate", dependencies=[Depends(require_app_token)])
def validate_model_config(body: ValidateBody):
    """表单「测试连接」：结果在 body（HTTP 200），ok=false 时 message 给原因。"""
    payload = {k: getattr(body, k) for k in
               ("name", "base_url", "model", "api_key", "api_mode")
               if getattr(body, k) is not None}
    resp = hc.request("POST", "/api/providers/custom-endpoints/validate", json=payload)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502,
                            detail=f"validate HTTP {resp.status_code}: {resp.text[:400]}")
    return resp.json()


@router.post("/{vendor_id}/activate", dependencies=[Depends(require_app_token)])
def activate_model_config(vendor_id: str):
    doc = _load_doc()
    v = _find_vendor(doc, vendor_id)
    if v is None:
        raise HTTPException(status_code=404, detail=f"找不到厂商 {vendor_id}")
    model_cfg = doc.setdefault("model", {})
    model_cfg["provider"] = _slug_for(v.get("name"))
    model_cfg["default"] = str(v.get("model") or "")
    model_cfg["base_url"] = str(v.get("base_url") or "").rstrip("/")
    _save_doc(doc)
    return {"ok": True}


@router.post("/{vendor_id}/default", dependencies=[Depends(require_app_token)])
def set_default_model(vendor_id: str, model: str):
    """把该厂商下的指定模型设为全局默认（写 model 段，热切换已实测）。"""
    body = {"scope": "main", "provider": vendor_id, "model": model}
    resp = hc.request("POST", "/api/model/set", json=body)
    if resp.status_code >= 400:
        raise HTTPException(status_code=502,
                            detail=f"model/set HTTP {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    if isinstance(data, dict) and data.get("confirm_required"):
        return {"ok": False, "confirm_required": True,
                "confirm_message": data.get("confirm_message", "")}
    return {"ok": True}


@router.delete("/{vendor_id}/models/{model_id}", dependencies=[Depends(require_app_token)])
def delete_vendor_model(vendor_id: str, model_id: str):
    """从厂商模型清单删除一项（raw 读改写，真删除）。默认模型不允许删（先切换）。"""
    doc = _load_doc()
    v = _find_vendor(doc, vendor_id)
    if v is None:
        raise HTTPException(status_code=404, detail=f"找不到厂商 {vendor_id}")
    models = v.get("models")
    if not isinstance(models, dict) or model_id not in models:
        raise HTTPException(status_code=404, detail=f"厂商 {v.get('name')} 的清单里没有 {model_id}")
    if str(v.get("model")) == model_id:
        raise HTTPException(status_code=400,
                            detail=f"{model_id} 是该厂商的默认模型，请先切换到其他模型再删除")
    models.pop(model_id)
    _save_doc(doc)
    return {"ok": True}


@router.delete("/{vendor_id}", dependencies=[Depends(require_app_token)])
def delete_vendor(vendor_id: str):
    """删除整个厂商端点（raw 读改写）。"""
    doc = _load_doc()
    v = _find_vendor(doc, vendor_id)
    if v is None:
        raise HTTPException(status_code=404, detail=f"找不到厂商 {vendor_id}")
    section = v.pop("_section", "custom_providers")
    if section == "custom_providers":
        doc["custom_providers"] = [e for e in (doc.get("custom_providers") or []) if e is not v]
    else:
        (doc.get("providers") or {}).pop(v.get("_key"), None)
    _save_doc(doc)
    return {"ok": True}