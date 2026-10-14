"""Environment / path configuration for hermes-mcp.

All tuning knobs come from the environment (never hard-coded), with safe
defaults. Loading is pure and side-effect free.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# project root = three levels up from this file (src/hermes_mcp/config.py)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    # --- paths ---
    # Where the shared SQLite `db_connections` registry lives. The web-console
    # and this MCP server must point at the SAME file.
    db_config_path: str = ""
    # Local SQLite used for offline dev / smoke tests (type="sqlite").
    sample_db_path: str = ""
    # Optional pointer to the Hermes config.yaml, so `ask` can follow the
    # model Hermes is configured with. Read-only; never written.
    hermes_config_path: str = ""
    # --- secrets / key ---
    enc_key: str = ""
    # --- generated-SQL (NL->SQL) endpoint (used by `ask`, M5) ---
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    # --- transport ---
    transport: str = "stdio"   # stdio | http
    # --- defaults ---
    default_max_rows: int = 200

    @property
    def resolved_db_config_path(self) -> Path:
        return Path(self.db_config_path) if self.db_config_path else PROJECT_ROOT / "db_connections.db"

    @property
    def resolved_sample_db_path(self) -> Path:
        return Path(self.sample_db_path) if self.sample_db_path else PROJECT_ROOT / "data" / "sample.db"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["llm_api_key"] = "<redacted>" if d.get("llm_api_key") else ""
        return d


def load_settings(env: dict | None = None) -> Settings:
    env = env if env is not None else os.environ

    def s(name: str, default: str = "") -> str:
        return env.get(name, default).strip() or default

    return Settings(
        db_config_path=s("HERMESMCP_DB_CONFIG"),
        sample_db_path=s("HERMESMCP_SAMPLE_DB"),
        hermes_config_path=s("HERMESMCP_CONFIG_PATH"),
        enc_key=s("HERMESMCP_DB_KEY"),
        llm_base_url=s("HERMESMCP_LLM_BASE_URL"),
        llm_api_key=s("HERMESMCP_LLM_API_KEY"),
        llm_model=s("HERMESMCP_LLM_MODEL"),
        transport=s("MCP_TRANSPORT", "stdio"),
        default_max_rows=int(s("HERMESMCP_MAX_ROWS", "200")),
    )