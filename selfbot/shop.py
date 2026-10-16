"""Diamond shop — DB-backed items."""
from datetime import datetime, timezone as dt_timezone
from typing import Optional
import json

from .economy import spend
from .logging_setup import log
from . import db

DEFAULT_ITEMS = {
    "extra_job_slot": {
        "name": "Extra job slot",
        "description": "+1 concurrent repeat job",
        "price": 20,
        "max_per_user": 10,
        "effect": {"extra_job_slots": 1},
    },
    "interval_reduction": {
        "name": "Interval reduction",
        "description": "Reduce minimum interval by 60 seconds",
        "price": 30,
        "max_per_user": 5,
        "effect": {"interval_discount": 60},
    },
    "duration_extension": {
        "name": "Duration extension",
        "description": "Extend max duration by 120 minutes",
        "price": 25,
        "max_per_user": 5,
        "effect": {"duration_extension": 120},
    },
    "custom_font_unlock": {
        "name": "Custom font unlock",
        "description": "Unlock custom fonts for name and clock",
        "price": 40,
        "max_per_user": 1,
        "effect": {"unlocked_fonts": ["custom"]},
    },
    "priority_support": {
        "name": "Priority support",
        "description": "Your requests are handled first",
        "price": 100,
        "max_per_user": 1,
        "effect": {"priority_support": True},
    },
}


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def _ensure_seeded() -> None:
    conn = db.get_conn()
    row = conn.execute("SELECT COUNT(*) AS c FROM shop_items").fetchone()
    if row and int(row["c"]) == 0:
        for item_id, item in DEFAULT_ITEMS.items():
            _write_item(item_id, item)


def _write_item(item_id: str, item: dict) -> None:
    conn = db.get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO shop_items
           (id, name, description, price, max_per_user, effect,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            item_id,
            item.get("name", item_id),
            item.get("description", ""),
            int(item.get("price", 0)),
            int(item.get("max_per_user", 1)),
            json.dumps(item.get("effect", {})),
            item.get("created_at", _now_iso()),
            _now_iso(),
        ),
    )


def all_items() -> dict:
    _ensure_seeded()
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM shop_items").fetchall()
    out = {}
    for r in rows:
        out[r["id"]] = {
            "name": r["name"],
            "description": r["description"],
            "price": int(r["price"]),
            "max_per_user": int(r["max_per_user"]),
            "effect": json.loads(r["effect"] or "{}"),
        }
    return out


def get_item(item_id: str) -> Optional[dict]:
    return all_items().get(item_id)


def set_item(item_id: str, item: dict) -> None:
    _write_item(item_id, item)


def delete_item(item_id: str) -> None:
    conn = db.get_conn()
    conn.execute("DELETE FROM shop_items WHERE id = ?", (item_id,))


def _purchase_count(user: dict, item_id: str) -> int:
    purchases = user.get("shop_purchases") or {}
    if isinstance(purchases, str):
        try:
            purchases = json.loads(purchases)
        except Exception:
            purchases = {}
    return int(purchases.get(item_id, 0))


def buy(user_id: int, item_id: str) -> tuple:
    item = get_item(item_id)
    if not item:
        return False, "Item not found."

    user = db.get_user(user_id) or {"id": int(user_id)}
    purchases = user.get("shop_purchases") or {}
    if isinstance(purchases, str):
        try:
            purchases = json.loads(purchases)
        except Exception:
            purchases = {}

    count = _purchase_count(user, item_id)
    max_per_user = int(item.get("max_per_user", 1))
    if count >= max_per_user:
        return False, f"Purchase limit reached ({max_per_user})."

    price = int(item.get("price", 0))
    if not spend(user_id, price, f"shop:{item_id}", meta={"item": item_id}):
        return False, "Insufficient diamonds."

    purchases[item_id] = count + 1
    db.upsert_user(user_id, shop_purchases=json.dumps(purchases))
    log.info(f"shop purchase user={user_id} item={item_id} price={price}")
    return True, item


def apply_to_limits(user_id: int, base_limits: dict) -> dict:
    user = db.get_user(user_id) or {}
    lim = dict(base_limits)
    lim["max_jobs"] = lim.get("max_jobs", 1) + int(user.get("extra_job_slots", 0) or 0)
    lim["min_interval"] = max(
        30, int(lim.get("min_interval", 300)) - int(user.get("interval_discount", 0) or 0)
    )
    lim["max_duration"] = int(lim.get("max_duration", 120)) + int(
        user.get("duration_extension", 0) or 0
    )
    return lim