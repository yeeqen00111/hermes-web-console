# -*- coding: utf-8 -*-
"""
消息渠道封装 —— 飞书 / 钉钉（2026-10-09 由「只做飞书」参数化而来）：
只管理「正在跑的那份网关」的配置（无 ?profile= 的 unscoped 直通通道）。
转发 Hermes dashboard 的 /api/messaging/platforms[/{platform_id}[/test]]
（web_routers/messaging.py:792/851/916），platform_id 限 ALLOWED_PLATFORMS。
不做多平台全量、不做 profile 维度（多网关同跑是服务器侧 multiplex_mode 的事，页面改不了）。

平台白名单
==========
上游 catalogue 有 30 个平台，本层只放行**已在生产验证过**的两个：
feishu（2026-09-28 验收）、dingtalk（2026-10-09 只读实测 enabled+connected）。
白名单外一律 404，**不打上游**。加平台 = 这里加一个 id + 前端加一组字段表。

env 可写键：运行时从平台卡取，不硬编码
=====================================
上游 GET /api/messaging/platforms 返回的每张卡带 env_vars[].key，那就是权威 allowlist。
这组键由 Hermes 从 plugins/platforms/<id>/plugin.yaml 的 requires_env+optional_env 注入
OPTIONAL_ENV_VARS（hermes_cli/config.py:_inject_platform_plugin_env_vars），再按
_PLATFORM_OVERRIDES 显式项合并、经 _is_setup_hidden_env 按后缀过滤
（*_HOME_CHANNEL* / *_ALLOW_ALL_USERS / *_PROXY / *_REQUIRE_MENTION… 这些"自配置项"被隐藏）。
实测（2026-10-09）：feishu 6 键、dingtalk 4 键。**不再抄一份常量**——抄的那份会在
Hermes 升级加字段时静默漂移，表现为"某个字段莫名其妙传不上去"。
代价：PUT 时多一次 GET（渠道配置是低频操作）。被拒的请求因此会打到上游一次 GET，
但**绝不打上游写接口**（写路径仍是零污染）。

PUT 语义（源码核实 messaging.py:879-885）
========================================
  - env 里的**空串会被上游 `if trimmed:` 跳过** → 空串 ≠ 清除，清除必须走 clear_env 数组。
    因此本层把前端传的 body **逐字透传**，绝不把「空串」擅自解释成「清除」。
  - env / clear_env 的键必须在上游 allowlist 内，本地先拦一遍，给干净的错误。
  - 凭据不进日志、不进错误回显：本模块不打日志；上游错误只透传状态码 + 固定文案，不回显响应体。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import hc, require_app_token
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel

router = APIRouter(prefix="/api/channels", tags=["channels"])

# 白名单：只放行生产验证过的平台。白名单外一律 404，不打上游。
ALLOWED_PLATFORMS = ("feishu", "dingtalk")

# 显示名，用于错误文案。飞书的文案与参数化之前**逐字一致**（对外契约零变更）。
PLATFORM_LABELS = {"feishu": "飞书", "dingtalk": "钉钉"}

# 平台 id 会被拼进上游 URL，限死字符集（同时也挡掉路径穿越）。
# 上游平台 id 全是小写字母+下划线，加数字留余量。
PLATFORM_PATH = Path(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")


def _require_platform(platform_id: str) -> str:
    """白名单校验。命中即在，未命中 404——绝不把未知 id 透传给上游。"""
    if platform_id not in ALLOWED_PLATFORMS:
        raise HTTPException(status_code=404, detail=f"不支持的消息渠道：{platform_id}")
    return platform_id


def _upstream_error(status: int, platform_id: str) -> HTTPException:
    """上游错误统一映射：只带状态码与固定文案，绝不回显上游响应体（可能含凭据原文）。"""
    name = PLATFORM_LABELS.get(platform_id, platform_id)
    if status in (400, 409):
        return HTTPException(status_code=status, detail=f"Hermes 拒绝了本次{name}渠道配置请求")
    if status == 404:
        return HTTPException(status_code=404, detail=f"{name}平台不在 Hermes 渠道目录中")
    return HTTPException(status_code=502, detail=f"Hermes 渠道接口请求失败（HTTP {status}）")


def _fetch_card(platform_id: str) -> Dict[str, Any]:
    """取该平台的卡片（unscoped = 正在跑的那份网关）。

    卡片同时是 env allowlist 的来源，见模块 docstring。
    """
    resp = hc.request("GET", "/api/messaging/platforms")
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code, platform_id)
    data = resp.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Hermes 渠道列表返回格式异常")
    for p in data.get("platforms") or []:
        if isinstance(p, dict) and p.get("id") == platform_id:
            return p
    raise HTTPException(status_code=404, detail=f"{PLATFORM_LABELS.get(platform_id, platform_id)}平台不在 Hermes 渠道目录中")


def _env_allowlist(platform_id: str) -> frozenset:
    """可写 env 键 = 该平台卡里的 env_vars[].key（上游即权威，见模块 docstring）。"""
    card = _fetch_card(platform_id)
    return frozenset(
        v["key"] for v in (card.get("env_vars") or [])
        if isinstance(v, dict) and v.get("key")
    )


@router.get("/{platform_id}", dependencies=[Depends(require_app_token)])
async def get_channel(platform_id: str = PLATFORM_PATH) -> Dict[str, Any]:
    _require_platform(platform_id)
    return _fetch_card(platform_id)


class ChannelUpdate(BaseModel):
    """与上游 MessagingPlatformUpdate 对齐（web_models.py:58），唯独**没有 profile 字段**：
    保证永远走 unscoped——即正在跑的那份网关配置（要管别的 profile 得另做 profile 切换页）。"""
    enabled: Optional[bool] = None
    env: Dict[str, str] = {}
    clear_env: List[str] = []


@router.put("/{platform_id}", dependencies=[Depends(require_app_token)])
async def update_channel(body: ChannelUpdate, platform_id: str = PLATFORM_PATH) -> Dict[str, Any]:
    _require_platform(platform_id)
    allowed = _env_allowlist(platform_id)
    for key in list(body.env) + list(body.clear_env):
        if key not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"{key} 不是{PLATFORM_LABELS.get(platform_id, platform_id)}可配置项"
                       f"（可用：{', '.join(sorted(allowed))}）",
            )
    resp = hc.request(
        "PUT",
        f"/api/messaging/platforms/{platform_id}",
        json={"enabled": body.enabled, "env": body.env, "clear_env": body.clear_env},
    )
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code, platform_id)
    return resp.json()


@router.post("/{platform_id}/test", dependencies=[Depends(require_app_token)])
async def test_channel(platform_id: str = PLATFORM_PATH) -> Dict[str, Any]:
    """检查状态。⚠️ 上游**不是探活**：只判 enabled→必填齐→网关在跑→state，不发真实网络请求。"""
    _require_platform(platform_id)
    resp = hc.request("POST", f"/api/messaging/platforms/{platform_id}/test")
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code, platform_id)
    return resp.json()


@router.post("/{platform_id}/restart", dependencies=[Depends(require_app_token)])
async def restart_gateway(platform_id: str = PLATFORM_PATH) -> Dict[str, Any]:
    """重启网关让新配置生效。2026-09-28 真机验证：POST /api/gateway/restart 在本部署
    （单容器）可用——网关回落约 15~40s（stopped→starting→running）后自动回来，
    feishu/weixin/api_server 全部重连（fresh updated_at）。转发结果 {ok, pid, name}。

    ⚠️ 这是**网关级**操作：一次重启**所有**渠道（feishu/dingtalk/weixin/api_server…）和
    正在进行的对话。路径里的 platform_id 只是与同平台其他动作保持路径一致（不破坏既有调用），
    **不代表"只重启该平台"**——上游没有单平台重启的口子：`/api/gateway/restart`
    （hermes_cli/web_routers/actions.py:139）拉的就是 `hermes gateway restart`（整个进程）；
    网关内部虽有 _platform_reconnect_watcher 能单独重连某个平台，但只由内部连接失败触发，
    没有任何外部入口。要"一个个重启"必须改 hermes-agent 源码——那是禁止的。
    """
    _require_platform(platform_id)
    resp = hc.request("POST", "/api/gateway/restart")
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code, platform_id)
    return resp.json()
