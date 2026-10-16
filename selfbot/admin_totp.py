"""Optional TOTP-based 2FA for the admin panel (SQLite-backed)."""
import io
from typing import Optional

from .logging_setup import log_flask
from . import db

try:
    import pyotp
    HAS_PYOTP = True
except ImportError:
    HAS_PYOTP = False

try:
    import qrcode  # type: ignore
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


def _ensure_row() -> None:
    conn = db.get_conn()
    row = conn.execute("SELECT id FROM admin_totp WHERE id = 1").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO admin_totp (id, enabled, secret, updated_at) "
            "VALUES (1, 0, '', '')"
        )


def is_enabled() -> bool:
    _ensure_row()
    conn = db.get_conn()
    row = conn.execute("SELECT enabled FROM admin_totp WHERE id = 1").fetchone()
    return bool(row and row["enabled"])


def get_secret() -> Optional[str]:
    _ensure_row()
    conn = db.get_conn()
    row = conn.execute("SELECT secret FROM admin_totp WHERE id = 1").fetchone()
    s = row["secret"] if row else ""
    return s or None


def _set(secret: str, enabled: bool) -> None:
    from datetime import datetime, timezone as dt_timezone
    _ensure_row()
    conn = db.get_conn()
    conn.execute(
        "UPDATE admin_totp SET secret = ?, enabled = ?, updated_at = ? WHERE id = 1",
        (secret, 1 if enabled else 0, datetime.now(dt_timezone.utc).isoformat()),
    )


def provision() -> tuple:
    """Generate (secret, uri, png_bytes)."""
    if not HAS_PYOTP:
        raise RuntimeError("pyotp not installed")
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(
        name="selfbot-admin", issuer_name="SELF BOT"
    )
    png = b""
    if HAS_QRCODE:
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()
    return secret, uri, png


def enable(secret: str) -> None:
    _set(secret, True)
    log_flask.info("admin TOTP enabled")


def disable() -> None:
    _set("", False)
    log_flask.info("admin TOTP disabled")


def verify(code: str) -> bool:
    if not is_enabled():
        return True
    if not HAS_PYOTP:
        return False
    secret = get_secret()
    if not secret:
        return False
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=1)
    except Exception:
        return False