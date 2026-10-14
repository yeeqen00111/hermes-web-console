"""Natural-language -> read-only SQL (the `ask` tool backend).

Pipeline: schema_map -> prompt a model -> validate generated SQL (read-only
guard) -> run read-only -> compose an answer. The model call is behind a
`ModelBackend` so the whole flow is deterministically testable offline with a
stub, and usable for real with OpenAI-compatible endpoints once a key is set.

Model selection (per design §10-7): follow the model Hermes is configured with
(parse HERMESMCP_CONFIG_PATH -> config.yaml, best-effort) with a hard fallback
to HERMESMCP_LLM_MODEL.
"""
from __future__ import annotations

import json

import httpx

from hermes_mcp.errors import ConfigError, HermesMCPError, QueryError, ReadOnlyViolation
from hermes_mcp.introspection import get_schema_map
from hermes_mcp.query_runner import run_readonly
from hermes_mcp.security import validate_readonly

NO_SQL_PREFIX = "NO_SQL:"
MAX_ATTEMPTS = 2
_SYSTEM = (
    "You are a SQL expert for read-only analytics. Given the database schema "
    "and a user question, produce a SINGLE read-only SQL statement "
    "(SELECT/WITH only) that answers it.\n"
    "Rules:\n"
    "- Return ONLY the SQL. No markdown fences, no explanation, no trailing ';'.\n"
    "- Only SELECT/WITH/SHOW/EXPLAIN/DESCRIBE. Never modify data.\n"
    "- Use the exact table and column names from the schema. Use pragmatic SQL.\n"
    "- If the question cannot be answered from this schema, respond with a "
    "single line starting with 'NO_SQL:' followed by a short reason.\n"
)

# Best-effort resolution of the configured model from a Hermes config.yaml.
# Hermes config schema is treated as unknown here (no guessing): we probe a few
# plausible locations and bail to the env fallback if anything odd is found.
_MODEL_CANDIDATE_KEYS = (
    "model", "default_model", "deployment",
)


def _configured_model(content: str) -> str | None:
    try:
        import yaml
        doc = yaml.safe_load(content)
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    for scope in (doc.get("agent"), doc.get("model", {})):
        if not isinstance(scope, dict):
            continue
        for k in _MODEL_CANDIDATE_KEYS:
            val = scope.get(k)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def resolve_model(settings) -> str:
    """Follow Hermes config.yaml model; otherwise HERMESMCP_LLM_MODEL."""
    if getattr(settings, "hermes_config_path", ""):
        from pathlib import Path
        p = Path(settings.hermes_config_path)
        if p.exists():
            try:
                m = _configured_model(p.read_text(encoding="utf-8"))
                if m:
                    return m
            except Exception:
                pass
    if getattr(settings, "llm_model", ""):
        return settings.llm_model
    return "glm-5.3-flash"  # last-resort default


def build_schema_prompt(schema_map: dict) -> str:
    """Compact JSON of tables -> columns for the model context."""
    return json.dumps(schema_map.get("tables", {}), ensure_ascii=False, indent=0)


# ---------- model backends ----------


class ModelBackend:
    """Interface for the NL->SQL (and answer) model call."""

    def generate_sql(self, system: str, schema_prompt: str, question: str) -> str:
        raise NotImplementedError

    def generate_answer(self, question: str, schema_prompt: str, sql: str, data_summary: str) -> str:
        raise NotImplementedError


class OpenAICompatBackend(ModelBackend):
    """Calls an OpenAI-compatible /chat/completions endpoint (reuse ark custom)."""

    def __init__(self, base_url: str, api_key: str, model: str):
        if not base_url or not api_key:
            raise ConfigError(
                "ask generation needs HERMESMCP_LLM_BASE_URL and HERMESMCP_LLM_API_KEY "
                "(OpenAI-compatible endpoint); or pass a stub backend for offline tests."
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def _complete(self, messages: list[dict]) -> str:
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "messages": messages, "temperature": 0.0},
                timeout=60.0,
            )
        except httpx.HTTPError as e:
            raise HermesMCPError(f"model endpoint unreachable: {type(e).__name__}") from e
        if resp.status_code != 200:
            raise HermesMCPError(f"model endpoint HTTP {resp.status_code}")
        try:
            return resp.json()["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, ValueError):
            raise HermesMCPError("unexpected model response shape")

    def generate_sql(self, system: str, schema_prompt: str, question: str) -> str:
        return self._complete([
            {"role": "system", "content": system + "\nSchema:\n" + schema_prompt},
            {"role": "user", "content": f"Question: {question}"},
        ])

    def generate_answer(self, question: str, schema_prompt: str, sql: str, data_summary: str) -> str:
        prompt = (
            f"Question: {question}\nSQL used: {sql}\nResult: {data_summary}\n"
            "Answer the question concisely based on this data (Chinese)."
        )
        return self._complete([
            {"role": "system", "content": "You answer data questions concisely."},
            {"role": "user", "content": prompt},
        ])


class StubBackend(ModelBackend):
    """Deterministic backend for offline tests: returns a fixed SQL (and answer)."""

    def __init__(self, sql: str, answer: str = "(stub answer)"):
        self.sql = sql
        self.answer = answer

    def generate_sql(self, system, schema_prompt, question):
        return self.sql

    def generate_answer(self, question, schema_prompt, sql, data_summary):
        return self.answer


# ---------- ask pipeline ----------


def _clean_model_sql(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        # strip optional markdown fences
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw.rstrip(";")


def ask(connection: dict, question: str, settings, engine=None, backend: ModelBackend | None = None) -> dict:
    """Run the NL->SQL pipeline for one question against a connection record.

    Returns {answer, sql, columns, rows, truncated, row_count} or raises
    HermesMCPError (ConfigError / ReadOnlyViolation / QueryError / SQLGenNotReady).
    """
    if not question or not question.strip():
        raise HermesMCPError("question is empty")

    schema_map = get_schema_map(engine)  # caller passed engine; None handled below
    schema_prompt = build_schema_prompt(schema_map)

    if backend is None:
        backend = OpenAICompatBackend(
            settings.llm_base_url, settings.llm_api_key, resolve_model(settings)
        )

    attempts = 0
    sql = ""
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        raw = _clean_model_sql(backend.generate_sql(_SYSTEM, schema_prompt, question))
        if raw.startswith(NO_SQL_PREFIX):
            return {"ok": False, "reason": raw[len(NO_SQL_PREFIX):].strip(), "sql": "", "rows": [], "row_count": 0}
        try:
            sql = validate_readonly(raw)
            break
        except ReadOnlyViolation as e:
            if attempts >= MAX_ATTEMPTS:
                raise QueryError(f"generated SQL was not read-only: {e.message}") from e
            # ask the model to fix, enclosing previous attempt as a hint
            raw = raw  # will re-fetch in next loop; keep simple (no injected hint)

    cap = connection.get("max_rows") or 200
    result = run_readonly(engine, sql, max_rows=cap)

    data_summary = f"{result['row_count']} rows (truncated: {result['truncated']}): {json.dumps(result['rows'][:5], ensure_ascii=False)}"
    answer = backend.generate_answer(question, schema_prompt, sql, data_summary)

    result.update(ok=True, question=question, answer=answer)
    return result


def ask_from_connection(record: dict, question: str, settings, store=None) -> dict:
    """Convenience: resolve engine via connections before calling ask."""
    from hermes_mcp import connections
    engine = connections.get_engine(record)
    return ask(record, question, settings, engine=engine)