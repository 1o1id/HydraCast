"""
hc/ssl_bootstrap.py  —  SSL certificate bootstrap for HydraCast.

Called once at application startup (from hydracast.py) via::

    from hc.ssl_bootstrap import ensure_ssl
    cert_path, key_path = ensure_ssl(console)

Priority order
──────────────
  1. Existing valid cert in the legacy install-dir locations
       (<install_dir>/ssl/  or  <install_dir>/_internal/certifi/)
       • Checked first so upgrades don't abandon already-generated certs.
       • Both cert.pem and key.pem must exist.
       • Cert must not expire within the next 30 days.
       → Returned as-is; no generation occurs.

  2. Existing valid cert in the user-writable CONFIG_DIR locations
       (CONFIG_DIR()/ssl/  or  CONFIG_DIR()/certifi/)
       • Same validity checks as above.
       → Returned as-is; no generation occurs.

  3. Fresh self-signed cert via the ``cryptography`` library
       • 2048-bit RSA / SHA-256 / 3650-day validity.
       • SAN: localhost, hydracast.local, 127.0.0.1.
       → Written to CONFIG_DIR()/ssl/.

  4. Bundled fallback cert (pre-generated, embedded in this module)
       → Written to CONFIG_DIR()/ssl/ if possible,
         otherwise to  CONFIG_DIR()/certifi/.

Location strategy (v6.2+)
──────────────────────────
  All cert *writing* now targets CONFIG_DIR() (e.g. %APPDATA%\HydraCast)
  rather than the install directory.  This fixes [WinError 5] Access is
  denied when HydraCast is installed under C:\Program Files\ and launched
  without administrator privileges — the same approach already used by
  every other writable path in constants.py.

  Legacy install-dir paths are still *read* (steps 1) so that users who
  already have a valid cert there don't lose it on upgrade.

  Primary write target  : CONFIG_DIR()/ssl/cert.pem  +  key.pem
  Fallback write target : CONFIG_DIR()/certifi/cert.pem  +  key.pem

Install-dir resolution
──────────────────────
  Frozen (.exe) : Path(sys.executable).parent  — folder containing hydracast.exe
  Source (.py)  : two levels up from this file  — repo root

Bundled cert metadata
─────────────────────
  Generated : 2026-05-23  |  Expires : 2036-05-20
  Key : RSA-2048  |  Hash : SHA-256  |  Validity : 3650 days
  CN  : hydracast.local
  SAN : DNS:localhost, DNS:hydracast.local, IP:127.0.0.1

  This cert is a last-resort fallback.  It is identical for every
  HydraCast installation (browsers will warn).  HydraCast always
  regenerates a unique cert on first run when the cryptography library
  is available — which it always is in the PyInstaller bundle.
"""
from __future__ import annotations

import base64
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bundled fallback certificate  (base-64 encoded PEM, NO embedded newlines)
# Generated 2026-05-23 · RSA-2048 · SHA-256 · valid until 2036-05-20
# CN=hydracast.local  SAN: localhost / hydracast.local / 127.0.0.1
# ---------------------------------------------------------------------------
_FALLBACK_CERT_B64 = (
    "LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSURSVENDQWkyZ0F3SUJBZ0lVTUttcWIr"
    "OXViOGxNR25FemZUM25ZazdPR3hzd0RRWUpLb1pJaHZjTkFRRUwKQlFBd1JERVlNQllHQTFV"
    "RUF3d1BhSGxrY21GallYTjBMbXh2WTJGc01SSXdFQVlEVlFRS0RBbEllV1J5WVVOaApjM1F4"
    "RkRBU0JnTlZCQXNNQzFObGJHWXRVMmxuYm1Wa01CNFhEVEkyTURVeU16QTJNVEV3TmxvWERU"
    "TTJNRFV5Ck1EQTJNVEV3Tmxvd1JERVlNQllHQTFVRUF3d1BhSGxrY21GallYTjBMbXh2WTJG"
    "c01SSXdFQVlEVlFRS0RBbEkKZVdSeVlVTmhjM1F4RkRBU0JnTlZCQXNNQzFObGJHWXRVMmxu"
    "Ym1Wa01JSUJJakFOQmdrcWhraUc5dzBCQVFFRgpBQU9DQVE4QU1JSUJDZ0tDQVFFQXhnUVZU"
    "RnVCek0xYTUzSHR3b1pxQ0w3eGVDTytUaUZMbEZXSG0xSXZONGFsCjJ4MXBKTGROZTZPdXRU"
    "bmtnMUlZYVl1MGk0cGZmb1RYZlFHQlp0VEVaVUNUVFVFMjdEREU4eThWREZIN0tPNHUKSVVw"
    "aTdKYmo4WE80WVFlRVBtbm5WVmd3UEV5Rnk1L3BUVkVOMUhvSy84Wlgrc1ZtcThpb3JVY3d5"
    "RTlmdXNybgpKR3MzaTVJSVdvYXdjVmJvVzBNby9laUNGazBURGxXdEFSSXIxNW1qUUZBcFBt"
    "MEVyQk9qd1hwRi84c1dVc3dpCk1oTkdLMUJNQVNqczVqVkxQN1pKeEkvTWg3TDRJRHRodVdV"
    "eVN3N3pwaUh0cmlCV1FNR21kWFpPSU1DaXBiUzMKa3Q0VmJhRmpNczF1aDFjTVJURnR3bEl3"
    "Y1krSUJmemd5THQrWjFKZVpRSURBUUFCb3k4d0xUQXJCZ05WSFJFRQpKREFpZ2dsc2IyTmhi"
    "R2h2YzNTQ0QyaDVaSEpoWTJGemRDNXNiMk5oYkljRWZ3QUFBVEFOQmdrcWhraUc5dzBCCkFR"
    "c0ZBQU9DQVFFQUVxendUTlVFeTNSd2V5MklOdWRVd1NDUXpKcm9ZLzdRSGtZNk5qcG10WXVS"
    "SlZlMVdZMk8KbWhxRXhhN28vQ1FQNGROYWVtS2Q5WXNyNUp6OGR1V1Y2LzdQWFh0NjRjME1K"
    "MnlDL3pRQ2VZZDdGWWpaK1NLRApXSThJQTdTZUoxNnRTaVNtVDk5L0pEVXYyYkltT2xYZjRZ"
    "MnVyNzBiQkhlSUViZ3d3L2dnWU9xcnB1Y3krekpnCkd6TGlZbUtUUFVxRFZQbEIvM2tLcEhG"
    "d3hnaHpSS2FPL2dsZVBzeXhpNXlyODhtQnZHKzM1MTBmNU14WW4yMmkKTUpCWGJzMS8xNlpE"
    "RkwwTGJXZWJsNk5BaWZQWmdMaWFuWXhyempsYVVWN0N6NW83RG5mdVpVZTZSOEN6VlhiZApH"
    "UVRGaWc4TTl3ZW1mdEEyNlNWQXZyTjVLYkN3ejlRUEdBPT0KLS0tLS1FTkQgQ0VSVElGSUNB"
    "VEUtLS0tLQo="
)

_FALLBACK_KEY_B64 = (
    "LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0tLQpNSUlFcFFJQkFBS0NBUUVBeGdRVlRG"
    "dUJ6TTFhNTNIdHdvWnFDTDd4ZUNPK1RpRkxsRldIbTFJdk40YWwyeDFwCkpMZE5lNk91dFRu"
    "a2cxSVlhWXUwaTRwZmZvVFhmUUdCWnRURVpVQ1RUVUUyN0RERTh5OFZERkg3S080dUlVcGkK"
    "N0piajhYTzRZUWVFUG1ublZWZ3dQRXlGeTUvcFRWRU4xSG9LLzhaWCtzVm1xOGlvclVjd3lF"
    "OWZ1c3JuSkdzMwppNUlJV29hd2NWYm9XME1vL2VpQ0ZrMFREbFd0QVJJcjE1bWpRRkFwUG0w"
    "RXJCT2p3WHBGLzhzV1Vzd2lNaE5HCksxQk1BU2pzNWpWTFA3Wkp4SS9NaDdMNElEdGh1V1V5"
    "U3c3enBpSHRyaUJXUU1HbWRYWk9JTUNpcGJTM2t0NFYKYmFGak1zMXVoMWNNUlRGdHdsSXdj"
    "WStJQmZ6Z3lMdCtaMUplWlFJREFRQUJBb0lCQUNsenJONjNjRGhtaTc5VQpGUFRxRFBQb1B1"
    "WEt1N21nMURESTU5S2V0WFl1K0hUaVZ3S2g0YlUrZW1JRExNQkYxUWp4UHpvUDNUWS95bGxu"
    "CmtZWnNoM0YzdjI1R2R1QWlSSFJ3K0h2RUJLc1lreTBTWkp1STZjNC9qb3MzVnRwMjhuK2w3"
    "ckVNeHR4dHpSbkwKbkRUTTJKVWJHUXRNdkJXOWMzd1VvVlJwYzAySGNtWGxaL1BHUXpUdHdy"
    "cGxETEQ5WkpPdjVhQ0dFbVl4N1N4RApOVFYxdzYrY0hBdUxCMDREVFhCN3ZOZzlGeENDYWtJ"
    "VWRMczFGRTd0Rm9BUFpWUTFtRVl4Z2hLNVJWMHdoOUZ6CjFSc1pQVGNhU2JFalR5MXBzdkZ5"
    "Q2tvbVNDT0c3dkFRN3dULzlEeW9HTXduTmRQdm5mWU9YVzZqdGlOTi9FenIKMU9uMHEzRUNn"
    "WUVBNTl6b09SWkVVdzE5RVkwaTdSNVlYVmovejY2MGJUc2hpNXlpMnJiMjcrZ2Y2bDJnWkQ0"
    "cQovc0RJZXc2N2JNY3hlSjRQaVlWU2hNQnMxTU40bURMNjkwU0xhV0xhSGgwbWFLUFJwM3Bj"
    "NERhQ09WcUlqWWhkCkdKWkRXeWwwWDRadGxBZitpeTdScXBSTWk1ZFRtai8zVGl0WkVWV3Mr"
    "WDRwZ244S0tHb0U3TFVDZ1lFQTJxRXAKaUNmYUFNdWM4QkJWVG0vbUxqcElOTnBaM1A0amdK"
    "UGljY1pIcytsS3NFQytGK3dHUWpyL3JyTkhpS0wvMkhBZgplQlJhcjR6TnhxTEJZRFBTTFNJ"
    "RWFRbEFnWHNYSi9Mdm9lcmZxbUk0cDNuVTZ0bUxYOGlxNEdvZG5KaHBlbmc5Ck1CZGJBMU9q"
    "YWE5NUxRRUdZYUlkeUtjZUx4cDlnZWJzWDlBdWFQRUNnWUVBNG1FU0x1S3B0UGsvZW9wMVps"
    "UXgKYkhxLzBSTS9RRUx5ZnJCaFpQQXM2NUdVejZ1NE5RZHB6UytHem5kVTBXRXUwUmhxRFJn"
    "cHVFbDBPTXZkQzZVQwoyYmVIOGs0OHJoaEI3dnE3Y1N5TVQ3R0l0ZHpKNUg0V2Z6SCs4NXZt"
    "N25sK3RZQ1VxMm43OWZNelJUdHJ1ZmZvClN0OFI2RlhoTy90TkpnZEpjS29Ld3QwQ2dZRUFt"
    "bklKWGVjUktVaXRxQ1ZScmlSOGppR2NDc0pKZzBXQkhQN2IKbEJaSFp3QWlSQnFvYjB2TUxC"
    "Tnp0aDF1SmtkSHg4V0ZaWld6YnBwZ2I3ZGdOaTdGaGg2bTBQQzVRZjhMbjZ3Tgp2dXVtYjc0"
    "TldicEdRRlhJRUxVNGlXcE5XdWVNYy9qbStNYzNBMFdkaGpad3V1c2piK3RQY1FVbDNJNnhK"
    "UWhWCjZXV3VzM0VDZ1lFQWxZMElkVU9wMDRKNnNQcEV1VjNmcWVhT2FCRlAvUFpiWkhtNEVt"
    "VGVKMjhWYmUxemNMbjkKYnZkZVQ2N0FkS1c5cUJocHNQTDFsR0k1QkNFUFlNSWpvbGx0OG92"
    "ODVqck9GelY3OTJBU3hJektMTytPNkpRbgp5TEVPNlplbWlMc01xS21zTWw3aW96QmdZNWNn"
    "a2xOdGU2Ti9ZbGt5eVp2c0ZiVnpXMmp3YzgwPQotLS0tLUVORCBSU0EgUFJJVkFURSBLRVkt"
    "LS0tLQo="
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _install_dir() -> Path:
    """
    Return the directory that contains hydracast.exe (frozen) or the
    repo root when running from source.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # hc/ssl_bootstrap.py  ->  ../  ->  repo root
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Writable cert directories — rooted under CONFIG_DIR(), not install_dir().
#
# The install directory (e.g. C:\Program Files\HydraCast) is UAC-protected
# on Windows.  Writing there without elevation raises [WinError 5] Access
# is denied.  CONFIG_DIR() resolves to a per-user writable location
# (%APPDATA%\HydraCast on Windows, ~/.config/hydracast on Linux/macOS)
# and is the same strategy already used for every other writable path in
# constants.py.
# ---------------------------------------------------------------------------

def _ssl_dir() -> Path:
    """
    Primary cert directory for *writing*: CONFIG_DIR()/ssl/

    Always user-writable; avoids [WinError 5] under Program Files.
    """
    from hc.constants import CONFIG_DIR
    return CONFIG_DIR() / "ssl"


def _certifi_dir() -> Path:
    """
    Fallback cert directory for *writing*: CONFIG_DIR()/certifi/

    Used only when _ssl_dir() itself cannot be written (should be rare
    since both share the same CONFIG_DIR parent).
    """
    from hc.constants import CONFIG_DIR
    return CONFIG_DIR() / "certifi"


# ---------------------------------------------------------------------------
# Legacy read-only locations — the old install-dir paths from v6.1 and
# earlier.  We never write here (they're protected under Program Files),
# but we still *read* them so existing certs survive an upgrade.
# ---------------------------------------------------------------------------

def _legacy_ssl_dir() -> Path:
    """Legacy primary: <install_dir>/ssl/  (read-only check only)."""
    return _install_dir() / "ssl"


def _legacy_certifi_dir() -> Path:
    """Legacy fallback: <install_dir>/_internal/certifi/  (read-only check only)."""
    return _install_dir() / "_internal" / "certifi"


def _cert_is_valid(cert_path: Path, key_path: Path, min_days: int = 30) -> bool:
    """
    Return True when both PEM files exist and the certificate has at least
    ``min_days`` of validity left.  Never raises.
    """
    if not cert_path.exists() or not key_path.exists():
        return False
    try:
        from cryptography.x509 import load_pem_x509_certificate
        cert    = load_pem_x509_certificate(cert_path.read_bytes())
        remaining = cert.not_valid_after_utc - datetime.now(timezone.utc)
        if remaining < timedelta(days=min_days):
            log.warning(
                "[SSL] Existing cert expires in %d day(s) — will regenerate.",
                remaining.days,
            )
            return False
        log.debug("[SSL] Existing cert valid for %d more day(s). Reusing.", remaining.days)
        return True
    except Exception as exc:
        log.warning("[SSL] Could not validate existing cert (%s) — will regenerate.", exc)
        return False


def _generate_cert(cert_path: Path, key_path: Path) -> None:
    """
    Generate a fresh self-signed certificate with the ``cryptography`` library.
    Raises ImportError if the library is absent.
    """
    from ipaddress import IPv4Address
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    log.info("[SSL] Generating new self-signed certificate …")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME,               "hydracast.local"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME,         "HydraCast"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME,  "Self-Signed"),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName("hydracast.local"),
                x509.IPAddress(IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    log.info(
        "[SSL] Certificate generated → %s  (expires %s)",
        cert_path, (now + timedelta(days=3650)).date(),
    )


def _write_bundled(cert_path: Path, key_path: Path) -> None:
    """
    Decode the embedded base-64 fallback cert/key and write them to disk.
    Does NOT require the ``cryptography`` library.
    """
    log.warning(
        "[SSL] Using bundled fallback certificate → %s  "
        "(expires 2036-05-20 — unique generation was unavailable)",
        cert_path.parent,
    )
    cert_path.write_bytes(base64.b64decode(_FALLBACK_CERT_B64))
    key_path.write_bytes(base64.b64decode(_FALLBACK_KEY_B64))


def _try_write_to(directory: Path, *, generate: bool) -> Tuple[Path, Path]:
    """
    Create ``directory`` and write certs there.

    If ``generate`` is True  : tries cryptography library first, then bundled.
    If ``generate`` is False : writes bundled certs directly (certifi fallback).

    Returns (cert_path, key_path).  Raises on I/O failure.
    """
    directory.mkdir(parents=True, exist_ok=True)
    cert_path = directory / "cert.pem"
    key_path  = directory / "key.pem"

    if generate:
        try:
            _generate_cert(cert_path, key_path)
            return cert_path, key_path
        except ImportError:
            log.warning("[SSL] 'cryptography' not available — using bundled cert.")
        except Exception as exc:
            log.error("[SSL] Certificate generation failed: %s", exc)

    _write_bundled(cert_path, key_path)
    return cert_path, key_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

#: Populated by ensure_ssl(); importable by web.py / WebServer.
CERT_PATH: Path | None = None
KEY_PATH:  Path | None = None


def ensure_ssl(console=None) -> Tuple[Path, Path]:
    """
    Guarantee a valid TLS certificate pair exists and return their paths.

    Search order
    ────────────
    1. Legacy install-dir locations (read-only; checked so upgrades keep
       existing certs without regenerating):
         <install_dir>/ssl/               ← old primary
         <install_dir>/_internal/certifi/ ← old fallback
    2. New CONFIG_DIR locations (user-writable; used on first run or after
       the legacy certs expire):
         CONFIG_DIR()/ssl/
         CONFIG_DIR()/certifi/
    3. Generate a fresh self-signed cert into CONFIG_DIR()/ssl/.
    4. Last resort: write the bundled fallback cert to CONFIG_DIR()/certifi/.

    Parameters
    ----------
    console : rich.console.Console, optional
        Startup messages are printed here; otherwise only logged.

    Returns
    -------
    (cert_path, key_path) : Tuple[Path, Path]

    Raises
    ------
    RuntimeError
        When no location can be written to.
    """
    global CERT_PATH, KEY_PATH

    def _msg(text: str, style: str = "dim") -> None:
        log.info("[SSL] %s", text.strip())
        if console is not None:
            try:
                console.print(f"[{style}]  {text}[/]")
            except Exception:
                pass

    # ── 1. Legacy install-dir certs (read-only upgrade path) ──────────────────
    # These paths may sit inside C:\Program Files\ — never write to them,
    # but honour any valid cert already there so users don't lose a working
    # cert after an upgrade.
    for legacy_dir, label in (
        (_legacy_ssl_dir(),     "ssl/"),
        (_legacy_certifi_dir(), "_internal/certifi/"),
    ):
        lc = legacy_dir / "cert.pem"
        lk = legacy_dir / "key.pem"
        if _cert_is_valid(lc, lk):
            _msg(
                f"SSL  : Existing certificate reused from legacy location "
                f"({label}) → {lc.parent}",
                "dim yellow",
            )
            CERT_PATH, KEY_PATH = lc, lk
            return lc, lk

    # ── 2. New CONFIG_DIR certs (user-writable; normal steady-state) ──────────
    ssl_cert = _ssl_dir()     / "cert.pem"
    ssl_key  = _ssl_dir()     / "key.pem"
    fb_cert  = _certifi_dir() / "cert.pem"
    fb_key   = _certifi_dir() / "key.pem"

    if _cert_is_valid(ssl_cert, ssl_key):
        _msg(f"SSL  : Existing certificate reused  (→ {ssl_cert.parent})", "dim")
        CERT_PATH, KEY_PATH = ssl_cert, ssl_key
        return ssl_cert, ssl_key

    if _cert_is_valid(fb_cert, fb_key):
        _msg("SSL  : Existing certificate reused from certifi/", "dim yellow")
        CERT_PATH, KEY_PATH = fb_cert, fb_key
        return fb_cert, fb_key

    # ── 3. Generate a fresh cert into CONFIG_DIR()/ssl/ ───────────────────────
    _msg("SSL  : Generating self-signed certificate …", "dim cyan")
    try:
        cert_path, key_path = _try_write_to(_ssl_dir(), generate=True)
        _msg(f"SSL  : Certificate ready  →  {cert_path.parent}", "green")
        CERT_PATH, KEY_PATH = cert_path, key_path
        return cert_path, key_path
    except Exception as exc:
        log.error("[SSL] CONFIG_DIR/ssl/ not writable (%s) — trying certifi/ …", exc)
        _msg(
            f"SSL  : ssl/ not writable ({exc}) — falling back to certifi/",
            "yellow",
        )

    # ── 4. Last resort: bundled cert into CONFIG_DIR()/certifi/ ───────────────
    try:
        cert_path, key_path = _try_write_to(_certifi_dir(), generate=False)
        _msg("SSL  : Fallback certificate written to certifi/", "yellow")
        CERT_PATH, KEY_PATH = cert_path, key_path
        return cert_path, key_path
    except Exception as exc:
        log.critical("[SSL] FATAL — could not write certs to any location: %s", exc)
        raise RuntimeError(
            f"HydraCast could not create SSL certificates.\n"
            f"  Primary  : {_ssl_dir()}\n"
            f"  Fallback : {_certifi_dir()}\n"
            f"  Error    : {exc}\n\n"
            f"  Fix: ensure the HydraCast data folder is writable, then restart.\n"
            f"  Data folder is typically: {_ssl_dir().parent}"
        ) from exc
