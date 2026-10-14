"""Read-only SQL guard (defense layer #1).

Enforces that a query is a single, read-only statement before it ever reaches
the database. The authoritative guarantee that nothing is written is the
ROLLBACK-scoped runner (query_runner) plus a SELECT-only DB account; this
syntax whitelist is the first, cheap line of defense that rejects the obvious
bad stuff early and returns a clear reason.
"""
from __future__ import annotations

import re

import sqlparse

from hermes_mcp.errors import ReadOnlyViolation

# Statements that are inherently read-only.
READONLY_FIRST = {"select", "with", "show", "explain", "describe", "desc"}

# Anything that can mutate data, schema, or session state.
WRITE_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "merge", "call", "grant", "revoke", "replace", "rename", "start", "stop",
    "kill", "analyze", "attach", "detach", "pragma", "vacuum", "reindex",
    "set", "commit", "rollback", "savepoint", "release", "load", "lock",
}

# Notorious DB-specific escalations / file access (regex net; over-matching
# is safer than under-matching).
DANGEROUS_PATTERNS = [
    re.compile(r"\binto\s+outfile\b", re.I),
    re.compile(r"\binto\s+dumpfile\b", re.I),
    re.compile(r"\bload_file\b", re.I),
    re.compile(r"\bsys_exec\b", re.I),
    re.compile(r"\bxp_cmdshell\b", re.I),
    re.compile(r"\bmaster\.\.xp_\w+", re.I),
    re.compile(r"@@\w+"),  # @@global.[xx] style server vars / version
]


def _significant(sql: str) -> list:
    """Flatten one statement into non-whitespace tokens."""
    parsed = sqlparse.parse(sql)
    if not parsed:
        return []
    return [t for t in parsed[0].flatten() if not t.is_whitespace]


def _first_word(tokens: list) -> str:
    if not tokens:
        return ""
    return tokens[0].normalized.strip().strip(";").lower()


def validate_readonly(sql: str | None) -> str:
    """Return the single normalized statement if it is safely read-only.

    Raises ReadOnlyViolation with a human reason otherwise. Never raises for
    quiet or empty input -- it raises.
    """
    raw = (sql or "").strip()
    if not raw:
        raise ReadOnlyViolation("empty statement")

    # 1) exactly one statement (blocks multi-statement / stray ';')
    statements = [s for s in sqlparse.split(raw) if s.strip()]
    if len(statements) != 1:
        raise ReadOnlyViolation("multiple statements or stray ';' are not allowed")

    stmt = statements[0].strip()
    tokens = _significant(stmt)
    if not tokens:
        raise ReadOnlyViolation("statement is empty after parsing")

    first = _first_word(tokens)
    if not first:
        raise ReadOnlyViolation("could not determine statement type")
    if first not in READONLY_FIRST:
        raise ReadOnlyViolation(
            f"statement must start with one of {sorted(READONLY_FIRST)}, got '{first}'"
        )

    # 2) a WITH must resolve to a SELECT (no DML disguised as CTE root)
    if first == "with":
        kws = {t.normalized.lower() for t in tokens if t.ttype in (sqlparse.tokens.Keyword.DML, sqlparse.tokens.Keyword.DDL)}
        if "select" not in kws and "show" not in kws:
            raise ReadOnlyViolation("WITH statement must ultimately SELECT")

    # 3) reject write / dangerous keywords
    for t in tokens:
        word = t.normalized.strip().strip(";").lower()
        if word in WRITE_KEYWORDS:
            raise ReadOnlyViolation(f"forbidden keyword: '{word}'")

    # 4) dangerous function / pattern net over raw text
    for rx in DANGEROUS_PATTERNS:
        m = rx.search(stmt)
        if m:
            raise ReadOnlyViolation(f"forbidden pattern: '{m.group(0)}'")

    return stmt


def is_readonly(sql: str | None) -> tuple[bool, str]:
    """Non-raising wrapper: returns (ok, reason_or_statement)."""
    try:
        return True, validate_readonly(sql)
    except ReadOnlyViolation as e:
        return False, e.message