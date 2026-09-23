# -*- coding: utf-8 -*-
"""模型配置设计探针：确认 ① endpoint id 形态 ② api_key 回读是否脱敏 ③ validate 返回结构。"""
import json
import os
import sys

import requests

BASE = os.environ.get("HERMES_BASE", "http://localhost:8426")
USER = os.environ.get("HERMES_USER", "admin")
PASS = os.environ.get("HERMES_PASS", "")

s = requests.Session()
r = s.post(f"{BASE}/auth/password-login",
           json={"provider": "basic", "username": USER, "password": PASS}, timeout=15)
print("[login]", r.status_code)
if r.status_code != 200:
    sys.exit(1)

# ① ② 列表原始返回
r = s.get(f"{BASE}/api/providers/custom-endpoints", timeout=15)
print(f"[list] HTTP {r.status_code}")
print(json.dumps(r.json(), ensure_ascii=False, indent=2)[:4000])

# ③ validate 返回结构（无效 key，只为看响应 schema）
r = s.post(f"{BASE}/api/providers/custom-endpoints/validate",
           json={"name": "probe-test", "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
                 "model": "deepseek-v4-flash", "api_key": "sk-invalid-probe-key",
                 "api_mode": "chat_completions"}, timeout=90)
print(f"[validate] HTTP {r.status_code}")
print(json.dumps(r.json(), ensure_ascii=False)[:1500])
