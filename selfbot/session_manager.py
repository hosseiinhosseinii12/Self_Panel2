"""Session Vault: encrypted storage, backup, restore, migrate, test.

Sessions are stored as encrypted strings in SQLite (table `sessions`).
Encryption key comes from SESSION_ENCRYPTION_KEY env var. If unset, a key
is generated once and stored in data/.session_key (chmod 600).
"""
import base64
import io
import json
import os
import zipfile
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import List, Optional

from .config import DB_PATH, get_api_credentials
from .logging_setup import log_bot
from . import db

try:
    from cryptography.fernet import Fernet, InvalidToken
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    Fernet = None  # type: ignore
    InvalidToken = Exception  # type: ignore

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import (
        AuthKeyUnregisteredError,
        UserDeactivatedError,
    )
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False
    TelegramClient = None  # type: ignore
    StringSession = None  # type: ignore
    AuthKeyUnregisteredError = Exception  # type: ignore
    UserDeactivatedError = Exception  # type: ignore


KEY_FILE = DB_PATH / ".session_key"
SESSIONS_BACKUP_DIR = DB_PATH / "sessions_backup"
SESSIONS_BACKUP_DIR.mkdir(parents=True, exist_ok=True)


# ===========================================================================
# Encryption
# ===========================================================================
_fernet: Optional["Fernet"] = None


def _get_fernet() -> Optional["Fernet"]:
    global _fernet
    if _fernet is not None:
        return _fernet
    if not HAS_CRYPTO:
        log_bot.error("cryptography not installed — session encryption disabled")
        return None

    key = os.environ.get("SESSION_ENCRYPTION_KEY", "").strip()
    if not key:
        if KEY_FILE.exists():
            key = KEY_FILE.read_text(encoding="utf-8").strip()
        else:
            key = Fernet.generate_key().decode()
            KEY_FILE.write_text(key, encoding="utf-8")
            try:
                os.chmod(KEY_FILE, 0o600)
            except Exception:
                pass
            log_bot.warning(
                f"SESSION_ENCRYPTION_KEY not set — generated at {KEY_FILE}. "
                "Add it to env for portability!"
            )
    try:
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
        return _fernet
    except Exception as e:
        log_bot.error(f"invalid SESSION_ENCRYPTION_KEY: {e}")
        return None


def encrypt(data: bytes) -> bytes:
    f = _get_fernet()
    return f.encrypt(data) if f else data


def decrypt(data: bytes) -> bytes:
    f = _get_fernet()
    if f is None:
        return data
    try:
        return f.decrypt(data)
    except InvalidToken:
        raise ValueError("decryption failed — wrong key or corrupted data")


# ===========================================================================
# CRUD
# ===========================================================================
def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def add_session(
    user_id: int,
    session_string: str,
    label: str = "",
    phone: str = "",
    notes: str = "",
    api_id: Optional[int] = None,
    api_hash: Optional[str] = None,
) -> int:
    enc = encrypt(session_string.encode("utf-8"))
    enc_b64 = base64.b64encode(enc).decode()
    sid = db.add_session(
        user_id=user_id,
        session_string=enc_b64,
        label=label,
        api_id=api_id,
        api_hash=api_hash,
        phone=phone,
        notes=notes,
    )
    log_bot.info(f"session added for uid={user_id} label={label!r} id={sid}")
    return sid


def get_session(session_id: int, decrypt_it: bool = True) -> Optional[dict]:
    d = db.get_session(session_id)
    if not d:
        return None
    if decrypt_it and d.get("session_string"):
        try:
            raw = base64.b64decode(d["session_string"])
            d["session_string"] = decrypt(raw).decode("utf-8")
        except Exception as e:
            log_bot.warning(f"decrypt failed for session {session_id}: {e}")
            d["session_string"] = ""
            d["decrypt_error"] = str(e)
    return d


def get_session_by_user(user_id: int) -> Optional[dict]:
    for s in db.list_sessions(user_id=user_id, include_inactive=False):
        return s
    return None


def list_sessions(user_id: Optional[int] = None,
                  include_inactive: bool = False) -> List[dict]:
    return db.list_sessions(user_id=user_id, include_inactive=include_inactive)


def update_session(session_id: int, **fields) -> None:
    db.update_session(session_id, **fields)


def delete_session(session_id: int) -> None:
    db.delete_session(session_id)
    log_bot.info(f"session {session_id} deleted")


def deactivate_session(session_id: int) -> None:
    db.update_session(session_id, is_active=0)


def mark_used(session_id: int) -> None:
    db.update_session(session_id, last_used_at=_now_iso())


# ===========================================================================
# Save current live session string
# ===========================================================================
def save_from_client(uid: int, client) -> bool:
    """Save the current session string from a live Telethon client."""
    try:
        session_str = client.session.save()
        if not session_str:
            return False
        # check if a session already exists for this user
        existing = list_sessions(user_id=uid, include_inactive=True)
        if existing:
            # update first, deactivate others
            primary = existing[0]
            enc = encrypt(session_str.encode("utf-8"))
            enc_b64 = base64.b64encode(enc).decode()
            db.update_session(primary["id"], session_string=enc_b64,
                              is_active=1, last_used_at=_now_iso())
            for s in existing[1:]:
                db.update_session(s["id"], is_active=0)
        else:
            add_session(uid, session_str, label=f"user-{uid}")
        return True
    except Exception as e:
        log_bot.warning(f"save_from_client failed uid={uid}: {e}")
        return False


# ===========================================================================
# Test session validity
# ===========================================================================
async def test_session(session_id: int) -> dict:
    if not HAS_TELETHON:
        return {"ok": False, "error": "telethon not available"}

    s = get_session(session_id)
    if not s or not s.get("session_string"):
        return {"ok": False, "error": "session not found or decrypt failed"}

    api_id, api_hash = get_api_credentials()
    client = TelegramClient(
        StringSession(s["session_string"]),
        int(s.get("api_id") or api_id),
        s.get("api_hash") or api_hash,
        device_model=s.get("device_model", "Pixel 5"),
        system_version=s.get("system_version", "11"),
        app_version=s.get("app_version", "8.4.1"),
    )

    try:
        await client.connect()
        if not await client.is_user_authorized():
            db.update_session(session_id, last_checked_at=_now_iso(),
                              is_active=0)
            return {"ok": False, "error": "not authorized (session expired)"}

        me = await client.get_me()
        db.update_session(
            session_id,
            last_checked_at=_now_iso(),
            telegram_username=me.username or "",
            telegram_user_id=me.id,
        )
        return {
            "ok": True,
            "user_id": me.id,
            "username": me.username,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "phone": me.phone,
        }
    except (AuthKeyUnregisteredError, UserDeactivatedError) as e:
        db.update_session(session_id, last_checked_at=_now_iso(), is_active=0)
        return {"ok": False, "error": f"auth error: {type(e).__name__}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


# ===========================================================================
# Migration from .session files
# ===========================================================================
def migrate_existing_sessions() -> int:
    """Read every user_*.session file and store its string form in DB."""
    if not HAS_TELETHON:
        log_bot.warning("telethon not available — cannot migrate sessions")
        return 0

    api_id, api_hash = get_api_credentials()
    migrated = 0

    for session_file in DB_PATH.glob("user_*.session"):
        try:
            uid = int(session_file.stem.split("_", 1)[1])
        except (IndexError, ValueError):
            continue

        existing = list_sessions(user_id=uid, include_inactive=True)
        if existing:
            continue

        try:
            client = TelegramClient(str(session_file), api_id, api_hash)
            client.connect()
            session_str = client.session.save()
            client.disconnect()
            if session_str:
                add_session(uid, session_str, label=f"migrated-{uid}")
                migrated += 1
                log_bot.info(f"migrated session for uid={uid}")
        except Exception as e:
            log_bot.warning(f"migration failed for {session_file.name}: {e}")

    log_bot.info(f"session migration complete: {migrated} sessions")
    return migrated


# ===========================================================================
# Backup / Restore
# ===========================================================================
def _timestamp() -> str:
    return datetime.now(dt_timezone.utc).strftime("%Y%m%d_%H%M%S")


def backup_all_sessions(encrypt_zip: bool = True) -> Path:
    """Export all sessions to an encrypted ZIP."""
    out_name = f"sessions_{_timestamp()}.zip" + (".enc" if encrypt_zip else "")
    out_path = SESSIONS_BACKUP_DIR / out_name

    rows = db.list_sessions(include_inactive=True)
    payload = []
    for r in rows:
        s = get_session(r["id"], decrypt_it=True)
        if s and s.get("session_string"):
            payload.append(s)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("sessions.json", json.dumps(payload, indent=2, default=str))
        zf.writestr("meta.json", json.dumps({
            "created_at": _now_iso(),
            "count": len(payload),
            "version": 1,
        }, indent=2))

    raw = buf.getvalue()
    if encrypt_zip:
        raw = encrypt(raw)

    out_path.write_bytes(raw)
    log_bot.info(
        f"session backup: {out_path.name} ({len(raw)} bytes, {len(payload)} sessions)"
    )
    return out_path


def restore_sessions_from_zip(data: bytes, encrypted: bool = True) -> dict:
    if encrypted:
        try:
            data = decrypt(data)
        except Exception as e:
            return {"restored": 0, "errors": [f"decrypt: {e}"]}

    result = {"restored": 0, "errors": []}
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:
        return {"restored": 0, "errors": [f"invalid zip: {e}"]}

    with zf:
        for member in zf.namelist():
            if ".." in member or member.startswith("/") or "\\" in member:
                result["errors"].append(f"unsafe member: {member}")
                return result

        try:
            payload = json.loads(zf.read("sessions.json").decode("utf-8"))
        except Exception as e:
            return {"restored": 0, "errors": [f"sessions.json: {e}"]}

        for s in payload:
            try:
                user_id = int(s.get("user_id"))
                session_str = s.get("session_string", "")
                if not session_str:
                    continue
                add_session(
                    user_id,
                    session_str,
                    label=s.get("label", ""),
                    phone=s.get("phone", ""),
                    notes=s.get("notes", ""),
                    api_id=s.get("api_id"),
                    api_hash=s.get("api_hash"),
                )
                result["restored"] += 1
            except Exception as e:
                result["errors"].append(str(e))

    log_bot.info(
        f"session restore: restored={result['restored']} errors={len(result['errors'])}"
    )
    return result


def list_session_backups() -> List[dict]:
    out = []
    for f in sorted(SESSIONS_BACKUP_DIR.glob("sessions_*.zip*"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        stat = f.stat()
        out.append({
            "name": f.name,
            "size_kb": round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                "%Y-%m-%d %H:%M"),
            "encrypted": f.suffix == ".enc",
        })
    return out


def prune_session_backups(keep: int = 10) -> int:
    files = sorted(SESSIONS_BACKUP_DIR.glob("sessions_*.zip*"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in files[keep:]:
        try:
            old.unlink()
            removed += 1
        except Exception:
            pass
    return removed