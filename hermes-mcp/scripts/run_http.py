"""Serve hermes-mcp over HTTP (streamable-http) for remote debugging.

HTTP is the fallback MCP transport used when Hermes cannot spawn a co-located
server process (e.g. Hermes on a server, DBs on an internal host). For local
debugging: run this, then drive it with scripts/http_probe_client.py or any
streamable-HTTP MCP client.

    python scripts/run_http.py            # 127.0.0.1:9123  (stdio fallback also works)
    HERMESMCP_HOST=0.0.0.0 HERMESMCP_PORT=9123 python scripts/run_http.py
    HERMESMCP_ALLOWED_HOSTS=192.168.3.180:* python scripts/run_http.py  # allow LAN/remote
"""
from __future__ import annotations

import os
import socket

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

from hermes_mcp.server import app

# mcp SDK 2.x's DNS-rebinding protection (TransportSecurityMiddleware) rejects any
# Host header not in this allow-list with a 421 before the request reaches the app.
_LOCALHOST_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_LOCALHOST_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


def _local_ipv4s() -> list[str]:
    """This machine's own IPv4 addresses (so e.g. 192.168.3.180 is auto-allowed)."""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    if not ips:  # fallback: UDP connect trick finds the default-route IP, sends nothing
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                ips.add(s.getsockname()[0])
            finally:
                s.close()
        except Exception:
            pass
    return sorted(ips)


def _allowed_hosts() -> list[str]:
    """Allowed Host patterns: localhost + the machine's own IPs + HERMESMCP_ALLOWED_HOSTS."""
    allowed = list(_LOCALHOST_HOSTS)

    for ip in _local_ipv4s():  # auto-add own LAN IPs (e.g. 192.168.3.180)
        pat = f"{ip}:*"
        if pat not in allowed:
            allowed.append(pat)

    extra = os.environ.get("HERMESMCP_ALLOWED_HOSTS", "")
    for h in (x.strip() for x in extra.split(",") if x.strip()):
        if h not in allowed:
            allowed.append(h)

    host = os.environ.get("HERMESMCP_HOST", "0.0.0.0")
    # A concrete bind host (not 0.0.0.0/::) should always be allowed on itself.
    if host and host not in ("0.0.0.0", "::") and host not in allowed:
        allowed.append(host)
    return allowed


def _allowed_origins() -> list[str]:
    """Mirror http:// origins for each allowed Host (keeps browser CORS working)."""
    origins = list(_LOCALHOST_ORIGINS)
    for h in _allowed_hosts():
        if h in _LOCALHOST_HOSTS:
            continue
        base = h.split(":")[0]
        orig = f"http://{base}:*"
        if orig not in origins:
            origins.append(orig)
    return origins


def main():
    host = os.environ.get("HERMESMCP_HOST", "0.0.0.0")
    port = int(os.environ.get("HERMESMCP_PORT", "9123"))
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts(),
        allowed_origins=_allowed_origins(),
    )
    uvicorn.run(
        app.streamable_http_app(transport_security=security),
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()