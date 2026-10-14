# -*- coding: utf-8 -*-
"""
智能问数 · 数据库配置（2026-10-12 落地）

**本模块只管写配置，不连数据库。**
页面上新增/修改的那条连接，写进与 hermes-mcp 共享的 SQLite 注册表就结束了；
真正连库的是问数端（hermes-mcp），它是独立服务、通过 HTTP 注册到 Hermes，
在对话链路里被调用：

    控制台后端 ──写──▶ 共享 SQLite（db_connections.db）◀──读── hermes-mcp
                                                                  ▲
    Hermes ──HTTP（mcp_servers.<name>.url）──────────────────────────┘

所以这里**不测连通、不装任何 DB driver、不碰 status/last_error 两列**——
那两列留给问数端，本层不写。
控制台也不连 hermes-mcp、不 import 它：两端只共享文件与格式约定。

格式约定（错一个字符，问数端就解不开）
=====================================
  key  = base64.urlsafe_b64encode( sha256( HERMESMCP_DB_KEY.encode("utf-8") ).digest() )
  密文 = Fernet(key).encrypt( password.encode("utf-8") )     → 写 password_cipher 列
  明文 = Fernet(key).decrypt( password_cipher ).decode()
表结构、列名、默认值与 hermes-mcp 的 store.py `_SCHEMA` **逐字对齐**。
缺 key 时拒绝保存密码（不落明文、不降级）。

一期 = 一个库
=============
表本身支持多条（name UNIQUE），本层也按「数组」返回；只是 UI 一期只管理一条，
且**改名必须换行**（先删旧行再插新行），否则会留下孤儿行、破坏「只有一条」的前提。
以后放开多库：加 `{id}` 系列端点与列表页即可，表结构和契约都不用动。

路径里只允许出现标识符占位符，不写死任何值（教训：/api/channels/feishu/restart）。
"""
from __future__ import annotations

import base64
import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from config import HERMESMCP_DB_KEY, HERMESMCP_DB_PATH, require_app_token
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/db-connections", tags=["db-connections"])

# 类型白名单**只用于校验**（决定页面上填什么、库里记什么），本层不据此连库，故不需要 driver。
# 刻意不含 sqlite：注册表里指向本地文件等于把服务器文件系统交给对话，生产不开放。
TYPES = ("mysql", "postgresql", "sqlserver", "oracle")
DEFAULT_PORTS = {"mysql": 3306, "postgresql": 5432, "sqlserver": 1433, "oracle": 1521}
MAX_ROWS_HARD_CAP = 1000
DEFAULT_MAX_ROWS = 200

# 表结构与 hermes-mcp store.py 的 _SCHEMA 对齐（IF NOT EXISTS：谁先跑谁建）
_SCHEMA = """
CREATE TABLE IF NOT EXISTS db_connections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    type            TEXT NOT NULL,
    host            TEXT,
    port            INTEGER,
    database        TEXT,
    username        TEXT,
    password_cipher TEXT,
    read_only       INTEGER NOT NULL DEFAULT 1,
    max_rows        INTEGER,
    status          TEXT,
    last_error      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class ConnectionForm(BaseModel):
    """新增/编辑表单。**id 不由前端提交**：一期就一条记录，PUT 幂等（有则改、没有则建）。"""
    name: str = ""
    type: str = ""
    host: str = ""
    port: Optional[int] = None
    database: str = ""
    username: str = ""
    read_only: bool = True
    max_rows: Optional[int] = None
    # 密码三态（与模型配置的 api_key 一致，**与 Hermes 渠道的 env 语义不同**）：
    #   不发该字段 = 保持原值 ｜ "" = 清除 ｜ 非空 = 换新（加密入库）
    password: Optional[str] = None


# ── 加解密（格式契约见模块注释；与 hermes-mcp store.py 逐字一致）──

def _fernet():
    if not HERMESMCP_DB_KEY:
        raise HTTPException(status_code=502, detail="后端未配置 HERMESMCP_DB_KEY，无法处理数据库密码")
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256(HERMESMCP_DB_KEY.encode("utf-8")).digest())
    return Fernet(key)


def _encrypt(password: str) -> str:
    return _fernet().encrypt(password.encode("utf-8")).decode()


# ── 存储 ──

def _open():
    """打开共享库。**必须显式 close**：`with sqlite3.connect(...)` 只管事务不管关闭，
    句柄不释放的话 Windows 上文件会一直被占（测试里删临时目录会 PermissionError）。"""
    path = Path(HERMESMCP_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)   # autocommit，事务显式开
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


class _Conn:
    """with 用法：正常/异常都 close（sqlite 的 with 只回滚事务，不关文件）。"""

    def __enter__(self) -> sqlite3.Connection:
        self.conn = _open()
        return self.conn

    def __exit__(self, *exc) -> bool:
        self.conn.close()
        return False


def _row_out(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    """对外形状：**永不回显密码**，只给 has_password。
    status / last_error 一并返回只为展示（由问数端写），本层不改它们。"""
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "host": row["host"] or "",
        "port": row["port"],
        "database": row["database"] or "",
        "username": row["username"] or "",
        "read_only": bool(row["read_only"]),
        "max_rows": row["max_rows"] or DEFAULT_MAX_ROWS,
        "has_password": bool(row["password_cipher"]),
        "status": row["status"],
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _sole(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    """一期只有一条记录：取 id 最小的那条（表本身允许多条，将来放开不用改契约）。"""
    return conn.execute("SELECT * FROM db_connections ORDER BY id LIMIT 1").fetchone()


def _cipher_of(conn: sqlite3.Connection, row_id: int) -> str:
    row = conn.execute("SELECT password_cipher FROM db_connections WHERE id=?", (row_id,)).fetchone()
    return (row["password_cipher"] or "") if row else ""


# ── 参数校验 ──

_NAME_RE = re.compile(r"^[A-Za-z0-9_一-龥-]{1,64}$")
_HOST_RE = re.compile(r"^[^\s/\\]{1,255}$")      # 主机名或 IP：不许空白、不许路径分隔符


def _clean(form: ConnectionForm) -> Dict[str, Any]:
    """校验并归一化。错误只在本地判定，不泄露任何已存值。"""
    name = (form.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="名称只能用中英文、数字、下划线、连字符（1–64 位）")

    kind = (form.type or "").strip().lower()
    if kind not in TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的数据库类型：{kind or '(空)'}（可用：{'、'.join(TYPES)}）")

    host = (form.host or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="主机不能为空")
    if not _HOST_RE.match(host):
        raise HTTPException(status_code=400, detail="主机名不合法")

    # 注意 `is not None`：端口 0 是「没填」，该走默认值，不能原样透传
    port = form.port if form.port is not None else DEFAULT_PORTS[kind]
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise HTTPException(status_code=400, detail="端口需在 1–65535 之间")

    database = (form.database or "").strip()
    if not database:
        raise HTTPException(status_code=400, detail="库名不能为空（Oracle 填服务名）")

    username = (form.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="账号不能为空（请用只有 SELECT 权限的账号）")

    max_rows = form.max_rows if form.max_rows is not None else DEFAULT_MAX_ROWS
    if not isinstance(max_rows, int) or not (1 <= max_rows <= MAX_ROWS_HARD_CAP):
        raise HTTPException(status_code=400, detail=f"行数上限需在 1–{MAX_ROWS_HARD_CAP} 之间")

    return {
        "name": name, "type": kind, "host": host, "port": port,
        "database": database, "username": username,
        "read_only": True,                      # 恒为 1：本层只写只读配置，不提供改写开关
        "max_rows": max_rows,
    }


# ── 端点 ──

@router.get("", dependencies=[Depends(require_app_token)])
async def list_connections() -> Dict[str, Any]:
    """一期恒为 0 或 1 条；返回数组形状，将来放开多库不用改契约。"""
    with _Conn() as conn:
        rows = conn.execute("SELECT * FROM db_connections ORDER BY id").fetchall()
    out = [_row_out(r) for r in rows]
    return {"connections": out, "count": len(out)}


@router.put("", dependencies=[Depends(require_app_token)])
async def save_connection(body: ConnectionForm) -> Dict[str, Any]:
    """新增或修改唯一那条（幂等）。改名会换行——否则「只有一条」的前提会被破坏。"""
    fields = _clean(body)

    with _Conn() as conn:
        current = _sole(conn)
        row_id: Optional[int] = current["id"] if current else None

        if body.password is None:
            if row_id is None:
                raise HTTPException(status_code=400, detail="首次保存必须填写密码")
            cipher = _cipher_of(conn, row_id)      # 不发 = 保持原密文
        else:
            cipher = _encrypt(body.password) if body.password else ""   # 空串 = 清除

        try:
            if row_id is not None and current["name"] == fields["name"]:
                conn.execute(
                    """UPDATE db_connections SET type=?, host=?, port=?, database=?, username=?,
                       password_cipher=?, read_only=?, max_rows=?, updated_at=datetime('now')
                       WHERE id=?""",
                    (fields["type"], fields["host"], fields["port"], fields["database"],
                     fields["username"], cipher, int(fields["read_only"]), fields["max_rows"], row_id),
                )
            else:
                # 首次新增，或改名：删旧插新必须在一个事务里，失败则整体回滚（不留孤儿行）
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if row_id is not None:
                        conn.execute("DELETE FROM db_connections WHERE id=?", (row_id,))
                    conn.execute(
                        """INSERT INTO db_connections
                           (name, type, host, port, database, username, password_cipher, read_only, max_rows)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (fields["name"], fields["type"], fields["host"], fields["port"],
                         fields["database"], fields["username"], cipher,
                         int(fields["read_only"]), fields["max_rows"]),
                    )
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"名称「{fields['name']}」已被占用，请换一个")

        saved = _sole(conn)

    return {"ok": True, "connection": _row_out(saved)}
