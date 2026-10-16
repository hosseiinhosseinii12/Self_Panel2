"""One-time password (OTP) generator and verification for the user web panel."""
import secrets
import string
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Optional

from .logging_setup import log_bot
from . import db


OTP_TTL_SECONDS = 300  # 5 minutes
OTP_LENGTH = 6


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def _gen_code() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(OTP_LENGTH))


def generate_for_user(user_id: int) -> str:
    """Generate a new OTP for a user, replacing any previous one."""
    code = _gen_code()
    expires_at = (datetime.now(dt_timezone.utc)
                  + timedelta(seconds=OTP_TTL_SECONDS)).isoformat()
    db.set_user_otp(user_id, code, expires_at)
    log_bot.info(f"otp generated uid={user_id}")
    return code


def verify_code(code: str) -> Optional[int]:
    """Return user_id if the code is valid and not expired, else None."""
    code = (code or "").strip()
    if not code or len(code) != OTP_LENGTH or not code.isdigit():
        return None
    user_id = db.find_user_by_otp(code)
    if user_id is None:
        return None
    otp = db.get_user_otp(user_id)
    if not otp or otp.get("used"):
        return None
    try:
        expires_at = datetime.fromisoformat(otp["expires_at"])
        if expires_at <= datetime.now(dt_timezone.utc):
            return None
    except Exception:
        return None
    db.mark_otp_used(user_id)
    log_bot.info(f"otp verified uid={user_id}")
    return user_id


def invalidate(user_id: int) -> None:
    db.clear_user_otp(user_id)


def get_ttl() -> int:
    return OTP_TTL_SECONDS