"""Referral system: /refer <code>, bonus for both sides."""
from .economy import grant, referral_bonus
from .logging_setup import log
from . import db


def get_or_create_code(user_id: int) -> str:
    from .users import ensure_user
    u = ensure_user(user_id)
    return u.get("referral_code", "")


def apply_referral(new_user_id: int, code: str) -> tuple:
    code = (code or "").strip().upper()
    if not code:
        return False, "referral_invalid"

    referrer_id = db.find_user_by_referral_code(code)
    if referrer_id is None:
        return False, "referral_invalid"
    if referrer_id == int(new_user_id):
        return False, "referral_invalid"

    from .users import ensure_user, get_user, add_referral
    new_u = ensure_user(new_user_id)
    if new_u.get("referred_by"):
        return False, "referral_invalid"

    db.upsert_user(new_user_id, referred_by=int(referrer_id))
    add_referral(referrer_id, new_user_id)

    bonus = referral_bonus()
    grant(referrer_id, bonus, "referral_bonus", meta={"referee": new_user_id})
    grant(int(new_user_id), bonus, "referral_welcome",
          meta={"referrer": referrer_id})

    log.info(f"referral applied referrer={referrer_id} referee={new_user_id}")
    return True, "referral_applied"


def stats(user_id: int) -> dict:
    from .users import get_user
    u = get_user(user_id) or {}
    refs = u.get("referrals") or []
    return {
        "code": u.get("referral_code", ""),
        "count": len(refs),
        "referrals": list(refs),
    }