"""Shared-SQLite integration probe (objective pass/fail for 联调).

Both sides can run this against the SAME db_connections.db to verify the
contract: what's in the file, whether the shared HERMESMCP_DB_KEY decrypts each
stored password (never printed), and optionally whether a live read-only
connect (`SELECT 1`) succeeds.

Never prints passwords. Secrets come from the environment / args.

Usage:
    python scripts/verify_contract.py [--db-config PATH] [--key KEY] [--test] [--connection NAME]
      --db-config   path to shared db_connections.db (default HERMESMCP_DB_CONFIG)
      --key         HERMESMCP_DB_KEY (default env)  -- required to decrypt
      --test        attempt a live read-only `SELECT 1` on each decrypted connection
      --connection  only inspect/test this connection name
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from hermes_mcp.config import load_settings  # noqa: E402
from hermes_mcp.connections import get_engine  # noqa: E402
from hermes_mcp.errors import HermesMCPError  # noqa: E402
from hermes_mcp.query_runner import run_readonly  # noqa: E402
from hermes_mcp.store import ConnectionStore  # noqa: E402


def _redacted(record: dict) -> dict:
    d = {k: v for k, v in record.items() if k != "password"}
    d["has_password"] = bool(record.get("password_cipher"))
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes-MCP shared-SQLite contract probe")
    ap.add_argument("--db-config", default=None)
    ap.add_argument("--key", default=None)
    ap.add_argument("--test", action="store_true", help="live connect SELECT 1")
    ap.add_argument("--connection", default=None, help="only this connection name")
    args = ap.parse_args()

    settings = load_settings()
    db_path = Path(args.db_config or settings.resolved_db_config_path)
    key = args.key or settings.enc_key

    print(f"[probe] shared db: {db_path}  exists={db_path.exists()}")
    print(f"[probe] key present: {bool(key)}")

    errs = 0
    if not db_path.exists():
        print("[FAIL] db_connections.db not found on this path")
        return 2

    store = ConnectionStore(str(db_path), key or "")
    records = store.list()
    if args.connection:
        records = [r for r in records if r["name"] == args.connection]
        if not records:
            print(f"[FAIL] connection {args.connection!r} not found")
            return 1

    if not records:
        print("[info] no connections in the file")
        return 0

    print(f"[info] {len(records)} connection(s) found\n")
    for c in records:
        name = c["name"]
        print(f"--- {name} ({c['type']}) ---")
        print(f"    host/port/db: {c.get('host')}:{c.get('port')}/{c.get('database')}")
        print(f"    read_only={c['read_only']} has_password={c['has_password']} status={c.get('status')}")

        # decrypt check (never print the password)
        if not c["has_password"]:
            print("    decrypt: N/A (no password)")
        elif not key:
            print("    decrypt: SKIP (no HERMESMCP_DB_KEY provided)")
            errs += 1
        else:
            try:
                rec = store.get_record(name)
                if rec["password"]:
                    print("    decrypt: OK")
                else:
                    print("    decrypt: OK (empty recover)")
            except Exception as e:
                print(f"    decrypt: FAIL ({type(e).__name__})")
                errs += 1

        # optional live read-only connect
        if args.test and c["has_password"]:
            if not key:
                continue
            try:
                rec = store.get_record(name)
                engine = get_engine(rec)
                r = run_readonly(engine, "SELECT 1", max_rows=1)
                print(f"    connect: OK (SELECT 1) [{r['elapsed_ms']}ms]")
            except Exception as e:
                kind = type(e).__module__.split(".")[0] if getattr(e, "__module__", "") else ""
                print(f"    connect: FAIL ({type(e).__name__}: {getattr(e, 'message', str(e).splitlines()[0][:80])})")
                errs += 1

    print("\n" + (f"[RESULT] FAIL ({errs} issue(s))" if errs else "[RESULT] PASS"))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())