# -*- coding: utf-8 -*-
"""Agent 身份文件设计探针（**只读**）：验证 agent-identity-files-design.md §7 阶段 1 的假设。

① 直连 dashboard（§7.0）：
   GET /api/profiles            能否给出 default 的 home 路径
   GET /api/profiles/default/soul   SOUL.md 专用端点是否可用
   GET /api/memory              内置记忆状态（builtin_files 大小）
   GET /api/fs/read-text        能否按绝对路径读 <home>/memories/*.md

② 经自研后端（§7.1，TestClient 起进程内的 app，**不占端口**）：
   GET /api/agent-files        三文件汇总
   GET /api/agent-files/{name} soul / memory / user
   并与 ① 的尺寸**交叉比对** —— 两边不一致就说明我们的 home 解析或通道选错了

**全程只发 GET**，不产生任何写操作；内容只回显长度 + 极短预览，不 dump 全文。
用法：cd backend && py310 probe_agentfiles.py
"""
from config import hc

PREVIEW = 60


def _json(label, resp):
    print(f"[{label}] HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        print(f"  非 JSON 响应：{resp.text[:200]!r}")
        return None
    if isinstance(data, dict):
        print(f"  字段：{list(data.keys())}")
    return data


def main():
    print("── ① 直连 dashboard ──")
    direct = {}
    # profile 清单 → default 的 home
    home = None
    data = _json("profiles", hc.request("GET", "/api/profiles"))
    if isinstance(data, dict):
        for p in data.get("profiles") or []:
            name = p.get("name")
            is_default = p.get("is_default")
            print(f"  {'[default]' if is_default else '         '} {name:<12} path={p.get('path')}")
            if is_default:
                home = p.get("path")
    print(f"  → default home = {home!r}")

    # SOUL.md 专用端点
    data = _json("soul", hc.request("GET", "/api/profiles/default/soul"))
    if isinstance(data, dict):
        content = data.get("content") or ""
        direct["soul"] = len(content.encode("utf-8"))
        print(f"  exists={data.get('exists')} 字节数={direct['soul']} 预览={content[:PREVIEW]!r}")

    # 内置记忆状态
    data = _json("memory", hc.request("GET", "/api/memory"))
    if isinstance(data, dict):
        print(f"  active={data.get('active')!r} builtin_files={data.get('builtin_files')}")

    # memories/ 绝对路径文本通道
    if home:
        for name, fname in (("memory", "MEMORY.md"), ("user", "USER.md")):
            path = f"{str(home).rstrip('/')}/memories/{fname}"
            data = _json(f"read-text {fname}", hc.request("GET", "/api/fs/read-text", params={"path": path}))
            if isinstance(data, dict):
                text = data.get("text") or ""
                direct[name] = data.get("byteSize")
                print(f"  byteSize={data.get('byteSize')} truncated={data.get('truncated')} "
                      f"binary={data.get('binary')} 预览={text[:PREVIEW]!r}")

    # ── ② 经自研后端（进程内起 app，只发 GET） ──
    print("\n── ② 经自研后端 /api/agent-files（只读） ──")
    from fastapi.testclient import TestClient
    import main as backend_main

    with TestClient(backend_main.app) as client:
        resp = client.get("/api/agent-files")
        print(f"[汇总] HTTP {resp.status_code}")
        print(f"  {resp.json() if resp.status_code == 200 else resp.text[:200]}")

        for name in ("soul", "memory", "user"):
            resp = client.get(f"/api/agent-files/{name}")
            if resp.status_code != 200:
                print(f"[{name}] HTTP {resp.status_code} {resp.text[:160]}")
                continue
            body = resp.json()
            print(f"[{name}] HTTP 200 exists={body['exists']} byteSize={body['byteSize']} "
                  f"truncated={body['truncated']} 预览={(body['content'] or '')[:PREVIEW]!r}")
            if body["exists"] and name in direct and direct[name] != body["byteSize"]:
                print(f"  ⚠️ 与直连尺寸不一致：direct={direct[name]} backend={body['byteSize']}")


if __name__ == "__main__":
    main()
