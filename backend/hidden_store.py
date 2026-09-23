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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS custom_models ("
        "vendor TEXT NOT NULL, model TEXT NOT NULL, "
        "context_length INTEGER, reasoning_effort TEXT, "
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


# ── 手动添加的模型（元数据在 SQLite，Hermes config 不写）──

def list_custom(vendor_slug: str) -> list:
    """某厂商的手动模型条目（SQLite）。"""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT model, context_length, reasoning_effort FROM custom_models "
            "WHERE vendor = ? ORDER BY model", (vendor_slug,)
        ).fetchall()
        return [{"model": r[0], "context_length": r[1], "reasoning_effort": r[2]} for r in rows]
    finally:
        conn.close()


def list_manual() -> list:
    """全部厂商的手动模型条目（[{vendor, model, context_length, reasoning_effort}]）。"""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT vendor, model, context_length, reasoning_effort FROM custom_models "
            "ORDER BY vendor, model"
        ).fetchall()
        return [{"vendor": r[0], "model": r[1], "context_length": r[2], "reasoning_effort": r[3]}
                for r in rows]
    finally:
        conn.close()


def add_custom(vendor_slug: str, model: str, context_length=None, reasoning_effort=None) -> None:
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO custom_models (vendor, model, context_length, reasoning_effort) "
            "VALUES (?, ?, ?, ?)",
            (vendor_slug, model, context_length, reasoning_effort),
        )
        conn.commit()
    finally:
        conn.close()


def rename_custom(vendor_slug: str, old: str, new: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE custom_models SET model = ? WHERE vendor = ? AND model = ?",
            (new, vendor_slug, old),
        )
        conn.commit()
    finally:
        conn.close()


def delete_custom(vendor_slug: str, model: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM custom_models WHERE vendor = ? AND model = ?",
            (vendor_slug, model),
        )
        conn.commit()
    finally:
        conn.close()