# -*- coding: utf-8 -*-
"""
Agent 身份文件编辑（2026-09-28 定案）—— 只编辑 **default** profile 的三个文件：

    逻辑名      文件                  dashboard 通道
    soul       <home>/SOUL.md        GET/PUT /api/profiles/default/soul   （专用端点）
    memory     <home>/memories/MEMORY.md   GET /api/fs/read-text、POST /api/fs/write-text
    user       <home>/memories/USER.md     同上（通用文本通道，绝对路径）

`<home>` 由 `GET /api/profiles` 的 default 条目 `path` **惰性发现并缓存**（2026-09-28 真机
实测 = `/opt/data`），**绝不出现在响应里**：前端只认 soul/memory/user 三个逻辑名，路径只在
本模块内部拼装（`_memory_path`）。

为什么不用 `GET /api/memory` 取尺寸做写前复查（设计 §2.2 原方案）：
`/api/memory` 走 `get_hermes_home()` 的**当前 profile scope**，而 fs 通道走我们拼的**绝对路径**
—— 二者在「服务的是命名 profile」的部署上会指向不同文件；且写前还需要 `truncated` 判断。
改成统一「用同一条读通道复查」，永远同一份文件、一次读拿齐 exists/byteSize/truncated。

并发（乐观锁，必须做）：MEMORY/USER 会被 agent 后台持续写，而 dashboard 明确不做陈旧检测
（`fs_write_text` docstring: "Stale-on-disk detection is the client's job"）。所以读回来的
`byteSize` 即版本号，写时必须回传 `base_byteSize`；不一致 → 409 + `current_byteSize`，
前端保留草稿、重新加载后再存。

写前备份：本进程**首次写**之前调一次 `POST /api/ops/backup`（09-23 事故铁律：恢复只能用操作
前的实时备份，绝不用对话快照）；**备份失败即拒绝写入**（fail-closed），不做「先写再说」。

安全：三个文件可能含用户记忆/设定——内容不进日志、不进错误回显；未知名字直接 404，
不给任何路径信息。超限保护：读取被截断（>512KB）或写入超 8MB 一律拒绝（对齐 dashboard 上限）。
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from config import hc, require_app_token
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/agent-files", tags=["agent-files"])

DEFAULT_PROFILE = "default"

# 逻辑名（前端只认这三个）
NAME_SOUL = "soul"
NAME_MEMORY = "memory"
NAME_USER = "user"
NAMES: Tuple[str, ...] = (NAME_SOUL, NAME_MEMORY, NAME_USER)

_MEMORY_FILENAMES = {NAME_MEMORY: "MEMORY.md", NAME_USER: "USER.md"}

# dashboard 通用文本端点上限（web_routers/files.py：_FS_TEXT_PREVIEW_MAX_BYTES /
# _FS_TEXT_WRITE_MAX_BYTES）。超过预览上限的读取是**截断稿**，绝不能拿来回写。
PREVIEW_MAX_BYTES = 512 * 1024
WRITE_MAX_BYTES = 8 * 1024 * 1024

# home 缓存：只在发现成功时写入（dashboard 未就绪时不缓存失败）
_home_cache: Dict[str, str] = {}
# 本进程是否已完成「首次写前备份」
_backup_done = False


def _upstream_error(status: int) -> HTTPException:
    """上游错误统一映射：只带状态码 + 固定文案，**绝不回显上游响应体**。"""
    if status == 400:
        return HTTPException(status_code=400, detail="Hermes 拒绝了本次请求（文件路径或内容不合法）")
    if status == 403:
        return HTTPException(status_code=403, detail="Hermes 拒绝访问该文件")
    if status == 404:
        return HTTPException(status_code=404, detail="Hermes 上找不到默认 Agent 的身份文件")
    if status == 413:
        return HTTPException(status_code=413, detail="文件过大，Hermes 拒绝写入")
    return HTTPException(status_code=502, detail=f"Hermes 身份文件接口请求失败（HTTP {status}）")


def _require_name(name: str) -> str:
    if name not in NAMES:
        raise HTTPException(status_code=404, detail=f"未知的身份文件：{name}（可用：{', '.join(NAMES)}）")
    return name


def _default_home() -> str:
    """default profile 的 home 绝对路径（`GET /api/profiles` 的 is_default 条目 path）。"""
    cached = _home_cache.get("path")
    if cached:
        return cached
    resp = hc.request("GET", "/api/profiles")
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code)
    data = resp.json()
    profiles = data.get("profiles") if isinstance(data, dict) else None
    for item in profiles or []:
        if isinstance(item, dict) and item.get("is_default") and item.get("path"):
            home = str(item["path"]).rstrip("/")
            _home_cache["path"] = home
            return home
    raise HTTPException(status_code=502, detail="Hermes 未返回默认 Agent（default profile）目录信息")


def _memory_path(name: str) -> str:
    """<home>/memories/<FILE> —— 路径只在后端内部拼，前端拿不到（也是唯一的外部输入点：home）。"""
    return f"{_default_home()}/memories/{_MEMORY_FILENAMES[name]}"


def _read_file(name: str) -> Dict[str, Any]:
    """统一读取形状：{name, exists, content, byteSize, truncated, binary}。

    文件不存在**不是错误**（前端按「未创建」处理）：soul 端点回 `exists:false`，
    fs 通道回 404 → 归一成 `exists:false, byteSize:0`。
    """
    if name == NAME_SOUL:
        resp = hc.request("GET", f"/api/profiles/{DEFAULT_PROFILE}/soul")
        if resp.status_code >= 400:
            raise _upstream_error(resp.status_code)
        data = resp.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=502, detail="Hermes SOUL.md 返回格式异常")
        content = data.get("content") or ""
        return {"name": name, "exists": bool(data.get("exists")), "content": content,
                "byteSize": len(content.encode("utf-8")), "truncated": False, "binary": False}

    resp = hc.request("GET", "/api/fs/read-text", params={"path": _memory_path(name)})
    if resp.status_code == 404:
        return {"name": name, "exists": False, "content": "", "byteSize": 0,
                "truncated": False, "binary": False}
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code)
    data = resp.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Hermes 记忆文件返回格式异常")
    return {"name": name, "exists": True, "content": data.get("text") or "",
            "byteSize": int(data.get("byteSize") or 0), "truncated": bool(data.get("truncated")),
            "binary": bool(data.get("binary"))}


def _write_file(name: str, content: str) -> int:
    """写回并返回新字节数。两个通道都是原子写（临时文件 + os.replace）。"""
    if name == NAME_SOUL:
        resp = hc.request("PUT", f"/api/profiles/{DEFAULT_PROFILE}/soul", json={"content": content})
    else:
        resp = hc.request("POST", "/api/fs/write-text",
                          json={"path": _memory_path(name), "content": content})
    if resp.status_code >= 400:
        raise _upstream_error(resp.status_code)
    return len(content.encode("utf-8"))


def _ensure_backup() -> None:
    """本进程首次写前备份一次。备份是唯一的恢复源 → **失败即拒绝写入**。

    ⚠️ body 必须发（哪怕空对象）：`POST /api/ops/backup` 的 `body: BackupRequest` 是**必填参数**，
    缺 body 会被 FastAPI 判 422（字段本身全可选，缺的是 body 本体）——2026-09-28 真机踩到。
    """
    global _backup_done
    if _backup_done:
        return
    resp = hc.request("POST", "/api/ops/backup", json={})
    if resp.status_code >= 400:
        raise HTTPException(status_code=502,
                            detail=f"写入前备份失败（HTTP {resp.status_code}），本次保存已取消")
    _backup_done = True


# ── 端点 ────────────────────────────────────────────────────────────────────

@router.get("", dependencies=[Depends(require_app_token)])
async def list_agent_files() -> Dict[str, Any]:
    """三文件的 {exists, byteSize} 汇总（不返回内容、不返回路径）。"""
    files = {}
    for name in NAMES:
        current = _read_file(name)
        files[name] = {"exists": current["exists"], "byteSize": current["byteSize"]}
    return {"profile": DEFAULT_PROFILE, "files": files}


@router.get("/{name}", dependencies=[Depends(require_app_token)])
async def get_agent_file(name: str) -> Dict[str, Any]:
    return _read_file(_require_name(name))


class AgentFileUpdate(BaseModel):
    """`base_byteSize` = 读取时拿到的字节数（乐观锁版本号，见模块 docstring）。"""
    content: str
    base_byteSize: int


@router.put("/{name}", dependencies=[Depends(require_app_token)])
async def put_agent_file(name: str, body: AgentFileUpdate):
    _require_name(name)

    if len(body.content.encode("utf-8")) > WRITE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="内容超过 8MB，Hermes 拒绝写入")

    current = _read_file(name)   # 写前复查：同一通道读同一份文件
    if current["truncated"]:
        raise HTTPException(
            status_code=413,
            detail="文件超过 512KB，读取内容已被截断，无法安全覆盖——请直接在服务器上编辑",
        )
    if current["byteSize"] != body.base_byteSize:
        # 陈旧：多半是 agent 后台刚写过。前端保留草稿，重新加载后再存。
        return JSONResponse(status_code=409, content={
            "detail": "文件已被更新（Agent 可能刚写入过），请重新加载后再保存",
            "current_byteSize": current["byteSize"],
        })

    _ensure_backup()
    return {"ok": True, "byteSize": _write_file(name, body.content)}
