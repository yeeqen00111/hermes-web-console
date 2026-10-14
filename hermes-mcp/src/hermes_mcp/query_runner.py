"""Read-only SELECT runner (defense layers #2/#3).

Executes a validated read-only statement inside an explicit transaction that is
always ROLLED BACK (never COMMITted), caps rows to `max_rows`, and reports
truncation + timing. Errors are wrapped in QueryError with a scrubbed message.
"""
from __future__ import annotations

import datetime
import decimal
import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from hermes_mcp.errors import QueryError, ReadOnlyViolation
from hermes_mcp.security import validate_readonly


def _serialize(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return v.hex()
    return str(v)


def run_readonly(engine, sql, max_rows: int = 200, timeout_s: float | None = None) -> dict:
    """Return {columns, rows, truncated, row_count, sql, elapsed_ms} or raise
    ReadOnlyViolation / QueryError."""
    try:
        stmt = validate_readonly(sql)
    except ReadOnlyViolation as e:
        raise  # caller decides how to surface

    start = time.perf_counter()
    result = {
        "columns": [],
        "rows": [],
        "truncated": False,
        "row_count": 0,
        "sql": stmt,
        "elapsed_ms": 0,
    }

    conn = engine.connect()
    try:
        conn = conn.execution_options(statement_timeout=timeout_s) if timeout_s is not None else conn
        cur = conn.execute(text(stmt))
        columns = list(cur.keys())
        rows, truncated = [], False
        for i, item in enumerate(cur.fetchmany(max_rows + 1)):
            if i >= max_rows:
                truncated = True
                break
            rows.append([_serialize(v) for v in item])
        result.update(columns=columns, rows=rows, truncated=truncated, row_count=len(rows))
    except SQLAlchemyError as e:
        msg = str(e).splitlines()[0] if str(e) else "unknown DB error"
        raise QueryError(f"query failed: {msg}") from e
    finally:
        try:
            conn.rollback()  # never commit -> nothing written, ever
        except SQLAlchemyError:
            pass
        conn.close()

    result["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
    return result