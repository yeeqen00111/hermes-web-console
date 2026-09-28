# -*- coding: utf-8 -*-
"""
消息渠道封装 —— 飞书（2026-09-28 定案）：
只做飞书平台，只管理「正在跑的那份网关」的配置（无 ?profile= 的 unscoped 直通通道）。
转发 Hermes dashboard 的 /api/messaging/platforms[/{platform_id}[/test]]
（web_routers/messaging.py:792/851/916），platform_id 写死 feishu。
不做多平台、不做 profile 维度（多网关同跑是服务器侧 multiplex_mode 的事，页面改不了）。

PUT 语义（源码核实 messaging.py:879-885）：
  - env 里的**空串会被上游 `if trimmed:` 跳过** → 空串 ≠ 清除，清除必须走 clear_env 数组。
    因此本层把前端传的 body **逐字透传**，绝不把「空串」擅自解释成「清除」。
  - env / clear_env 的键必须在上游 allowlist 内（catalog entry env_vars + OPTIONAL_ENV_VARS
    前缀合并，2026-09-28 对真实 dashboard 实测 = 6 键），本地先拦一遍，给干净的错误。
  - 凭据不进日志、不进错误回显：本模块不打日志；上游错误只透传状态码 + 固定文案，不回显响应体。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import hc, require_app_token
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/channels", tags=["channels"])

PLATFORM_ID = "feishu"

# 飞书可写 env 键（2026-09-28 实测：APP_ID/APP_SECRET 必填 + 4 个可选，全部可 PUT）
FEISHU_ENV_KEYS = {
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_ENCRYPT_KEY",
    "FEISHU_VERIFICATION_TOKEN",
    "FEISHU_DOMAIN",
    "FEISHU_ALLOWED_USERS",
}


def _upstream_error(status: int) -> HTTPException:
    """上游错误统一映射：只带状态码与固定文案，绝不回显上游响应体（可能含凭据原文）。"""
    if status in (400, 409):
        return HTTPException(status_code=status, detail="Hermes 拒绝了本次飞书渠道配置请求")
    if status == 404:
        return HTTPException(status_code=404, detail="飞书平台不在 Hermes 渠道目录中")
    return HTTPException(status_code=502, detail=f"Hermes 渠道接口请求失败（HTTP {status}）")


@router.get("/feishu", dependencies=[Depends(require_app_token)])
async def get_feishu_channel() -> Dict[str, Any]:
    resp = hc.request("GET", "/api/messaging/platforms")
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code)
    data = resp.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Hermes 渠道列表返回格式异常")
    for p in data.get("platforms") or []:
        if isinstance(p, dict) and p.get("id") == PLATFORM_ID:
            return p
    raise HTTPException(status_code=404, detail="飞书平台不在 Hermes 渠道目录中")


class FeishuChannelUpdate(BaseModel):
    """与上游 MessagingPlatformUpdate 对齐（web_models.py:58），唯独**没有 profile 字段**：
    保证永远走 unscoped——即正在跑的那份网关配置（要管别的 profile 得另做 profile 切换页）。"""
    enabled: Optional[bool] = None
    env: Dict[str, str] = {}
    clear_env: List[str] = []


@router.put("/feishu", dependencies=[Depends(require_app_token)])
async def update_feishu_channel(body: FeishuChannelUpdate) -> Dict[str, Any]:
    for key in list(body.env) + list(body.clear_env):
        if key not in FEISHU_ENV_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"{key} 不是飞书可配置项（可用：{', '.join(sorted(FEISHU_ENV_KEYS))}）",
            )
    resp = hc.request(
        "PUT",
        f"/api/messaging/platforms/{PLATFORM_ID}",
        json={"enabled": body.enabled, "env": body.env, "clear_env": body.clear_env},
    )
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code)
    return resp.json()


@router.post("/feishu/test", dependencies=[Depends(require_app_token)])
async def test_feishu_channel() -> Dict[str, Any]:
    resp = hc.request("POST", f"/api/messaging/platforms/{PLATFORM_ID}/test")
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code)
    return resp.json()