"""Shared SQLite connection registry (defense layer #4 + config source of truth).

Stores connection records in SQLite so the web-console (management) and this
MCP server (execution) agree on the same connections. Passwords are encrypted
at rest (Fernet) with a key derived from HERMESMCP_DB_KEY; without that key we
refuse to persist any non-empty password (no plaintext fallback).
"""
from __future__ import annotations

import base64
import hashlib
import sqlite3
import threading

from cryptography.fernet import Fernet

from hermes_mcp.connections import describe_connection
from hermes_mcp.errors import ConfigError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS db_connections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    type            TEXT NOT NULL,
    host            TEXT,
    port            INTEGER,
    database        TEXT,
    username        TEXT,
    password_cipher TEXT,          -- Fernet token (self-contained IV)
    read_only       INTEGER NOT NULL DEFAULT 1,
    max_rows        INTEGER,
    status          TEXT,
    last_error      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class ConnectionStore:
    def __init__(self, path: str, enc_key: str = ""):
        self._path = str(path)
        self._enc_key = enc_key or ""
        self._lock = threading.Lock()
        with self._new_conn() as conn:
            conn.executescript(_SCHEMA)

    # ---- low level ----
    def _new_conn(self):
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _fernet(self):
        if not self._enc_key:
            raise ConfigError(
                "HERMESMCP_DB_KEY is not set; refusing to store database passwords"
            )
        key = base64.urlsafe_b64encode(
            hashlib.sha256(self._enc_key.encode("utf-8")).digest()
        )
        return Fernet(key)

    def _encrypt(self, password: str) -> str:
        return self._fernet().encrypt(password.encode("utf-8")).decode()

    def _decrypt(self, cipher: str) -> str:
        if not cipher:
            return ""
        return self._fernet().decrypt(cipher.encode("utf-8")).decode()

    # ---- public API ----
    def list(self) -> list[dict]:
        with self._lock, self._new_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM db_connections ORDER BY name COLLATE NOCASE"
            ).fetchall()
        out = []
        for r in rows:
            d = describe_connection(dict(r))
            d["has_password"] = bool(r["password_cipher"])
            d["status"], d["last_error"] = r["status"], r["last_error"]
            out.append(d)
        return out

    def get_record(self, name: str) -> dict | None:
        """Internal: full record including decrypted password (never log this)."""
        with self._lock, self._new_conn() as conn:
            r = conn.execute(
                "SELECT * FROM db_connections WHERE name=?", (name,)
            ).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["password"] = self._decrypt(d.get("password_cipher") or "")
        return d

    def upsert(
        self,
        name: str,
        *,
        type_: str,
        database: str,
        password: str | None = None,
        host: str = "",
        port: int | None = None,
        username: str = "",
        read_only: bool = True,
        max_rows: int | None = 200,
    ) -> dict:
        """Create or update a connection. password tri-state:
        None -> keep existing; "" -> clear; non-empty -> encrypt new."""
        existing = None
        with self._lock, self._new_conn() as conn:
            existing = conn.execute(
                "SELECT password_cipher FROM db_connections WHERE name=?", (name,)
            ).fetchone()
        cipher = existing["password_cipher"] if existing else ""
        if password is not None:
            cipher = self._encrypt(password) if password else ""

        with self._lock, self._new_conn() as conn:
            conn.execute(
                """
                INSERT INTO db_connections
                  (name,type,database,host,port,username,password_cipher,
                   read_only,max_rows)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                  type=excluded.type, database=excluded.database,
                  host=excluded.host, port=excluded.port,
                  username=excluded.username,
                  password_cipher=excluded.password_cipher,
                  read_only=excluded.read_only, max_rows=excluded.max_rows,
                  updated_at=datetime('now')
                """,
                (
                    name, type_, database, host or None, port, username or None,
                    cipher, int(read_only), max_rows,
                ),
            )
            conn.commit()
        return self.get_record(name)  # includes decrypted password (internal)

    def delete(self, name: str) -> bool:
        with self._lock, self._new_conn() as conn:
            cur = conn.execute("DELETE FROM db_connections WHERE name=?", (name,))
            conn.commit()
            return cur.rowcount > 0

    def set_status(self, name: str, status: str, last_error: str = ""):
        with self._lock, self._new_conn() as conn:
            conn.execute(
                "UPDATE db_connections SET status=?, last_error=?, updated_at=datetime('now') WHERE name=?",
                (status, last_error or None, name),
            )
            conn.commit()