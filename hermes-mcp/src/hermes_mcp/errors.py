"""Structured error types for hermes-mcp.

All exceptions here are safe to return to the caller: they carry a human
readable message and never embed passwords or raw query results.
"""


class HermesMCPError(Exception):
    """Base error. `code` maps to a stable machine label."""

    code = "error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def to_dict(self):
        return {"error": self.code, "message": self.message}


class ConfigError(HermesMCPError):
    """Bad or missing configuration (e.g. no encryption key)."""

    code = "config"


class StoreError(HermesMCPError):
    """Failure in the connection registry SQLite store."""

    code = "store"


class ConnectionResolveError(HermesMCPError):
    """Referenced connection name does not exist or cannot be built."""

    code = "connection"


class ReadOnlyViolation(HermesMCPError):
    """SQL did not pass the read-only whitelist / guard."""

    code = "readonly_violation"


class QueryError(HermesMCPError):
    """The (already validated) SELECT failed to execute."""

    code = "query"


class IntrospectionError(HermesMCPError):
    """Schema inspection failed against the target connection."""

    code = "introspection"