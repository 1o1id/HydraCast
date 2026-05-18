"""hc/web_server.py  —  HTTP/HTTPS server classes for HydraCast Web UI.

SSL quick-start
───────────────
Drop your certificate files into the  ssl/  folder next to hydracast.py:

    ssl/cert.pem   ← certificate (chain)
    ssl/key.pem    ← private key

HydraCast will automatically detect them and start in HTTPS mode.
When port 443 is used and no cert files are found, a self-signed certificate
is generated automatically into ssl/ (valid 10 years, CN=localhost).

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
import subprocess
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
    def _generate_self_signed(cert: Path, key: Path) -> bool:
        """
        Generate a self-signed certificate using the ``cryptography`` package
        (preferred) or fall back to shelling out to ``openssl``.
        Returns True on success, False on failure.
        """
        # ── Try cryptography (pure-Python, no openssl binary needed) ──────────
        try:
            import datetime
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa

            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
            )
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, u"localhost"),
            ])
            now = datetime.datetime.utcnow()
            cert_obj = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(private_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now)
                .not_valid_after(now + datetime.timedelta(days=3650))
                .add_extension(
                    x509.SubjectAlternativeName([
                        x509.DNSName("localhost"),
                    ]),
                    critical=False,
                )
                .sign(private_key, hashes.SHA256())
            )

            key.write_bytes(
                private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption(),
                )
            )
            cert.write_bytes(cert_obj.public_bytes(serialization.Encoding.PEM))
            log.info("SSL: self-signed certificate generated via cryptography → %s", cert)
            return True

        except ImportError:
            pass  # cryptography not installed — try openssl binary
        except Exception as exc:
            log.warning("SSL: cryptography cert generation failed: %s", exc)

        # ── Fallback: shell out to openssl ────────────────────────────────────
        try:
            subprocess.run(
                [
                    "openssl", "req", "-x509",
                    "-newkey", "rsa:2048",
                    "-keyout", str(key),
                    "-out",    str(cert),
                    "-sha256", "-days", "3650",
                    "-nodes",
                    "-subj",   "/CN=localhost",
                ],
                check=True,
                capture_output=True,
            )
            log.info("SSL: self-signed certificate generated via openssl → %s", cert)
            return True
        except FileNotFoundError:
            log.warning(
                "SSL: openssl binary not found and cryptography package not installed. "
                "Install the cryptography package:  pip install cryptography"
            )
        except subprocess.CalledProcessError as exc:
            log.warning("SSL: openssl cert generation failed: %s", exc.stderr.decode())
        except Exception as exc:
            log.warning("SSL: openssl cert generation error: %s", exc)

        return False

    @staticmethod
    def _resolve_ssl(port: int) -> "tuple[Optional[Path], Optional[Path]]":
        """
        Return (cert_path, key_path) to use, or (None, None) if SSL is
        not configured.  Priority order:
          1. CLI flags  (--ssl-cert / --ssl-key)
          2. Default locations  ssl/cert.pem + ssl/key.pem
          3. Auto-generate a self-signed cert when port == 443
        Both files must exist (or be generated) for SSL to activate.
        """
        from hc.constants import SSL_CERT, SSL_KEY, SSL_DIR, get_ssl_cert, get_ssl_key

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
                "(%s, %s) — falling back to auto-detect.", cert, key,
            )

        # Auto-detect default locations
        cert = SSL_CERT()
        key  = SSL_KEY()
        if cert.is_file() and key.is_file():
            return cert, key

        # Auto-generate self-signed cert when running on port 443
        if port == _PORT_HTTPS:
            log.info(
                "SSL: no certificate found for port 443 — "
                "generating a self-signed certificate in %s/", SSL_DIR(),
            )
            if WebServer._generate_self_signed(cert, key):
                return cert, key
            log.warning(
                "SSL: could not generate a self-signed cert — "
                "falling back to plain HTTP on port 8080. "
                "Install the 'cryptography' package or drop ssl/cert.pem + ssl/key.pem "
                "to enable HTTPS."
            )

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
        from hc.constants import LISTEN_ADDR

        # Determine the candidate port first (needed by _resolve_ssl for
        # the auto-generate-on-443 logic).
        from hc.constants import WEB_PORT as _DEFAULT_PORT
        candidate_port = self._port if self._port is not None else _DEFAULT_PORT

        cert, key = self._resolve_ssl(candidate_port)
        use_ssl   = cert is not None

        # If SSL generation failed when port==443 was requested, fall back
        # to plain HTTP on 8080 so the UI is still reachable.
        if not use_ssl and candidate_port == _PORT_HTTPS:
            port = _PORT_HTTP
        else:
            port = candidate_port

        # Respect --listen / LISTEN_ADDR (defaults to "0.0.0.0")
        bind_addr = LISTEN_ADDR()

        try:
            self._server = _HydraCastHTTPServer((bind_addr, port), WebHandler)

            if use_ssl:
                ctx = self._make_ssl_context(cert, key)
                self._server.socket = ctx.wrap_socket(
                    self._server.socket,
                    server_side=True,
                )
                log.info(
                    "Web UI (HTTPS) → https://%s:%d  [cert: %s]",
                    bind_addr, port, cert,
                )
            else:
                log.info("Web UI (HTTP) → http://%s:%d", bind_addr, port)

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
                "Web UI failed to bind %s:%d — %s. "
                "Try --web-port to use a different port, or run with elevated "
                "privileges for ports below 1024.",
                bind_addr, port, exc,
            )
        except Exception as exc:
            log.error("Web UI failed to start: %s", exc)

    def stop(self) -> None:
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
