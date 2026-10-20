# -*- coding: utf-8 -*-
"""数据源类型元数据 —— 后端校验与前端表单**共用的一份事实**。

为什么必须由控制台来管这份知识（2026-10-20 实测）：
  hermes-mcp 的 ``register_connection`` **不校验类型**（``type="nosuchtype"`` 也能 ok:true），
  也**不校验完整性**（只给 name+type 就注册成功，存进 host=null / database=''）。
  而失败时返回的错误又只有一句通用文案（拿不到原因）。
  → 所以「哪些类型合法、每种要填什么」只能由控制台管，而且这是**唯一能给用户可读报错**的地方。

类型集来自 hermes-mcp 的 ``connections.py:DIALECTS``，但**故意只取子集**：
  ``type=api``（HTTP 接口当表查）不做，两个理由：
    ① 表单完全是另一套字段（api_url/api_method/api_params/api_data_path）；
    ② ``list_connections`` **不回传** api_* 字段（见其 ``describe_connection``），
       编辑一条已存在的 api 数据源时读不回来，做不了编辑。

字段规则（同样来自实测 + 源码 ``connections.py``）：
  关系库：host / port / database / username（+ password）
  sqlite：**只认 database = 数据库文件的路径**，host/port/username 都不用
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 字段中文名。后端拿它拼校验报错，前端拿它渲染表单标签 —— 一份就够。
FIELD_LABELS: Dict[str, str] = {
    "name": "名称",
    "type": "类型",
    "host": "主机",
    "port": "端口",
    "database": "库名",
    "username": "账号",
    "password": "密码",
    "max_rows": "行数上限",
}

# 关系库共用的字段集（password 可留空=不改，见 dbconn 的三态语义）
_RELATIONAL_FIELDS = ["host", "port", "database", "username", "password", "max_rows"]
# sqlite 只用到路径 + 行数上限
_FILE_FIELDS = ["database", "max_rows"]

# 默认端口是**业界惯例值，不是 MCP 给的**（MCP 没有类型元数据接口），用户可改。
# 已知实例：ob_mysql 用 2881、opengauss_business 用 15432（都是各自环境的自定义值）。
TYPES: List[Dict[str, Any]] = [
    {"id": "mysql", "label": "MySQL", "group": "relational", "default_port": 3306,
     "hints": {"database": "库名填 schema 名"}},
    {"id": "postgresql", "label": "PostgreSQL", "group": "relational", "default_port": 5432,
     "hints": {"database": "库名填 database"}},
    {"id": "sqlserver", "label": "SQL Server", "group": "relational", "default_port": 1433,
     "hints": {"database": "库名填 database"}},
    {"id": "oracle", "label": "Oracle", "group": "relational", "default_port": 1521,
     "hints": {"database": "库名填服务名（service_name）"}},
    {"id": "oceanbase_mysql", "label": "OceanBase（MySQL 模式）", "group": "relational",
     "default_port": 2881, "hints": {"database": "库名填 database"}},
    {"id": "oceanbase_oracle", "label": "OceanBase（Oracle 模式）", "group": "relational",
     "default_port": 2881, "hints": {"database": "库名填服务名"}},
    {"id": "opengauss", "label": "openGauss", "group": "relational", "default_port": 5432,
     "hints": {"database": "库名填 database"}},
    {"id": "dameng", "label": "达梦 DM", "group": "relational", "default_port": 5236,
     "hints": {"database": "库名填 database"}},
    {"id": "sqlite", "label": "SQLite 文件", "group": "file", "default_port": None,
     "hints": {"database": "数据库文件的绝对路径"}},
]

_BY_ID = {t["id"]: t for t in TYPES}

#: 前端 PUT/POST 时会带上、但不参与「数据源类型」本身的字段
META_FIELDS = ("name", "type", "password")


def get_type(type_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """按 id 取类型定义；不合法返回 None（调用方负责报错）。"""
    return _BY_ID.get((type_id or "").strip().lower())


def fields_for(type_id: Optional[str]) -> List[str]:
    """该类型要填哪些字段（不含 name/type）。类型不合法返回空列表。"""
    meta = get_type(type_id)
    if meta is None:
        return []
    return list(_FILE_FIELDS if meta["group"] == "file" else _RELATIONAL_FIELDS)


def catalog() -> Dict[str, Any]:
    """给前端的类型目录：类型清单 + 字段中文名。"""
    return {"types": TYPES, "field_labels": FIELD_LABELS}
