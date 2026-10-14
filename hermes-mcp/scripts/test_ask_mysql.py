# -*- coding: utf-8 -*-
"""本机联调测试：对注册的 mysql_local 跑几条真实 ask（问数）。

需要环境变量（与联调阶段约定一致）：
    HERESMCP_DB_KEY          = 写/读共享 SQLite 密码用的 key（测试期固定即可）
    HERESMCP_LLM_BASE_URL    = ark OpenAI 兼容端点
    HERESMCP_LLM_API_KEY     = ark key
    HERESMCP_LLM_MODEL       = 模型，如 DeepSeek-V4.1-Flash
用法：
    HERESMCP_DB_KEY=... HERESMCP_LLM_BASE_URL=... ... python scripts/test_ask_mysql.py
"""
from __future__ import annotations

import os


def main():
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    from hermes_mcp import sqlgen
    from hermes_mcp.config import load_settings
    from hermes_mcp.store import ConnectionStore

    s = load_settings()
    store = ConnectionStore(s.resolved_db_config_path, s.enc_key)
    rec = store.get_record("mysql_local")
    if rec is None:
        print("没有 mysql_local 连接，请先注册。现有:", [c["name"] for c in store.list()])
        return

    questions = [
        "武汉的客户一共有多少位？",
        "每种产品各卖出了多少件？按销量从高到低排。",
        "待支付的订单分别是谁、买了什么？",
    ]
    for q in questions:
        print("=" * 60)
        print("问:", q)
        try:
            r = sqlgen.ask_from_connection(rec, q, s)
            print("ok:", r.get("ok"))
            print("sql:", r.get("sql"))
            print("row_count:", r.get("row_count"), "| truncated:", r.get("truncated"))
            if r.get("columns"):
                print("columns:", r["columns"])
                for row in r.get("rows", [])[:6]:
                    print("  ", row)
            print("answer:", r.get("answer"))
        except Exception as e:  # noqa: BLE001
            print("ERROR:", type(e).__name__, str(e)[:600])


if __name__ == "__main__":
    main()