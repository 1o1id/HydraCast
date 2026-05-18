"""
hc/web_access_log.py  —  Structured HTTP access logger for HydraCast Web UI.

Writes one line per request to the named logger  ``hc.access``  in Combined
Log Format so it drops straight into any log-analysis tool (GoAccess, AWStats,
Graylog, etc.).

Format (CLF + extras):
    <ip> - <username> [<timestamp>] "<method> <path> <protocol>" <status>
    <bytes> "<referer>" "<user-agent>"

Usage — call  log_access(handler, status, response_bytes)  from WebHandler
after every response is sent:

    from hc.web_access_log import log_access
    ...
    self._send(200, body)
    log_access(self, 200, len(body))

The IP field honours X-Forwarded-For so reverse-proxy deployments show the
real client address.  Username is extracted from a ``X-HC-User`` header if
present (set by a future auth layer); otherwise it is ``-``.

All access events are also forwarded to the existing  ``hc.upload_audit``
logger for upload endpoints so operators get full traceability in one place.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

access_log  = logging.getLogger("hc.access")
upload_audit = logging.getLogger("hc.upload_audit")

_UPLOAD_PATHS = {
    "/api/upload/init",
    "/api/upload/chunk",
    "/api/upload/finalize",
    "/api/upload/abort",
    "/api/upload/status",
}


def _real_ip(handler) -> str:
    """Return the real client IP, honouring X-Forwarded-For."""
    xff = handler.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    addr = getattr(handler, "client_address", None)
    if addr:
        return addr[0]
    return "-"


def _username(handler) -> str:
    """Return a username if the request carries one, else '-'."""
    user = handler.headers.get("X-HC-User", "").strip()
    return user if user else "-"


def log_access(
    handler,
    status:         int,
    response_bytes: int = 0,
    *,
    extra: Optional[str] = None,
) -> None:
    """
    Emit one Combined-Log-Format line to ``hc.access``.

    Parameters
    ----------
    handler        : BaseHTTPRequestHandler subclass instance
    status         : HTTP status code sent
    response_bytes : number of body bytes sent (0 if unknown)
    extra          : optional free-form annotation appended in brackets
    """
    ip        = _real_ip(handler)
    username  = _username(handler)
    method    = getattr(handler, "command", "-") or "-"
    path      = getattr(handler, "path",    "-") or "-"
    proto     = getattr(handler, "request_version", "HTTP/1.1") or "HTTP/1.1"
    referer   = handler.headers.get("Referer",    "-")
    ua        = handler.headers.get("User-Agent", "-")
    ts        = datetime.now(tz=timezone.utc).strftime("%d/%b/%Y:%H:%M:%S %z")
    size_str  = str(response_bytes) if response_bytes else "-"
    extra_str = f' [{extra}]' if extra else ""

    line = (
        f'{ip} - {username} [{ts}] "{method} {path} {proto}" '
        f'{status} {size_str} "{referer}" "{ua}"{extra_str}'
    )
    access_log.info(line)

    # Mirror upload-related requests to the upload audit log as well.
    base_path = path.split("?")[0]
    if base_path in _UPLOAD_PATHS:
        upload_audit.info("ACCESS  %s", line)
