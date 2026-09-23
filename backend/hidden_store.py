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
        "display_name TEXT, "
        "context_length INTEGER, reasoning_effort TEXT, "
        "is_default INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY (vendor, model))"
    )
    # 旧库迁移：补 display_name / is_default 列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(custom_models)")]
    if "display_name" not in cols:
        conn.execute("ALTER TABLE custom_models ADD COLUMN display_name TEXT")
    if "is_default" not in cols:
        conn.execute("ALTER TABLE custom_models ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0")
    return conn


def set_default(vendor_slug: str, model: str,
                display_name=None, context_length=None, reasoning_effort=None) -> None:
    """把 (vendor, model) 标记为当前默认：先清全表标记，再确保条目存在并置位。"""
    conn = _conn()
    try:
        conn.execute("UPDATE custom_models SET is_default = 0")
        row = conn.execute(
            "SELECT 1 FROM custom_models WHERE vendor = ? AND model = ?",
            (vendor_slug, model),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO custom_models (vendor, model, display_name, context_length, "
                "reasoning_effort, is_default) VALUES (?, ?, ?, ?, ?, 1)",
                (vendor_slug, model, display_name, context_length, reasoning_effort),
            )
        else:
            conn.execute(
                "UPDATE custom_models SET is_default = 1 WHERE vendor = ? AND model = ?",
                (vendor_slug, model),
            )
        conn.execute(
            "DELETE FROM hidden_models WHERE vendor = ? AND model = ?",
            (vendor_slug, model),
        )
        conn.commit()
    finally:
        conn.close()


def get_default():
    """当前默认条目（{vendor, model, ...}）或 None。"""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT vendor, model, display_name, context_length, reasoning_effort "
            "FROM custom_models WHERE is_default = 1"
        ).fetchone()
        if row is None:
            return None
        return {"vendor": row[0], "model": row[1], "display_name": row[2],
                "context_length": row[3], "reasoning_effort": row[4]}
    finally:
        conn.close()


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
            "SELECT model, display_name, context_length, reasoning_effort FROM custom_models "
            "WHERE vendor = ? ORDER BY model", (vendor_slug,)
        ).fetchall()
        return [{"model": r[0], "display_name": r[1], "context_length": r[2],
                 "reasoning_effort": r[3]} for r in rows]
    finally:
        conn.close()


def list_manual() -> list:
    """全部厂商的手动模型条目（[{vendor, model, display_name, context_length, reasoning_effort}]）。"""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT vendor, model, display_name, context_length, reasoning_effort FROM custom_models "
            "ORDER BY vendor, model"
        ).fetchall()
        return [{"vendor": r[0], "model": r[1], "display_name": r[2],
                 "context_length": r[3], "reasoning_effort": r[4]}
                for r in rows]
    finally:
        conn.close()


def add_custom(vendor_slug: str, model: str, display_name=None, context_length=None, reasoning_effort=None) -> None:
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO custom_models (vendor, model, display_name, context_length, reasoning_effort) "
            "VALUES (?, ?, ?, ?, ?)",
            (vendor_slug, model, display_name, context_length, reasoning_effort),
        )
        conn.commit()
    finally:
        conn.close()


def rename_custom(vendor_slug: str, old: str, model: str, display_name=None,
                  context_length=None, reasoning_effort=None) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE custom_models SET "
            "model = COALESCE(?, model), "
            "display_name = COALESCE(?, display_name), "
            "context_length = COALESCE(?, context_length), "
            "reasoning_effort = COALESCE(?, reasoning_effort) "
            "WHERE vendor = ? AND model = ?",
            (model, display_name, context_length, reasoning_effort, vendor_slug, old),
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