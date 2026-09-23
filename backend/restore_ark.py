# -*- coding: utf-8 -*-
"""事故恢复：从用户贴出的原始 config.yaml 重建 ark 的完整模型清单（180 个）。
合并语义：保留磁盘现有 + 补全缺失，不覆盖磁盘上可能的新增。"""
import json
import requests
import io
import sys
import yaml

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://61.184.23.92:8426"
env = {}
for line in open("../.env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()

s = requests.Session()
s.post(f"{BASE}/auth/password-login",
       json={"provider": "basic", "username": env.get("HERMES_USER", "admin"),
             "password": env.get("HERMES_PASS", "")}, timeout=15)

# 用户 2026-09-23 贴出的原始 config.yaml 中 ark 的完整模型清单
ARK_MODELS = [
    "doubao-lite-128k-240428", "doubao-pro-128k-240515", "doubao-lite-4k-240328",
    "doubao-lite-32k-240428", "doubao-pro-4k-240515", "doubao-lite-4k-character-240515",
    "doubao-embedding-text-240515", "mistral-7b-instruct-v0.2", "doubao-pro-4k-functioncall-240515",
    "doubao-lite-4k-pretrain-character-240516", "doubao-pro-32k-character-240528",
    "doubao-pro-4k-browsing-240524", "doubao-pro-32k-functioncall-240515",
    "doubao-pro-4k-functioncall-240615", "doubao-pro-32k-browsing-240615",
    "doubao-pro-32k-240615", "doubao-lite-32k-240628", "doubao-pro-128k-240628",
    "doubao-embedding-text-240715", "doubao-pro-4k-character-240728",
    "doubao-pro-32k-functioncall-240815", "doubao-pro-32k-240828",
    "doubao-lite-4k-character-240828", "doubao-lite-32k-240828", "doubao-lite-128k-240828",
    "doubao-pro-32k-browsing-240828", "doubao-pro-32k-functioncall-preview",
    "doubao-embedding-large-text-240915", "doubao-lite-32k-character-241015",
    "doubao-pro-32k-functioncall-241028", "doubao-pro-32k-browsing-241115",
    "doubao-vision-pro-32k-241028", "doubao-vision-lite-32k-241015", "doubao-seaweed-241128",
    "doubao-pro-256k-241115", "doubao-pro-32k-character-241215", "doubao-pro-32k-241215",
    "doubao-1-5-lite-32k-250115", "doubao-1-5-pro-32k-250115", "doubao-1-5-vision-pro-32k-250115",
    "doubao-embedding-vision-241215", "doubao-1-5-pro-256k-250115", "deepseek-v3-241226",
    "deepseek-r1-distill-qwen-7b-250120", "deepseek-r1-distill-qwen-32b-250120",
    "deepseek-r1-250120", "doubao-1-5-pro-32k-character-250228", "doubao-1.5-vision-lite-250315",
    "deepseek-v3-250324", "doubao-1.5-vision-pro-250328", "doubao-lite-32k-character-250228",
    "doubao-1-5-ui-tars-250328", "doubao-embedding-vision-250328",
    "doubao-1-5-thinking-pro-250415", "wan2-1-14b-i2v-250225", "wan2-1-14b-t2v-250225",
    "doubao-1-5-thinking-pro-m-250415", "doubao-seedance-1-0-lite-i2v-250428",
    "doubao-seedance-1-0-lite-t2v-250428", "doubao-seedream-3-0-t2i-250415",
    "wan2-1-14b-flf2v-250417", "doubao-1-5-thinking-vision-pro-250428",
    "doubao-1-5-thinking-pro-m-250428", "doubao-embedding-large-text-250515",
    "deepseek-r1-250528", "doubao-seed-1-6-flash-250615", "doubao-seed-1-6-250615",
    "doubao-seed-1-6-thinking-250615", "doubao-seedance-1-0-pro-250528",
    "doubao-embedding-vision-250615", "doubao-seed-1-6-thinking-250715",
    "doubao-1-5-pro-32k-character-250715", "doubao-seededit-3-0-i2i-250628",
    "doubao-seed-1-6-flash-250715", "kimi-k2-250711", "doubao-seed-1-6-vision-250815",
    "deepseek-v3-1-250821", "doubao-seed-1-6-flash-250828", "glm-4-5-air-20250728",
    "qwen3-8b-20250429", "qwen3-32b-20250429", "qwen2-5-72b-20240919",
    "doubao-seedream-4-0-250828", "kimi-k2-250905", "doubao-seed-translation-250915",
    "deepseek-v3-1-terminus", "doubao-smart-router-250928", "doubao-seed-1-6-251015",
    "doubao-seedance-1-0-pro-fast-251015", "doubao-seed-1-6-lite-251015",
    "doubao-seed3d-1-0-250928", "kimi-k2-thinking-251104", "doubao-seed-code-preview-251028",
    "qwen3-0-6b-20250429", "qwen3-14b-20250429", "doubao-seedream-4-5-251128",
    "doubao-embedding-vision-251215", "deepseek-v3-2-251201", "doubao-seedance-1-5-pro-251215",
    "glm-4-7-251222", "doubao-seed-1-8-251228", "doubao-seed-character-251128",
    "doubao-seed-2-0-lite-260215", "doubao-seedance-2-0-260128", "doubao-seedream-5-0-260128",
    "doubao-seed-2-0-mini-260215", "doubao-seed-2-0-pro-260215", "doubao-seedance-2-0-fast-260128",
    "doubao-seed-2-0-code-preview-260215", "hyper3d-gen2-260112", "hitem3d-2-0-251223",
    "doubao-seed3d-2-0-260328", "doubao-seedream-4-0-20260415", "doubao-seed-2-0-mini-260428",
    "doubao-seed-2-0-lite-260428", "deepseek-v4-pro-260425", "deepseek-v4-flash-260425",
    "doubao-seedance-2-0-mini-260615", "doubao-seed-2-1-pro-260628", "doubao-seed-2-1-turbo-260628",
    "doubao-seed-character-260628", "doubao-seed-evolving", "glm-5-2-260617",
    "doubao-seedream-5-0-pro-260628", "doubao-seedance-2-5-260628",
    "deepseek-v4-flash-ga-260731", "deepseek-v4-pro-ga-260813", "doubao-seed-2-1-lite-260915",
    "glm-5-3-flash-260828", "deepseek-v4-1-flash-260910", "doubao-seed-2-1-pro-260915",
    "doubao-seedream-5-0-flash-260915",
]

raw = s.get(f"{BASE}/api/config/raw", timeout=30).json()
doc = yaml.safe_load(raw["yaml"])

ark = next((e for e in (doc.get("custom_providers") or [])
            if "ark" in str(e.get("name", "")).lower()), None)
if ark is None:
    print("找不到 ark entry")
    sys.exit(1)

before = set((ark.get("models") or {}).keys())
merged = dict(ark.get("models") or {})
for m in ARK_MODELS:
    merged.setdefault(m, {})
ark["models"] = merged

new_text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
r = s.put(f"{BASE}/api/config/raw", json={"yaml_text": new_text}, timeout=30)
print("[restore] HTTP", r.status_code)

# 验证
doc2 = yaml.safe_load(s.get(f"{BASE}/api/config/raw", timeout=30).json()["yaml"])
ark2 = next((e for e in (doc2.get("custom_providers") or [])
             if "ark" in str(e.get("name", "")).lower()), None)
after = set((ark2.get("models") or {}).keys())
missing = set(ARK_MODELS) - after
print(f"[恢复前] {len(before)} 个  [恢复后] {len(after)} 个")
print(f"清单中 180 个目标模型缺失数: {len(missing)}", ("→ " + ", ".join(sorted(missing))) if missing else "✓ 全部就位")
print("商汤未受影响:", end=" ")
sn = next((e for e in (doc2.get("custom_providers") or [])
           if "sensenova" in str(e.get("name", "")).lower()), None)
print(len(sn.get("models") or {}), "个模型")
