"""Sync sessions between local storage and remote destinations (R2/Telegram)."""
import asyncio
import os
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Optional

from .config import CONFIG, DB_PATH
from .logging_setup import log_backup
from .session_manager import (
    SESSIONS_BACKUP_DIR,
    backup_all_sessions,
    prune_session_backups,
    restore_sessions_from_zip,
)


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


# ===========================================================================
# Volume (local)
# ===========================================================================
def _volume_dir() -> Optional[Path]:
    d = os.environ.get("DATA_DIR") or str(DB_PATH)
    p = Path(d)
    if not p.exists():
        return None
    return p


def _backup_to_volume(zip_path: Path) -> bool:
    """Copy ZIP to the persistent volume (already there in DB_PATH)."""
    return zip_path.exists()


def _fetch_from_volume() -> bool:
    """Sessions are in DB already; no-op."""
    return True


# ===========================================================================
# R2 (S3-compatible)
# ===========================================================================
def _r2_configured() -> bool:
    return all(os.environ.get(k) for k in (
        "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET", "R2_ENDPOINT",
    ))


def _get_s3_client():
    try:
        import boto3  # type: ignore
    except ImportError:
        log_backup.warning("boto3 not installed — R2/S3 disabled")
        return None
    try:
        return boto3.client(
            "s3",
            endpoint_url=os.environ["R2_ENDPOINT"],
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )
    except Exception as e:
        log_backup.error(f"could not create S3 client: {e}")
        return None


def _backup_to_r2(zip_path: Path) -> bool:
    if not _r2_configured():
        return False
    client = _get_s3_client()
    if not client:
        return False
    try:
        client.upload_file(
            str(zip_path),
            os.environ["R2_BUCKET"],
            f"sessions/{zip_path.name}",
        )
        log_backup.info(f"uploaded {zip_path.name} to R2")
        return True
    except Exception as e:
        log_backup.error(f"R2 upload failed: {e}")
        return False


def _fetch_from_r2() -> bool:
    if not _r2_configured():
        return False
    client = _get_s3_client()
    if not client:
        return False
    try:
        resp = client.list_objects_v2(
            Bucket=os.environ["R2_BUCKET"], Prefix="sessions/",
        )
        items = resp.get("Contents", [])
        if not items:
            return False
        items.sort(key=lambda x: x["LastModified"], reverse=True)
        latest = items[0]["Key"]
        local = SESSIONS_BACKUP_DIR / Path(latest).name
        client.download_file(os.environ["R2_BUCKET"], latest, str(local))
        log_backup.info(f"downloaded {latest} from R2")
        data = local.read_bytes()
        encrypted = local.name.endswith(".enc")
        restore_sessions_from_zip(data, encrypted=encrypted)
        return True
    except Exception as e:
        log_backup.error(f"R2 fetch failed: {e}")
        return False


# ===========================================================================
# Telegram (send ZIP to owner DM)
# ===========================================================================
def _backup_to_telegram(zip_path: Path) -> bool:
    if os.environ.get("BACKUP_TO_TELEGRAM", "true").lower() not in ("1", "true", "yes", "on"):
        return False
    try:
        from .bootstrap import _async_loop
        from app import _get_runtime
        rt = _get_runtime()
    except Exception as e:
        log_backup.warning(f"cannot get runtime: {e}")
        return False

    if rt is None or rt.bot_client is None or _async_loop is None:
        return False

    owner_id = int(CONFIG.get("owner_id", 338266658))

    async def _send():
        try:
            await rt.bot_client.send_file(
                owner_id, str(zip_path),
                caption=f"Session backup {zip_path.name}",
            )
            return True
        except Exception as e:
            log_backup.error(f"telegram send failed: {e}")
            return False

    try:
        fut = asyncio.run_coroutine_threadsafe(_send(), _async_loop)
        return bool(fut.result(timeout=60))
    except Exception as e:
        log_backup.error(f"telegram dispatch failed: {e}")
        return False


def _fetch_from_telegram() -> bool:
    """Fetch the latest session backup from owner's DM with the bot."""
    try:
        from .bootstrap import _async_loop
        from app import _get_runtime
        rt = _get_runtime()
    except Exception:
        return False

    if rt is None or rt.bot_client is None or _async_loop is None:
        return False

    owner_id = int(CONFIG.get("owner_id", 338266658))

    async def _fetch():
        try:
            async for msg in rt.bot_client.iter_messages(owner_id, limit=50):
                if msg.document:
                    name = ""
                    for attr in getattr(msg.document, "attributes", []):
                        fn = getattr(attr, "file_name", None)
                        if fn:
                            name = fn
                            break
                    if name.startswith("sessions_") and name.endswith((".zip", ".zip.enc")):
                        local = SESSIONS_BACKUP_DIR / name
                        await rt.bot_client.download_media(msg, file=str(local))
                        data = local.read_bytes()
                        encrypted = name.endswith(".enc")
                        restore_sessions_from_zip(data, encrypted=encrypted)
                        return True
        except Exception as e:
            log_backup.error(f"telegram fetch failed: {e}")
        return False

    try:
        fut = asyncio.run_coroutine_threadsafe(_fetch(), _async_loop)
        return bool(fut.result(timeout=120))
    except Exception:
        return False


# ===========================================================================
# Orchestration
# ===========================================================================
def backup_to_all_sources(zip_path: Path) -> dict:
    results = {}
    for name, fn in (
        ("volume", _backup_to_volume),
        ("r2", _backup_to_r2),
        ("telegram", _backup_to_telegram),
    ):
        try:
            results[name] = bool(fn(zip_path))
        except Exception as e:
            results[name] = False
            log_backup.warning(f"{name} backup failed: {e}")
    return results


def fetch_all_sessions() -> dict:
    priority = os.environ.get("FETCH_PRIORITY", "volume,r2,telegram").split(",")
    priority = [p.strip() for p in priority if p.strip()]

    fns = {
        "volume": _fetch_from_volume,
        "r2": _fetch_from_r2,
        "telegram": _fetch_from_telegram,
    }

    results = {}
    for name in priority:
        fn = fns.get(name)
        if not fn:
            continue
        try:
            ok = bool(fn())
            results[name] = ok
            if ok:
                break
        except Exception as e:
            results[name] = False
            log_backup.warning(f"{name} fetch failed: {e}")
    return results


def scheduled_session_backup() -> Optional[Path]:
    """Full backup + upload + prune."""
    try:
        path = backup_all_sessions(encrypt_zip=True)
        backup_to_all_sources(path)
        prune_session_backups(keep=int(os.environ.get("BACKUP_KEEP", "10")))
        return path
    except Exception as e:
        log_backup.exception(f"scheduled session backup failed: {e}")
        return None