"""Read helpers for the transaction ledger (backed by SQLite)."""
from typing import List

from . import db


def for_user(user_id: int, limit: int = 100) -> List[dict]:
    return db.user_transactions(user_id, limit)


def recent(limit: int = 200) -> List[dict]:
    return db.recent_transactions(limit)


def top_spenders(limit: int = 10) -> List[dict]:
    return db.tx_top_spenders(limit)


def totals() -> dict:
    return db.tx_totals()