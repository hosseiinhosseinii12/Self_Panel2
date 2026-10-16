"""Admin audit log: every admin action recorded with IP + timestamp."""
from datetime import datetime, timezone as dt_timezone
from typing import List, Optional

from . import db


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def record(
    admin_id: int,
    action: str,
    target: Optional[str] = None,
    ip: str = "",
    meta: Optional[dict] = None,
    admin_name: str = "",
) -> None:
    entry = {
        "id": f"aud_{int(datetime.now().timestamp() * 1000)}",
        "admin_id": int(admin_id),
        "admin_name": admin_name or "",
        "action": action,
        "target": target,
        "ip": ip,
        "created_at": _now_iso(),
        "meta": meta or {},
    }
    db.add_audit(entry)


def all_entries(limit: int = 500) -> List[dict]:
    return db.audit_recent(limit)


def for_admin(admin_id: int, limit: int = 200) -> List[dict]:
    out = [e for e in all_entries(10000)
           if int(e.get("admin_id", 0)) == int(admin_id)]
    return out[:limit]