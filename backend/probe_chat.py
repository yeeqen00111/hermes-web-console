# -*- coding: utf-8 -*-
"""直连 dashboard 验证对话链路：登录 → ws-ticket → /api/ws → session.create → prompt.submit。
用法：conda py310 的 python 直接运行本文件（在 backend 目录下）。"""
import asyncio
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chat import HERMES_BASE, stream_turn  # noqa: E402

USER = os.environ.get("HERMES_USER", "admin")
PASS = os.environ.get("HERMES_PASS", "")

s = requests.Session()
r = s.post(f"{HERMES_BASE}/auth/password-login",
           json={"provider": "basic", "username": USER, "password": PASS}, timeout=15)
print(f"[login] HTTP {r.status_code} {r.text[:120]}")
if r.status_code != 200:
    sys.exit(1)


async def on_event(name: str, params: dict) -> None:
    p = params.get("payload") if isinstance(params, dict) else None
    p = p if p is not None else params
    text = p.get("text", "") if isinstance(p, dict) else ""
    line = text if text else str(p)[:160]
    print(f"[{name}] {line}")


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else "hi"
    print(f"[probe] base={HERMES_BASE} text={text!r}")
    summary = asyncio.run(stream_turn(s, text=text, on_event=on_event))
    print("[summary]", summary)
