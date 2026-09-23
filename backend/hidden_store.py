# -*- coding: utf-8 -*-
"""隐藏模型存储（SQLite，零依赖）。
记录「厂商 slug + 模型名」的隐藏清单——纯展示层配置，不碰 Hermes 的 config.yaml。
（Hermes 的 models_discovered 厂商清单会被自动发现回写，删 config 项会被复活；
所以「从可选列表移除」用本存储实现。）"""
from __future__ import annotations

import sqlite3
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent / "hidden_models.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS hidden_models ("
        "vendor TEXT NOT NULL, model TEXT NOT NULL, "
        "created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY (vendor, model))"
    )
    return conn


def hidden_set(vendor_slug: str) -> set:
    """某厂商的隐藏模型集合。"""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT model FROM hidden_models WHERE vendor = ?", (vendor_slug,)
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def hide_model(vendor_slug: str, model_id: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO hidden_models (vendor, model) VALUES (?, ?)",
            (vendor_slug, model_id),
        )
        conn.commit()
    finally:
        conn.close()


def unhide_model(vendor_slug: str, model_id: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM hidden_models WHERE vendor = ? AND model = ?",
            (vendor_slug, model_id),
        )
        conn.commit()
    finally:
        conn.close()