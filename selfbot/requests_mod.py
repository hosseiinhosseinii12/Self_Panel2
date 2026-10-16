"""User requests: diamonds or subscription. Admin approves/rejects."""
from datetime import datetime, timezone as dt_timezone
from typing import List, Optional

from .economy import grant
from .logging_setup import log
from . import db


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def all_requests() -> List[dict]:
    return db.list_requests(limit=5000)


def create_diamond_request(user_id: int, amount: int, note: str = "") -> dict:
    amount = max(1, min(10000, int(amount)))
    req = {
        "id": f"req_{int(datetime.now().timestamp() * 1000)}_{user_id}",
        "user_id": int(user_id),
        "type": "diamonds",
        "amount": amount,
        "plan": None,
        "note": note or "",
        "status": "pending",
        "created_at": _now_iso(),
        "decided_at": None,
        "decided_by": None,
    }
    db.add_request(req)
    log.info(f"request created user={user_id} type=diamonds amount={amount}")
    return req


def create_subscription_request(user_id: int, plan: str, note: str = "") -> dict:
    if plan not in ("basic", "pro", "vip"):
        plan = "basic"
    req = {
        "id": f"req_{int(datetime.now().timestamp() * 1000)}_{user_id}",
        "user_id": int(user_id),
        "type": "subscription",
        "amount": None,
        "plan": plan,
        "note": note or "",
        "status": "pending",
        "created_at": _now_iso(),
        "decided_at": None,
        "decided_by": None,
    }
    db.add_request(req)
    log.info(f"request created user={user_id} type=subscription plan={plan}")
    return req


def pending() -> List[dict]:
    return db.list_requests(status="pending", limit=500)


def for_user(user_id: int) -> List[dict]:
    return db.list_requests(user_id=user_id, limit=200)


def get(req_id: str) -> Optional[dict]:
    for r in all_requests():
        if r.get("id") == req_id:
            return r
    return None


def approve(req_id: str, admin_id: int = 0) -> Optional[dict]:
    for r in all_requests():
        if r.get("id") != req_id or r.get("status") != "pending":
            continue
        db.update_request(
            req_id, status="approved", decided_at=_now_iso(),
            decided_by=int(admin_id),
        )
        if r["type"] == "diamonds":
            grant(int(r["user_id"]), int(r["amount"]), "request_approved",
                  meta={"req": req_id})
        log.info(f"request approved id={req_id} by={admin_id}")
        return r
    return None


def reject(req_id: str, admin_id: int = 0) -> Optional[dict]:
    for r in all_requests():
        if r.get("id") != req_id or r.get("status") != "pending":
            continue
        db.update_request(
            req_id, status="rejected", decided_at=_now_iso(),
            decided_by=int(admin_id),
        )
        log.info(f"request rejected id={req_id} by={admin_id}")
        return r
    return None


def approval_rate_series(days: int = 14) -> List[dict]:
    from datetime import timedelta
    today = datetime.now(dt_timezone.utc).date()
    buckets = {}
    for i in range(days, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        buckets[d] = {"date": d, "approved": 0, "rejected": 0}
    for r in all_requests():
        if r.get("status") not in ("approved", "rejected"):
            continue
        try:
            d = datetime.fromisoformat(r["decided_at"]).date().isoformat()
        except Exception:
            continue
        if d in buckets:
            buckets[d][r["status"]] += 1
    return list(buckets.values())