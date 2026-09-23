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

import hidden_store
from hidden_store import (
    add_custom, delete_custom, hide_model as _hide, list_custom, rename_custom,
    unhide_model as _unhide,
)

router = APIRouter(prefix="/api/model-configs", tags=["model-configs"])

VALID_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")


def _raw_ok(resp, what: str) -> Dict[str, Any]:
    if resp.status_code >= 400:
        raise HTTPException(status_code=502,
                            detail=f"{what} HTTP {resp.status_code}: {resp.text[:400]}")
    return resp.json()


def _load_doc() -> Dict[str, Any]:
    """读 dashboard 磁盘上的 config.yaml 原文并解析（直读，零缓存）。"""
    raw = _raw_ok(hc.request("GET", "/api/config/raw"), "raw-read")
    doc = yaml.safe_load(raw.get("yaml") or "")
    if not isinstance(doc, dict):
        raise HTTPException(status_code=500, detail="服务器 config.yaml 不是映射结构")
    return doc


def _all_vendor_entries(doc: Dict[str, Any]) -> List[Any]:
    """合并两个段的厂商条目。返回 (entry, section, key) 三元组——entry 是 doc 内的
    **原引用**（改动直接作用于 doc，保存时生效）；key 仅 providers 段有。"""
    rows: List[Any] = []
    for e in (doc.get("custom_providers") or []):
        if isinstance(e, dict) and e.get("name"):
            rows.append((e, "custom_providers", None))
    for key, e in (doc.get("providers") or {}).items():
        if isinstance(e, dict) and e.get("name"):
            rows.append((e, "providers", key))
    return rows


def _slug_for(name: str) -> str:
    return "custom:" + str(name or "").strip().lower()


def _key_env_for(name: str) -> str:
    """复刻官方 custom_endpoint_key_env 规则（identity=厂商名）。
    实测与用户现有 .env 变量名一致：Ark.cn-beijing.volces.com → HERMES_CUSTOM_ARK_CN_BEIJING_VOLCES_COM_API_KEY"""
    import re
    slug = re.sub(r"[^A-Z0-9]+", "_", str(name or "").upper()).strip("_")
    return f"HERMES_CUSTOM_{slug}_API_KEY" if slug else "HERMES_CUSTOM_API_KEY"


def _find_vendor(doc: Dict[str, Any], vendor_id: str):
    """vendor_id = custom:<name.lower()>。返回 (entry原引用, section, key) 或 (None, None, None)。
    裸 'custom' 的 direct 形态不在此处理。"""
    bare = vendor_id.removeprefix("custom:").lower()
    return next(((e, sec, k) for e, sec, k in _all_vendor_entries(doc)
                 if str(e.get("name", "")).strip().lower() == bare), (None, None, None))


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



@router.get("", dependencies=[Depends(require_app_token)])
def list_model_configs(refresh: bool = False):
    """厂商列表 + 模型清单（分层组装，零缓存）：
    - 厂商接入：config.yaml 原文（custom_providers 段 + providers 段）
    - 模型条目：config entry.models（discovery）∪ SQLite custom_models（手动），SQLite 元数据优先
    - hidden：SQLite hidden_models（展示层删除标记）
    refresh 参数保留（兼容旧前端），raw 通道本身零缓存。
    """
    doc = _load_doc()
    model_cfg = doc.get("model") or {}
    cur_provider = str(model_cfg.get("provider", "") or "")
    cur_model = str(model_cfg.get("default", "") or "")
    cur_base = str(model_cfg.get("base_url", "") or "").rstrip("/")

    # SQLite 手动条目按 vendor 归组
    manual_by_vendor: Dict[str, List[Dict[str, Any]]] = {}
    for r in hidden_store.list_manual():
        manual_by_vendor.setdefault(r["vendor"], []).append(r)

    configs = []
    for e, section, key in _all_vendor_entries(doc):
        name = str(e.get("name") or "")
        slug = _slug_for(name)
        base_url = str(e.get("base_url") or "")
        hidden = hidden_store.hidden_set(slug)
        manual = {m["model"]: m for m in manual_by_vendor.get(slug, [])}

        # 模型条目合并：config entry.models ∪ SQLite 手动（manual 元数据优先），hidden 标注
        config_models = e.get("models")
        config_names = list(config_models.keys()) if isinstance(config_models, dict) else             [str(m) for m in config_models] if isinstance(config_models, list) else []
        items = []
        seen = set()
        for m in config_names:
            if m in seen:
                continue
            seen.add(m)
            man = manual.get(m)
            items.append({
                "model": m,
                "display_name": (man or {}).get("display_name"),
                "source": "manual" if man else "config",
                "hidden": m in hidden,
                "context_length": (man or {}).get("context_length") or
                                  (config_models.get(m, {}) if isinstance(config_models, dict) else {}).get("context_length"),
                "reasoning_effort": (man or {}).get("reasoning_effort") or
                                    (config_models.get(m, {}) if isinstance(config_models, dict) else {}).get("reasoning_effort"),
            })
        for m, man in manual.items():
            if m in seen:
                continue
            seen.add(m)
            items.append({
                "model": m, "display_name": man.get("display_name"),
                "source": "manual", "hidden": False,
                "context_length": man.get("context_length"),
                "reasoning_effort": man.get("reasoning_effort"),
            })

        # 兜底：当前默认模型不在 SQLite → 补存一份（含默认标记），保证列表第一条可见
        default_entry = hidden_store.get_default()
        if cur_provider and cur_model and (
                default_entry is None or default_entry.get("model") != cur_model
                or default_entry.get("vendor") != slug):
            vend = next((ve for ve, _s, _k in _all_vendor_entries(doc)
                         if _slug_for(str(ve.get("name") or "")) == cur_provider), None)
            if vend is not None:
                meta = (vend.get("models") or {}).get(cur_model, {}) if isinstance(vend.get("models"), dict) else {}
                hidden_store.set_default(cur_provider, cur_model,
                                         None, meta.get("context_length"), meta.get("reasoning_effort"))
                default_entry = hidden_store.get_default()

        is_current = (cur_provider == slug) or                      (cur_provider.lower() == "custom" and bool(cur_base) and cur_base == base_url.rstrip("/"))
        # 默认模型不在 entry.models 清单里（model 段直接指定）→ 补入条目
        if default_entry and default_entry.get("vendor") == slug and                 not any(i["model"] == default_entry.get("model") for i in items):
            items.append({"model": default_entry.get("model"), "display_name": None,
                          "source": "default", "hidden": False,
                          "context_length": default_entry.get("context_length"),
                          "reasoning_effort": default_entry.get("reasoning_effort")})
        for it in items:
            it["is_default"] = bool(default_entry and default_entry.get("vendor") == slug
                                    and default_entry.get("model") == it["model"])
        items.sort(key=lambda i: (0 if i.get("is_default") else 1, i["model"]))   # 默认置顶
        configs.append({
            "id": slug,                                    # 切换/设默认用（model/set 认 slug）
            "manage_id": slug,                             # raw 通道下编辑/删除都用 slug
            "name": name,
            "base_url": base_url,
            "items": items,                                # 对象数组（model/display_name/source/hidden/元数据）
            "models": [i["model"] for i in items],         # 兼容字段
            "hidden_models": sorted(hidden),
            "model": str(e.get("model") or (items[0]["model"] if items else "")),
            "has_api_key": bool(str(e.get("api_key") or "").strip() or str(e.get("key_env") or "").strip()),
            "is_current": is_current,
            "api_mode": str(e.get("api_mode") or e.get("transport") or ""),
        })
    return {"configs": configs, "current": {
        "provider": cur_provider, "model": cur_model, "base_url": cur_base,
    }}


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
    # 模型清单不在 config 层写（SQLite custom_models 管理展示层模型）
    # api_key 的写入/清除在 upsert 主函数里走 PUT/DELETE /api/env（.env + key_env 引用，
    # 与官方 upsert 同款生命周期），不在此处处理。


@router.post("", dependencies=[Depends(require_app_token)])
def upsert_model_config(body: ModelConfigBody):
    """新增/编辑厂商端点（raw 读改写，直接落磁盘原文）。
    定位键 = body.id（custom:<name.lower()>）；找不到 = 新增（追加 legacy 段）。"""
    doc = _load_doc()
    target, _sec, _key = _find_vendor(doc, body.id) if body.id else (None, None, None)
    is_new = target is None
    if target is None:
        target = {"name": body.name.strip()}
        doc.setdefault("custom_providers", []).append(target)
    _apply_vendor_fields(target, body)

    # api_key 生命周期（官方同款）：写 .env（PUT /api/env）+ entry 用 key_env 引用。
    # 三态：填写=写新 key；空串=清除；缺省=不动。key 不进 config.yaml。
    key_var = _key_env_for(body.name)
    if body.api_key is not None:
        key = body.api_key.strip()
        if key:
            r = hc.request("PUT", "/api/env", json={"key": key_var, "value": key})
            if r.status_code >= 400:
                raise HTTPException(status_code=502,
                                    detail=f"写 API key 到 .env 失败: {r.text[:200]}")
            target["key_env"] = key_var
            target.pop("api_key", None)
        else:
            r = hc.request("DELETE", "/api/env", json={"key": key_var}, timeout=30)
            target.pop("key_env", None)
            target.pop("api_key", None)

    if body.make_default:
        model_cfg = doc.setdefault("model", {})
        model_cfg["provider"] = _slug_for(body.name)
        model_cfg["default"] = body.model.strip()
        model_cfg["base_url"] = body.base_url.strip().rstrip("/")
        model_cfg.pop("api_key", None)
        if target.get("key_env"):
            model_cfg["key_env"] = target["key_env"]

    _save_doc(doc)
    if body.make_default:
        hidden_store.set_default(_slug_for(body.name), body.model.strip())
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
    v, _sec, _key = _find_vendor(doc, vendor_id)
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
    # SQLite 同步默认标记（列表第一条的依据）
    hidden_store.set_default(vendor_id, model)
    return {"ok": True}


class HideBatchBody(BaseModel):
    models: List[str]


@router.post("/{vendor_id}/models/hide-batch", dependencies=[Depends(require_app_token)])
def hide_models_batch(vendor_id: str, body: HideBatchBody):
    """批量隐藏（= 批量删除）。逐项幂等，已隐藏的忽略。"""
    for m in body.models:
        m = m.strip()
        if m:
            _hide(vendor_id, m)
    return {"ok": True, "count": len([m for m in body.models if m.strip()])}


@router.post("/{vendor_id}/models/{model_id}/hide", dependencies=[Depends(require_app_token)])
def hide_vendor_model(vendor_id: str, model_id: str):
    """从可选列表隐藏一个模型（SQLite 展示层配置；Hermes config 不动）。"""
    _hide(vendor_id, model_id)
    return {"ok": True, "hidden": True}


@router.post("/{vendor_id}/models/{model_id}/unhide", dependencies=[Depends(require_app_token)])
def unhide_vendor_model(vendor_id: str, model_id: str):
    """恢复显示一个已隐藏的模型。"""
    _unhide(vendor_id, model_id)
    return {"ok": True, "hidden": False}


class AddModelBody(BaseModel):
    name: str                                # 模型 ID（厂商 API 真名）
    display_name: Optional[str] = None       # 展示名（前端列表显示用）
    context_length: Optional[int] = None
    reasoning_effort: Optional[str] = None   # minimal|low|medium|high|xhigh|max|ultra


class RenameModelBody(BaseModel):
    name: str
    context_length: Optional[int] = None
    reasoning_effort: Optional[str] = None


# 合法思考档位（hermes_constants.VALID_REASONING_EFFORTS）
VALID_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")


def _model_meta(body) -> Dict[str, Any]:
    """从表单字段构造 models.<id> 的元数据项（context_length / reasoning_effort）。"""
    meta: Dict[str, Any] = {}
    if body.context_length and body.context_length > 0:
        meta["context_length"] = int(body.context_length)
    if body.reasoning_effort:
        if body.reasoning_effort not in VALID_EFFORTS:
            raise HTTPException(status_code=400,
                                detail=f"思考等级非法：{body.reasoning_effort}（合法：{', '.join(VALID_EFFORTS)}）")
        meta["reasoning_effort"] = body.reasoning_effort
    return meta


@router.post("/{vendor_id}/models", dependencies=[Depends(require_app_token)])
def add_vendor_model(vendor_id: str, body: AddModelBody):
    """向厂商的可选列表添加一个模型（SQLite；Hermes config 不动）。"""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="模型名不能为空")
    ctx_len = body.context_length if body.context_length and body.context_length > 0 else None
    effort = body.reasoning_effort if body.reasoning_effort in VALID_EFFORTS else None
    if body.reasoning_effort and not effort:
        raise HTTPException(status_code=400,
                            detail=f"思考等级非法：{body.reasoning_effort}（合法：{', '.join(VALID_EFFORTS)}）")
    add_custom(vendor_id, name, body.display_name, ctx_len, effort)
    return {"ok": True, "model": name}


@router.put("/{vendor_id}/models/{model_id}", dependencies=[Depends(require_app_token)])
def rename_vendor_model(vendor_id: str, model_id: str, body: AddModelBody):
    """重命名（编辑）一个模型：SQLite 内旧名出、新名进（元数据保留）。"""
    new_name = body.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="新模型名不能为空")
    if new_name == model_id:
        return {"ok": True, "model": new_name}
    rename_custom(vendor_id, model_id, new_name, body.display_name,
                  body.context_length if body.context_length and body.context_length > 0 else None,
                  body.reasoning_effort if body.reasoning_effort in VALID_EFFORTS else None)
    return {"ok": True, "model": new_name}


@router.delete("/{vendor_id}/models/{model_id}", dependencies=[Depends(require_app_token)])
def delete_vendor_model(vendor_id: str, model_id: str):
    """删除一个模型条目（SQLite；同时清隐藏记录，列表立即消失）。"""
    delete_custom(vendor_id, model_id)
    _unhide(vendor_id, model_id)
    return {"ok": True}


@router.delete("/{vendor_id}", dependencies=[Depends(require_app_token)])
def delete_vendor(vendor_id: str):
    """删除整个厂商端点（raw 读改写，按原引用移除）。"""
    doc = _load_doc()
    v, section, key = _find_vendor(doc, vendor_id)
    if v is None:
        raise HTTPException(status_code=404, detail=f"找不到厂商 {vendor_id}")
    if section == "custom_providers":
        doc["custom_providers"] = [e for e in (doc.get("custom_providers") or []) if e is not v]
    else:
        (doc.get("providers") or {}).pop(key, None)
    _save_doc(doc)
    return {"ok": True}