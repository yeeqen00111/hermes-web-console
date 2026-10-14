"""Build a local SQLite sample DB (customers / products / orders) and register
it with the shared connection store under the name "sample".

Run:  python scripts/make_sample_db.py
Requires the venv with the installed package (or src on path).
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from hermes_mcp.config import load_settings  # noqa: E402
from hermes_mcp.store import ConnectionStore  # noqa: E402


def build(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            country TEXT
        );
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            price REAL
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            product_id INTEGER NOT NULL REFERENCES products(id),
            quantity INTEGER,
            order_date TEXT
        );
        """
    )
    cust = [(1, "Alice", "CN"), (2, "Bob", "US"), (3, "Carol", "CN")]
    prod = [(1, "Lamp", 89.0), (2, "Desk", 240.0), (3, "Chair", 120.5)]
    orders = [
        (1, 1, 1, 2, "2024-08-01"),
        (2, 2, 2, 1, "2024-08-05"),
        (3, 1, 3, 4, "2024-09-02"),
        (4, 3, 2, 2, "2024-09-15"),
    ]
    conn.executemany("INSERT INTO customers VALUES (?,?,?)", cust)
    conn.executemany("INSERT INTO products VALUES (?,?,?)", prod)
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", orders)
    conn.commit()
    conn.close()


def main():
    settings = load_settings()
    sample = settings.resolved_sample_db_path
    build(sample)
    store = ConnectionStore(settings.resolved_db_config_path, settings.enc_key)
    store.upsert(
        "sample",
        type_="sqlite",
        database=str(sample),
        password=None,  # sqlite needs no password
        read_only=True,
        max_rows=200,
    )
    print(f"OK sample DB: {sample}")
    print(f"OK connections: {[c['name'] for c in store.list()]}")


if __name__ == "__main__":
    main()