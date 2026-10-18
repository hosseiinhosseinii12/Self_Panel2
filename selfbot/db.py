"""SQLite storage layer — thread-safe, WAL mode."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone as dt_timezone
from typing import Dict, List, Optional

from .config import DB_PATH

DB_FILE = DB_PATH / "selfbot.db"
_local = threading.local()
_init_lock = threading.Lock()
_initialized = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(DB_FILE), timeout=30.0, isolation_level=None,
        check_same_thread=False,
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


@contextmanager
def tx():
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    diamonds INTEGER DEFAULT 0,
    plan TEXT DEFAULT 'member',
    banned INTEGER DEFAULT 0,
    referral_code TEXT DEFAULT '',
    referred_by INTEGER,
    referrals TEXT DEFAULT '[]',
    notify TEXT DEFAULT '{}',
    created_at TEXT DEFAULT '',
    last_seen TEXT DEFAULT '',
    clock_on INTEGER DEFAULT 0,
    interval INTEGER DEFAULT 5,
    timezone TEXT DEFAULT 'Asia/Tehran',
    base_name TEXT DEFAULT 'User',
    name_font TEXT DEFAULT 'normal',
    clock_font TEXT DEFAULT 'double',
    custom_name_font TEXT DEFAULT '',
    custom_clock_font TEXT DEFAULT '',
    job_target INTEGER,
    web_token TEXT DEFAULT '',
    session_string TEXT DEFAULT '',
    data TEXT DEFAULT '{}',
    casino_unlocked INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_users_web_token ON users(web_token);
CREATE INDEX IF NOT EXISTS idx_users_referral ON users(referral_code);
CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen);
CREATE INDEX IF NOT EXISTS idx_users_casino_unlocked ON users(casino_unlocked);

CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    amount INTEGER,
    reason TEXT,
    balance_after INTEGER,
    kind TEXT,
    created_at TEXT,
    meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_tx_created ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_tx_kind ON transactions(kind);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    owner_id INTEGER,
    chat_id INTEGER,
    interval INTEGER,
    duration INTEGER,
    text TEXT,
    sent INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    created_at TEXT,
    finished_at TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    type TEXT,
    amount INTEGER,
    plan TEXT,
    note TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT,
    decided_at TEXT,
    decided_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_req_user ON requests(user_id);
CREATE INDEX IF NOT EXISTS idx_req_status ON requests(status);
CREATE INDEX IF NOT EXISTS idx_req_type ON requests(type);

CREATE TABLE IF NOT EXISTS audit (
    id TEXT PRIMARY KEY,
    admin_id INTEGER DEFAULT 0,
    admin_name TEXT DEFAULT '',
    action TEXT,
    target TEXT,
    ip TEXT,
    created_at TEXT,
    meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_admin ON audit(admin_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(created_at);

CREATE TABLE IF NOT EXISTS templates (
    user_id INTEGER,
    name TEXT,
    text TEXT,
    PRIMARY KEY (user_id, name)
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    owner_id INTEGER,
    chat_id INTEGER,
    text TEXT,
    sent INTEGER,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    label TEXT DEFAULT '',
    session_string TEXT NOT NULL,
    api_id INTEGER,
    api_hash TEXT,
    device_model TEXT DEFAULT 'Pixel 5',
    system_version TEXT DEFAULT '11',
    app_version TEXT DEFAULT '8.4.1',
    phone TEXT DEFAULT '',
    telegram_username TEXT DEFAULT '',
    telegram_user_id INTEGER,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT '',
    last_used_at TEXT DEFAULT '',
    last_checked_at TEXT DEFAULT '',
    notes TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(is_active);
CREATE INDEX IF NOT EXISTS idx_sessions_telegram_id ON sessions(telegram_user_id);

CREATE TABLE IF NOT EXISTS shop_items (
    id TEXT PRIMARY KEY,
    name TEXT DEFAULT '',
    description TEXT DEFAULT '',
    price INTEGER DEFAULT 0,
    max_per_user INTEGER DEFAULT 1,
    effect TEXT DEFAULT '{}',
    created_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER PRIMARY KEY,
    plan TEXT DEFAULT 'member',
    expires_at TEXT,
    auto_renew INTEGER DEFAULT 0,
    started_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS admin_totp (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER DEFAULT 0,
    secret TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS kv_store (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS user_otp (
    user_id INTEGER PRIMARY KEY,
    code TEXT DEFAULT '',
    expires_at TEXT DEFAULT '',
    used INTEGER DEFAULT 0,
    created_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS text_format_settings (
    user_id INTEGER PRIMARY KEY,
    bold INTEGER DEFAULT 0,
    italic INTEGER DEFAULT 0,
    underline INTEGER DEFAULT 0,
    strike INTEGER DEFAULT 0,
    spoiler INTEGER DEFAULT 0,
    chats_filter TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS casino_config (
    user_id INTEGER PRIMARY KEY,
    enabled INTEGER DEFAULT 0,
    chat_id INTEGER,
    panel_msg_id INTEGER DEFAULT 0,
    initial_bet INTEGER DEFAULT 100000,
    current_bet INTEGER DEFAULT 100000,
    max_bet INTEGER DEFAULT 5000000,
    max_allowed_bet INTEGER DEFAULT 5000000,
    min_bet INTEGER DEFAULT 1000,
    bet_interval INTEGER DEFAULT 301,
    multipliers TEXT DEFAULT '1.5,2,2.5,3',
    strategy_mode TEXT DEFAULT 'martingale',
    last_run TEXT DEFAULT '',
    next_run TEXT DEFAULT '',
    total_plays INTEGER DEFAULT 0,
    total_wins INTEGER DEFAULT 0,
    total_losses INTEGER DEFAULT 0,
    total_pushes INTEGER DEFAULT 0,
    total_profit INTEGER DEFAULT 0,
    total_invested INTEGER DEFAULT 0,
    total_won INTEGER DEFAULT 0,
    total_lost INTEGER DEFAULT 0,
    peak_bet INTEGER DEFAULT 0,
    peak_balance INTEGER DEFAULT 0,
    last_multiplier REAL DEFAULT 0,
    last_score INTEGER DEFAULT 0,
    last_result TEXT DEFAULT '',
    current_streak INTEGER DEFAULT 0,
    best_streak INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS casino_runs (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    chat_id INTEGER,
    bet_amount INTEGER,
    multiplier REAL DEFAULT 0,
    result TEXT DEFAULT '',
    score INTEGER DEFAULT 0,
    emoji TEXT DEFAULT '',
    profit INTEGER DEFAULT 0,
    status TEXT DEFAULT 'started',
    error TEXT DEFAULT '',
    started_at TEXT DEFAULT '',
    finished_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_casino_runs_user ON casino_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_casino_runs_status ON casino_runs(status);

CREATE TABLE IF NOT EXISTS casino_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    chat_id INTEGER,
    level TEXT DEFAULT 'info',
    message TEXT DEFAULT '',
    extra TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_casino_logs_user ON casino_logs(user_id);
"""


def _add_column_if_missing(conn, table: str, col: str, decl: str) -> None:
    """Safely add a column. Ignores errors."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    except Exception:
        pass


def init_db() -> None:
    global _initialized
    with _init_lock:
        conn = get_conn()
        conn.executescript(SCHEMA)

        # ---- users: ensure newer columns exist ----
        cur = conn.execute("PRAGMA table_info(users)")
        cols = {row["name"] for row in cur.fetchall()}
        wanted = {
            "clock_on": "INTEGER DEFAULT 0",
            "interval": "INTEGER DEFAULT 5",
            "timezone": "TEXT DEFAULT 'Asia/Tehran'",
            "base_name": "TEXT DEFAULT 'User'",
            "name_font": "TEXT DEFAULT 'normal'",
            "clock_font": "TEXT DEFAULT 'double'",
            "custom_name_font": "TEXT DEFAULT ''",
            "custom_clock_font": "TEXT DEFAULT ''",
            "job_target": "INTEGER",
            "web_token": "TEXT DEFAULT ''",
            "session_string": "TEXT DEFAULT ''",
            "data": "TEXT DEFAULT '{}'",
            "casino_unlocked": "INTEGER DEFAULT 0",
        }
        for col, decl in wanted.items():
            if col not in cols:
                _add_column_if_missing(conn, "users", col, decl)

        # ---- casino_config: ensure newer columns exist ----
        cur = conn.execute("PRAGMA table_info(casino_config)")
        ccols = {row["name"] for row in cur.fetchall()}
        cwanted = {
            "panel_msg_id": "INTEGER DEFAULT 0",
            "max_allowed_bet": "INTEGER DEFAULT 5000000",
            "last_multiplier": "REAL DEFAULT 0",
            "last_score": "INTEGER DEFAULT 0",
            "last_result": "TEXT DEFAULT ''",
            "total_invested": "INTEGER DEFAULT 0",
            "total_won": "INTEGER DEFAULT 0",
            "total_lost": "INTEGER DEFAULT 0",
            "peak_balance": "INTEGER DEFAULT 0",
            "current_streak": "INTEGER DEFAULT 0",
            "best_streak": "INTEGER DEFAULT 0",
        }
        for col, decl in cwanted.items():
            if col not in ccols:
                _add_column_if_missing(conn, "casino_config", col, decl)

        _initialized = True


def _ensure_ready() -> None:
    if not _initialized:
        init_db()


# ===========================================================================
# Users
# ===========================================================================
def get_user(user_id: int) -> Optional[dict]:
    _ensure_ready()
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?",
                       (int(user_id),)).fetchone()
    return dict(row) if row else None


def upsert_user(user_id: int, **fields) -> None:
    _ensure_ready()
    conn = get_conn()
    existing = conn.execute("SELECT id FROM users WHERE id = ?",
                            (int(user_id),)).fetchone()
    if existing is None:
        cols = ["id"] + list(fields.keys())
        vals = [int(user_id)] + [
            json.dumps(v) if isinstance(v, (dict, list)) else v
            for v in fields.values()
        ]
        placeholders = ",".join("?" * len(cols))
        conn.execute(
            f"INSERT INTO users ({','.join(cols)}) VALUES ({placeholders})", vals
        )
    else:
        if not fields:
            return
        sets = []
        vals = []
        for k, v in fields.items():
            sets.append(f"{k} = ?")
            vals.append(json.dumps(v) if isinstance(v, (dict, list)) else v)
        vals.append(int(user_id))
        conn.execute(f"UPDATE users SET {','.join(sets)} WHERE id = ?", vals)


def all_users() -> Dict[str, dict]:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users").fetchall()
    return {str(r["id"]): dict(r) for r in rows}


def count_users() -> int:
    _ensure_ready()
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
    return int(row["c"]) if row else 0


def delete_user(user_id: int) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE id = ?", (int(user_id),))


def find_user_by_web_token(token: str) -> Optional[int]:
    _ensure_ready()
    if not token:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM users WHERE web_token = ? LIMIT 1", (token,)
    ).fetchone()
    return int(row["id"]) if row else None


def find_user_by_referral_code(code: str) -> Optional[int]:
    _ensure_ready()
    if not code:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM users WHERE UPPER(referral_code) = ? LIMIT 1",
        (code.strip().upper(),)
    ).fetchone()
    return int(row["id"]) if row else None


def find_user_by_username(username: str) -> Optional[int]:
    _ensure_ready()
    if not username:
        return None
    clean = username.lstrip("@").strip()
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM users WHERE LOWER(username) = ? LIMIT 1",
        (clean.lower(),)
    ).fetchone()
    return int(row["id"]) if row else None


# ===========================================================================
# Casino access lock
# ===========================================================================
def is_casino_unlocked(user_id: int) -> bool:
    u = get_user(user_id)
    if not u:
        return False
    return bool(u.get("casino_unlocked", 0))


def set_casino_unlocked(user_id: int, unlocked: bool) -> None:
    _ensure_ready()
    upsert_user(user_id, casino_unlocked=1 if unlocked else 0)


def list_casino_unlocked_users() -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM users WHERE casino_unlocked = 1 ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


# ===========================================================================
# Transactions
# ===========================================================================
def add_tx(tx: dict) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO transactions "
        "(id, user_id, amount, reason, balance_after, kind, created_at, meta) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (tx.get("id"), int(tx.get("user_id", 0)), int(tx.get("amount", 0)),
         tx.get("reason", ""), int(tx.get("balance_after", 0)),
         tx.get("kind", "grant"), tx.get("created_at", ""),
         json.dumps(tx.get("meta", {}))),
    )


def user_transactions(user_id: int, limit: int = 100) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM transactions WHERE user_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (int(user_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def recent_transactions(limit: int = 200) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM transactions ORDER BY created_at DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def tx_totals() -> dict:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute(
        "SELECT kind, SUM(amount) AS total FROM transactions GROUP BY kind"
    ).fetchall()
    out = {"granted": 0, "spent": 0, "refunded": 0}
    for r in rows:
        if r["kind"] in out:
            out[r["kind"]] = int(r["total"] or 0)
    return out


def tx_top_spenders(limit: int = 10) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute(
        "SELECT user_id, SUM(amount) AS spent FROM transactions "
        "WHERE kind='spend' GROUP BY user_id ORDER BY spent DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [{"user_id": int(r["user_id"]), "spent": int(r["spent"] or 0)}
            for r in rows]


def hourly_spend(hours: int = 24) -> List[dict]:
    _ensure_ready()
    from datetime import timedelta
    now = datetime.now(dt_timezone.utc).replace(minute=0, second=0, microsecond=0)
    buckets = {}
    for i in range(hours, -1, -1):
        h = now - timedelta(hours=i)
        buckets[h.isoformat()] = 0
    conn = get_conn()
    cutoff = (now - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT created_at, amount FROM transactions "
        "WHERE kind='spend' AND created_at >= ? ORDER BY created_at ASC",
        (cutoff,),
    ).fetchall()
    for r in rows:
        try:
            t = datetime.fromisoformat(r["created_at"]).replace(
                minute=0, second=0, microsecond=0)
        except Exception:
            continue
        key = t.isoformat()
        if key in buckets:
            buckets[key] += int(r["amount"] or 0)
    return [{"hour": k, "spent": v} for k, v in sorted(buckets.items())]


# ===========================================================================
# Jobs
# ===========================================================================
def create_job_record(job: dict) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO jobs "
        "(id, owner_id, chat_id, interval, duration, text, sent, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (job["id"], int(job["owner_id"]), int(job["chat_id"]),
         int(job["interval"]), int(job["duration"]), job["text"], 0, "running",
         job.get("created_at", datetime.now(dt_timezone.utc).isoformat())),
    )


def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    _ensure_ready()
    conn = get_conn()
    sets = []
    vals = []
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    vals.append(job_id)
    conn.execute(f"UPDATE jobs SET {','.join(sets)} WHERE id = ?", vals)


def get_job(job_id: str) -> Optional[dict]:
    _ensure_ready()
    conn = get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def active_jobs_db(owner_id: Optional[int] = None) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    if owner_id is not None:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status='running' AND owner_id = ? "
            "ORDER BY created_at DESC",
            (int(owner_id),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status='running' ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def history_recent(owner_id: Optional[int] = None, limit: int = 30) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    if owner_id is not None:
        rows = conn.execute(
            "SELECT * FROM history WHERE owner_id = ? ORDER BY id DESC LIMIT ?",
            (int(owner_id), int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def add_history(job_id: str, owner_id: int, chat_id: int, text: str,
                sent: int) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        "INSERT INTO history (job_id, owner_id, chat_id, text, sent, finished_at) "
        "VALUES (?,?,?,?,?,?)",
        (job_id, int(owner_id), int(chat_id), (text or "")[:500], int(sent),
         datetime.now(dt_timezone.utc).isoformat()),
    )


# ===========================================================================
# Requests
# ===========================================================================
def add_request(req: dict) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO requests "
        "(id, user_id, type, amount, plan, note, status, created_at, "
        "decided_at, decided_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (req["id"], int(req["user_id"]), req["type"],
         req.get("amount"), req.get("plan"), req.get("note", ""),
         req.get("status", "pending"), req["created_at"],
         req.get("decided_at"), req.get("decided_by")),
    )


def update_request(req_id: str, **fields) -> None:
    if not fields:
        return
    _ensure_ready()
    conn = get_conn()
    sets = []
    vals = []
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    vals.append(req_id)
    conn.execute(f"UPDATE requests SET {','.join(sets)} WHERE id = ?", vals)


def list_requests(status: Optional[str] = None,
                  user_id: Optional[int] = None,
                  req_type: Optional[str] = None,
                  limit: int = 200) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    q = "SELECT * FROM requests WHERE 1=1"
    vals = []
    if status:
        q += " AND status = ?"
        vals.append(status)
    if user_id is not None:
        q += " AND user_id = ?"
        vals.append(int(user_id))
    if req_type:
        q += " AND type = ?"
        vals.append(req_type)
    q += " ORDER BY created_at DESC LIMIT ?"
    vals.append(int(limit))
    rows = conn.execute(q, vals).fetchall()
    return [dict(r) for r in rows]


def find_pending_request(user_id: int, req_type: str) -> Optional[dict]:
    _ensure_ready()
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM requests WHERE user_id = ? AND type = ? "
        "AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
        (int(user_id), req_type),
    ).fetchone()
    return dict(row) if row else None


# ===========================================================================
# Audit
# ===========================================================================
def add_audit(entry: dict) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO audit "
        "(id, admin_id, admin_name, action, target, ip, created_at, meta) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (entry["id"], int(entry.get("admin_id", 0)),
         entry.get("admin_name", ""), entry["action"],
         entry.get("target"), entry.get("ip", ""), entry["created_at"],
         json.dumps(entry.get("meta", {}))),
    )


def audit_recent(limit: int = 500) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM audit ORDER BY created_at DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


# ===========================================================================
# Templates
# ===========================================================================
def save_template(user_id: int, name: str, text: str) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO templates (user_id, name, text) VALUES (?,?,?)",
        (int(user_id), name, text),
    )


def list_templates(user_id: int) -> Dict[str, str]:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute(
        "SELECT name, text FROM templates WHERE user_id = ?",
        (int(user_id),),
    ).fetchall()
    return {r["name"]: r["text"] for r in rows}


def delete_template(user_id: int, name: str) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute("DELETE FROM templates WHERE user_id = ? AND name = ?",
                 (int(user_id), name))


# ===========================================================================
# Sessions
# ===========================================================================
def add_session(user_id: int, session_string: str, label: str = "",
                api_id: Optional[int] = None, api_hash: Optional[str] = None,
                phone: str = "", notes: str = "") -> int:
    _ensure_ready()
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO sessions
           (user_id, label, session_string, api_id, api_hash, phone,
            notes, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (int(user_id), label, session_string, api_id, api_hash, phone, notes,
         datetime.now(dt_timezone.utc).isoformat()),
    )
    return cur.lastrowid


def get_session(session_id: int) -> Optional[dict]:
    _ensure_ready()
    conn = get_conn()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?",
                       (int(session_id),)).fetchone()
    return dict(row) if row else None


def list_sessions(user_id: Optional[int] = None,
                  include_inactive: bool = False) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    q = "SELECT * FROM sessions WHERE 1=1"
    vals = []
    if user_id is not None:
        q += " AND user_id = ?"
        vals.append(int(user_id))
    if not include_inactive:
        q += " AND is_active = 1"
    q += " ORDER BY user_id, id"
    rows = conn.execute(q, vals).fetchall()
    return [dict(r) for r in rows]


def update_session(session_id: int, **fields) -> None:
    if not fields:
        return
    _ensure_ready()
    conn = get_conn()
    sets = []
    vals = []
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    vals.append(int(session_id))
    conn.execute(f"UPDATE sessions SET {','.join(sets)} WHERE id = ?", vals)


def delete_session(session_id: int) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute("DELETE FROM sessions WHERE id = ?", (int(session_id),))


# ===========================================================================
# User OTP
# ===========================================================================
def set_user_otp(user_id: int, code: str, expires_at: str) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO user_otp (user_id, code, expires_at, used, created_at) "
        "VALUES (?, ?, ?, 0, ?)",
        (int(user_id), code, expires_at,
         datetime.now(dt_timezone.utc).isoformat()),
    )


def get_user_otp(user_id: int) -> Optional[dict]:
    _ensure_ready()
    conn = get_conn()
    row = conn.execute("SELECT * FROM user_otp WHERE user_id = ?",
                       (int(user_id),)).fetchone()
    return dict(row) if row else None


def find_user_by_otp(code: str) -> Optional[int]:
    _ensure_ready()
    if not code:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT user_id FROM user_otp WHERE code = ? AND used = 0 LIMIT 1",
        (code.strip(),),
    ).fetchone()
    return int(row["user_id"]) if row else None


def mark_otp_used(user_id: int) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute("UPDATE user_otp SET used = 1 WHERE user_id = ?",
                 (int(user_id),))


def clear_user_otp(user_id: int) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute("DELETE FROM user_otp WHERE user_id = ?", (int(user_id),))


# ===========================================================================
# Text format settings
# ===========================================================================
def get_text_format(user_id: int) -> Optional[dict]:
    _ensure_ready()
    conn = get_conn()
    row = conn.execute("SELECT * FROM text_format_settings WHERE user_id = ?",
                       (int(user_id),)).fetchone()
    return dict(row) if row else None


def upsert_text_format(user_id: int, **fields) -> None:
    _ensure_ready()
    conn = get_conn()
    existing = conn.execute(
        "SELECT user_id FROM text_format_settings WHERE user_id = ?",
        (int(user_id),),
    ).fetchone()
    fields["updated_at"] = datetime.now(dt_timezone.utc).isoformat()
    if existing is None:
        cols = ["user_id"] + list(fields.keys())
        vals = [int(user_id)] + list(fields.values())
        placeholders = ",".join("?" * len(cols))
        conn.execute(
            f"INSERT INTO text_format_settings ({','.join(cols)}) "
            f"VALUES ({placeholders})", vals,
        )
    else:
        sets = []
        vals = []
        for k, v in fields.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(int(user_id))
        conn.execute(
            f"UPDATE text_format_settings SET {','.join(sets)} WHERE user_id = ?",
            vals,
        )


def list_text_format_active() -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM text_format_settings "
        "WHERE bold = 1 OR italic = 1 OR underline = 1 "
        "OR strike = 1 OR spoiler = 1"
    ).fetchall()
    return [dict(r) for r in rows]


# ===========================================================================
# Casino config
# ===========================================================================
def get_casino_config(user_id: int) -> Optional[dict]:
    _ensure_ready()
    conn = get_conn()
    row = conn.execute("SELECT * FROM casino_config WHERE user_id = ?",
                       (int(user_id),)).fetchone()
    return dict(row) if row else None


def upsert_casino_config(user_id: int, **fields) -> None:
    _ensure_ready()
    conn = get_conn()
    existing = conn.execute(
        "SELECT user_id FROM casino_config WHERE user_id = ?",
        (int(user_id),),
    ).fetchone()
    fields["updated_at"] = datetime.now(dt_timezone.utc).isoformat()
    if existing is None:
        cols = ["user_id"] + list(fields.keys())
        vals = [int(user_id)] + list(fields.values())
        placeholders = ",".join("?" * len(cols))
        conn.execute(
            f"INSERT INTO casino_config ({','.join(cols)}) "
            f"VALUES ({placeholders})", vals,
        )
    else:
        if not fields:
            return
        sets = []
        vals = []
        for k, v in fields.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(int(user_id))
        conn.execute(
            f"UPDATE casino_config SET {','.join(sets)} WHERE user_id = ?",
            vals,
        )


def list_casino_enabled() -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM casino_config WHERE enabled = 1"
    ).fetchall()
    return [dict(r) for r in rows]


# ===========================================================================
# Casino runs
# ===========================================================================
def add_casino_run(run: dict) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO casino_runs
           (id, user_id, chat_id, bet_amount, multiplier, result, score,
            emoji, profit, status, error, started_at, finished_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run["id"], int(run["user_id"]), int(run["chat_id"]),
            int(run.get("bet_amount", 0)),
            float(run.get("multiplier", 0)),
            run.get("result", ""),
            int(run.get("score", 0)),
            run.get("emoji", ""),
            int(run.get("profit", 0)),
            run.get("status", "started"),
            run.get("error", ""),
            run.get("started_at", ""),
            run.get("finished_at", ""),
        ),
    )


def update_casino_run(run_id: str, **fields) -> None:
    if not fields:
        return
    _ensure_ready()
    conn = get_conn()
    sets = []
    vals = []
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    vals.append(run_id)
    conn.execute(f"UPDATE casino_runs SET {','.join(sets)} WHERE id = ?", vals)


def get_casino_run(run_id: str) -> Optional[dict]:
    _ensure_ready()
    conn = get_conn()
    row = conn.execute("SELECT * FROM casino_runs WHERE id = ?",
                       (run_id,)).fetchone()
    return dict(row) if row else None


def list_casino_runs(user_id: int = None, limit: int = 100) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    if user_id is not None:
        rows = conn.execute(
            "SELECT * FROM casino_runs WHERE user_id = ? "
            "ORDER BY started_at DESC LIMIT ?",
            (int(user_id), int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM casino_runs ORDER BY started_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def clear_casino_runs(user_id: int = None) -> int:
    _ensure_ready()
    conn = get_conn()
    if user_id is not None:
        cur = conn.execute("DELETE FROM casino_runs WHERE user_id = ?",
                          (int(user_id),))
    else:
        cur = conn.execute("DELETE FROM casino_runs")
    return cur.rowcount or 0


def casino_stats(user_id: int) -> dict:
    _ensure_ready()
    conn = get_conn()
    row = conn.execute(
        """SELECT
             COUNT(*) AS total,
             SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) AS wins,
             SUM(CASE WHEN result = 'lose' THEN 1 ELSE 0 END) AS losses,
             SUM(CASE WHEN result = 'push' THEN 1 ELSE 0 END) AS pushes,
             COALESCE(SUM(profit), 0) AS profit,
             COALESCE(SUM(bet_amount), 0) AS invested
           FROM casino_runs WHERE user_id = ?""",
        (int(user_id),),
    ).fetchone()
    if not row:
        return {"total": 0, "wins": 0, "losses": 0, "pushes": 0,
                "profit": 0, "invested": 0}
    return {
        "total": int(row["total"] or 0),
        "wins": int(row["wins"] or 0),
        "losses": int(row["losses"] or 0),
        "pushes": int(row["pushes"] or 0),
        "profit": int(row["profit"] or 0),
        "invested": int(row["invested"] or 0),
    }


# ===========================================================================
# Casino logs
# ===========================================================================
def add_casino_log(user_id: int, chat_id: int, level: str, message: str,
                   extra: str = "") -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute(
        """INSERT INTO casino_logs
           (user_id, chat_id, level, message, extra, created_at)
           VALUES (?,?,?,?,?,?)""",
        (int(user_id), int(chat_id or 0), level,
         (message or "")[:500], (extra or "")[:500],
         datetime.now(dt_timezone.utc).isoformat()),
    )


def list_casino_logs(user_id: int = None, limit: int = 100) -> List[dict]:
    _ensure_ready()
    conn = get_conn()
    if user_id is not None:
        rows = conn.execute(
            "SELECT * FROM casino_logs WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (int(user_id), int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM casino_logs ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def clear_casino_logs(user_id: int) -> None:
    _ensure_ready()
    conn = get_conn()
    conn.execute("DELETE FROM casino_logs WHERE user_id = ?", (int(user_id),))