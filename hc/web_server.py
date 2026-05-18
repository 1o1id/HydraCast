"""hc/web_server.py  —  HTTP/HTTPS server classes for HydraCast Web UI.

SSL quick-start
───────────────
Drop your certificate files into the  ssl/  folder next to hydracast.py:

    ssl/cert.pem   ← certificate (chain)
    ssl/key.pem    ← private key

HydraCast will automatically detect them and start in HTTPS mode.

Alternatively pass explicit paths via CLI:

    python hydracast.py --ssl-cert /path/to/cert.pem --ssl-key /path/to/key.pem

Self-signed certificate (for local testing only):

    openssl req -x509 -newkey rsa:4096 -keyout ssl/key.pem \
        -out ssl/cert.pem -sha256 -days 365 --nodes \
        -subj "/CN=localhost"
"""
from __future__ import annotations

import logging
import ssl
import threading
from http.server import HTTPServer
from pathlib import Path
from typing import Optional

from hc.web_handler import WebHandler

log = logging.getLogger(__name__)


# =============================================================================
# SERVER
# =============================================================================
class _HydraCastHTTPServer(HTTPServer):
    allow_reuse_address = True


_PORT_HTTPS = 443
_PORT_HTTP  = 8080

class WebServer:
    def __init__(self, port: Optional[int] = None) -> None:
        # None = auto-select: 443 when SSL is active, 8080 otherwise.
        # Pass an explicit int to override (e.g. via --web-port).
        self._port   = port
        self._server: Optional[_HydraCastHTTPServer] = None
        self._thread: Optional[threading.Thread]     = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_ssl() -> "tuple[Optional[Path], Optional[Path]]":
        """
        Return (cert_path, key_path) to use, or (None, None) if SSL is
        not configured.  Priority order:
          1. CLI flags  (--ssl-cert / --ssl-key)
          2. Default locations  ssl/cert.pem + ssl/key.pem
        Both files must exist for SSL to activate.
        """
        from hc.constants import SSL_CERT, SSL_KEY, get_ssl_cert, get_ssl_key

        # CLI override
        cli_cert = get_ssl_cert()
        cli_key  = get_ssl_key()
        if cli_cert and cli_key:
            cert = Path(cli_cert)
            key  = Path(cli_key)
            if cert.is_file() and key.is_file():
                return cert, key
            log.warning(
                "SSL: --ssl-cert/--ssl-key provided but file(s) not found "
                "(%s, %s) — falling back to HTTP.", cert, key,
            )
            return None, None

        # Auto-detect default locations
        cert = SSL_CERT()
        key  = SSL_KEY()
        if cert.is_file() and key.is_file():
            return cert, key

        return None, None

    @staticmethod
    def _make_ssl_context(cert: Path, key: Path) -> ssl.SSLContext:
        """Build a modern TLS server context from cert + key files."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # Disable old, insecure protocol versions
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
        return ctx

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self) -> None:
        cert, key = self._resolve_ssl()
        use_ssl   = cert is not None

        # Auto-select port: 443 for HTTPS, 8080 for HTTP (unless explicitly overridden)
        port = self._port if self._port is not None else (
            _PORT_HTTPS if use_ssl else _PORT_HTTP
        )

        try:
            self._server = _HydraCastHTTPServer(("0.0.0.0", port), WebHandler)

            if use_ssl:
                ctx = self._make_ssl_context(cert, key)
                self._server.socket = ctx.wrap_socket(
                    self._server.socket,
                    server_side=True,
                )
                log.info(
                    "Web UI (HTTPS) → https://0.0.0.0:%d  [cert: %s]",
                    port, cert,
                )
            else:
                log.info("Web UI (HTTP) → http://0.0.0.0:%d", port)

            self._thread = threading.Thread(
                target=self._server.serve_forever,
                daemon=True,
                name="webui",
            )
            self._thread.start()

        except ssl.SSLError as exc:
            log.error(
                "SSL error — could not wrap socket with cert=%s key=%s: %s. "
                "Check that the certificate and key file are valid and match.",
                cert, key, exc,
            )
        except OSError as exc:
            log.error(
                "Web UI failed to bind :%d — %s. "
                "Try --web-port to use a different port.",
                port, exc,
            )
        except Exception as exc:
            log.error("Web UI failed to start: %s", exc)

    def stop(self) -> None:
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
