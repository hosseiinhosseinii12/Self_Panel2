"""Invite members from a source chat to a target chat.

Workflow:
  1. List all members of source_chat.
  2. Exclude: bots, deleted, owner, self-bot account, target members,
     admins of source, admins of target.
  3. For each remaining user, ONLY try to add directly:
       - Channel (supergroup / broadcast) → InviteToChannelRequest
       - Chat (legacy small group)        → AddChatUserRequest
  4. NO fallback to sending invite links. If direct add fails, mark as
     skipped (privacy / not mutual) or failed (other errors).
  5. Delay between adds can be 0 (instant, one per ~0.1s) up to 600s.
     Default is still 30s to be safe, but you can set it to 0 if you
     want to add many users very fast.
  6. Adaptive rate-limit: every N successful adds, pause for a bit.

Cancellation:
  - A running job can be cancelled via `cancel_job(job_id)` which sets a
    threading.Event. The run loop checks it at the top of every iteration
    and between 0.1s sleep chunks, so Stop takes effect within ~0.1s.
"""
import asyncio
import secrets
import threading
import traceback
from datetime import datetime, timezone as dt_timezone
from typing import Optional

from .config import CONFIG
from .logging_setup import log_bot
from . import db

try:
    from telethon import functions, types
    from telethon.errors import (
        FloodWaitError, UserPrivacyRestrictedError,
        UserNotMutualContactError, UserChannelsTooMuchError,
        ChatAdminRequiredError, UserAlreadyParticipantError,
        UserKickedError, PeerFloodError, UsersTooMuchError,
        InviteHashExpiredError, InviteHashInvalidError,
        UserIsBlockedError, YouBlockedUserError, InputUserDeactivatedError,
    )
    from telethon.tl.types import (
        ChannelParticipantsAdmins,
        Channel, Chat, User,
    )
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False


# ===========================================================================
# Constants
# ===========================================================================
DEFAULT_BATCH_SIZE = 10
MAX_BATCH_SIZE = 10

# Delay between adds can be ZERO (instant) or up to 10 minutes.
DEFAULT_DELAY = 5
MIN_DELAY = 0
MAX_DELAY = 600

# After N successful adds, pause for RATE_LIMIT_PAUSE seconds.
RATE_LIMIT_AFTER_N = 30
RATE_LIMIT_PAUSE = 30

LINK_TTL_HOURS = 24

# In-memory cancel events: {job_id: threading.Event}
_CANCEL_EVENTS: dict = {}


def _rand_id() -> str:
    return "inv_" + secrets.token_hex(6)


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def _is_channel(entity) -> bool:
    """Supergroup or broadcast channel."""
    return isinstance(entity, Channel)


def _is_basic_chat(entity) -> bool:
    """Legacy small group (not a supergroup)."""
    return isinstance(entity, Chat) and not isinstance(entity, Channel)


# ===========================================================================
# Job management
# ===========================================================================
def create_job(user_id: int, source_chat: str, target_chat: str,
               batch_size: int = DEFAULT_BATCH_SIZE,
               delay_seconds: int = DEFAULT_DELAY) -> dict:
    batch_size = max(1, min(MAX_BATCH_SIZE, int(batch_size)))
    delay_seconds = max(MIN_DELAY, min(MAX_DELAY, int(delay_seconds)))

    job = {
        "id": _rand_id(),
        "user_id": int(user_id),
        "source_chat": str(source_chat),
        "target_chat": str(target_chat),
        "batch_size": batch_size,
        "delay_seconds": delay_seconds,
        "status": "pending",
        "created_at": _now_iso(),
    }
    db.create_invite_job(job)
    log_bot.info(
        f"invite: created job {job['id']} src={source_chat} "
        f"tgt={target_chat} batch={batch_size} delay={delay_seconds}"
    )
    return job


def delete_job(job_id: str) -> bool:
    job = db.get_invite_job(job_id)
    if not job:
        return False
    if job.get("status") == "running":
        log_bot.warning(f"invite: refusing delete of running job {job_id}")
        return False
    db.delete_invite_job(job_id)
    _CANCEL_EVENTS.pop(job_id, None)
    log_bot.info(f"invite: deleted job {job_id}")
    return True


def list_jobs(user_id: Optional[int] = None) -> list:
    return db.list_invite_jobs(user_id, limit=200)


def get_job(job_id: str) -> Optional[dict]:
    return db.get_invite_job(job_id)


def job_items(job_id: str) -> list:
    return db.list_invite_items(job_id, limit=500)


# ===========================================================================
# Edit / Update
# ===========================================================================
EDITABLE_STATUSES = ("pending", "failed", "cancelled")


def update_job(job_id: str, source_chat: Optional[str] = None,
               target_chat: Optional[str] = None,
               batch_size: Optional[int] = None,
               delay_seconds: Optional[int] = None) -> tuple:
    job = db.get_invite_job(job_id)
    if not job:
        return False, "Job not found."
    status = (job.get("status") or "").lower()
    if status not in EDITABLE_STATUSES:
        return False, (
            f"Job cannot be edited while status is '{status}'. "
            f"Only {', '.join(EDITABLE_STATUSES)} jobs can be edited."
        )

    fields = {}
    if source_chat is not None:
        s = str(source_chat).strip()
        if not s:
            return False, "Source chat cannot be empty."
        fields["source_chat"] = s
    if target_chat is not None:
        t = str(target_chat).strip()
        if not t:
            return False, "Target chat cannot be empty."
        fields["target_chat"] = t
    if batch_size is not None:
        try:
            b = int(batch_size)
        except (TypeError, ValueError):
            return False, "Batch size must be a number."
        if b < 1 or b > MAX_BATCH_SIZE:
            return False, f"Batch size must be between 1 and {MAX_BATCH_SIZE}."
        fields["batch_size"] = b
    if delay_seconds is not None:
        try:
            d = int(delay_seconds)
        except (TypeError, ValueError):
            return False, "Delay must be a number."
        if d < MIN_DELAY or d > MAX_DELAY:
            return False, f"Delay must be between {MIN_DELAY} and {MAX_DELAY} seconds."
        fields["delay_seconds"] = d

    if not fields:
        return False, "Nothing to update."

    if status in ("failed", "cancelled"):
        fields["status"] = "pending"
        fields["last_error"] = ""
        fields["started_at"] = ""
        fields["finished_at"] = ""

    db.update_invite_job(job_id, **fields)
    log_bot.info(f"invite: job {job_id} updated fields={list(fields.keys())}")
    return True, ""


# ===========================================================================
# Cancel (Stop)
# ===========================================================================
def cancel_job(job_id: str) -> tuple:
    job = db.get_invite_job(job_id)
    if not job:
        return False, "Job not found."

    status = (job.get("status") or "").lower()
    if status != "running":
        return False, f"Job is not running (current status: {status})."

    ev = _CANCEL_EVENTS.get(job_id)
    if ev is None:
        log_bot.warning(
            f"invite: cancel requested for {job_id} but no Event registered"
        )
        return False, (
            "Runner not active yet. The job may have just started — "
            "wait a second and try Stop again."
        )

    ev.set()
    log_bot.info(f"invite: STOP event SET for job {job_id}")
    return True, "Stop requested. The job will halt within ~0.1 second."


def is_cancelled(job_id: str) -> bool:
    ev = _CANCEL_EVENTS.get(job_id)
    return bool(ev and ev.is_set())


# ===========================================================================
# Chat resolution
# ===========================================================================
async def _resolve_chat(client, chat_ref: str):
    ref = str(chat_ref).strip()
    if not ref:
        raise ValueError("empty chat reference")

    try:
        cid = int(ref)
        try:
            return await client.get_entity(cid)
        except Exception:
            pass
    except ValueError:
        pass

    if ref.startswith("@"):
        return await client.get_entity(ref)

    if "t.me/" in ref or "telegram.me/" in ref:
        try:
            hash_part = ref.split("/")[-1].split("?")[0]
            invite = await client(functions.messages.CheckChatInviteRequest(
                hash=hash_part
            ))
            chat = getattr(invite, "chat", None)
            if chat is not None:
                return chat
            raise ValueError("invite link is for a chat you must join first")
        except Exception as e:
            log_bot.warning(f"invite link resolve failed: {e}")

    return await client.get_entity(ref)


# ===========================================================================
# Member listing
# ===========================================================================
async def _list_members(client, chat, max_count: int = 5000) -> list:
    members = []
    try:
        async for user in client.iter_participants(chat, aggressive=True):
            if not user:
                continue
            if getattr(user, "bot", False):
                continue
            if getattr(user, "deleted", False):
                continue
            members.append({
                "user_id": int(user.id),
                "username": user.username or "",
                "first_name": user.first_name or "",
            })
            if len(members) >= max_count:
                break
    except Exception as e:
        log_bot.warning(f"list members failed: {type(e).__name__}: {e}")
    return members


async def _list_admins(client, chat) -> set:
    admin_ids = set()
    try:
        async for user in client.iter_participants(
            chat, filter=ChannelParticipantsAdmins()
        ):
            if user and not getattr(user, "bot", False):
                admin_ids.add(int(user.id))
    except Exception as e:
        log_bot.warning(f"list admins failed: {type(e).__name__}: {e}")
    return admin_ids


# ===========================================================================
# Direct-add helper — NO fallback to link
# ===========================================================================
async def _try_direct_add(client, target, uid: int) -> tuple:
    """Try to add a user directly to target.

    Returns (method, status, error):
      - method: 'direct' if any add attempt was made, else None
      - status: 'success' / 'failed' / 'skipped'
      - error:  human-readable error string (or '')
    """
    # ---- Channel (supergroup / broadcast) ----
    if _is_channel(target):
        try:
            await client(functions.channels.InviteToChannelRequest(
                channel=target, users=[uid],
            ))
            return "direct", "success", ""
        except UserPrivacyRestrictedError:
            return "direct", "skipped", "privacy_restricted"
        except UserNotMutualContactError:
            return "direct", "skipped", "not_mutual"
        except UserChannelsTooMuchError:
            return "direct", "failed", "user in too many channels"
        except UserAlreadyParticipantError:
            return "direct", "skipped", "already participant"
        except UserKickedError:
            return "direct", "failed", "user kicked from target"
        except FloodWaitError as e:
            log_bot.warning(
                f"invite: flood wait {e.seconds}s before direct add uid={uid}"
            )
            await asyncio.sleep(e.seconds + 5)
            try:
                await client(functions.channels.InviteToChannelRequest(
                    channel=target, users=[uid],
                ))
                return "direct", "success", ""
            except Exception as e2:
                return "direct", "failed", f"flood_retry_failed: {e2}"
        except Exception as e:
            return "direct", "failed", f"{type(e).__name__}: {e}"

    # ---- Legacy small Chat ----
    if _is_basic_chat(target):
        try:
            await client(functions.messages.AddChatUserRequest(
                chat_id=target.id, user_id=uid, fwd_limit=0,
            ))
            return "direct", "success", ""
        except UserPrivacyRestrictedError:
            return "direct", "skipped", "privacy_restricted"
        except UserNotMutualContactError:
            return "direct", "skipped", "not_mutual"
        except UserChannelsTooMuchError:
            return "direct", "failed", "user in too many channels"
        except UserAlreadyParticipantError:
            return "direct", "skipped", "already participant"
        except UserKickedError:
            return "direct", "failed", "user kicked from target"
        except FloodWaitError as e:
            log_bot.warning(
                f"invite: flood wait {e.seconds}s before add_chat_user uid={uid}"
            )
            await asyncio.sleep(e.seconds + 5)
            try:
                await client(functions.messages.AddChatUserRequest(
                    chat_id=target.id, user_id=uid, fwd_limit=0,
                ))
                return "direct", "success", ""
            except Exception as e2:
                return "direct", "failed", f"flood_retry_failed: {e2}"
        except Exception as e:
            return "direct", "failed", f"{type(e).__name__}: {e}"

    # ---- Unknown type ----
    return None, "failed", (
        f"target is not a Channel or Chat (type={type(target).__name__})"
    )


# ===========================================================================
# Main migrator
# ===========================================================================
async def run_job(runtime, job_id: str, progress_cb=None) -> dict:
    if not HAS_TELETHON:
        raise RuntimeError("Telethon not available")

    job = db.get_invite_job(job_id)
    if not job:
        raise ValueError(f"job {job_id} not found")
    if job["status"] == "running":
        raise ValueError(f"job {job_id} already running")
    if job["status"] == "done":
        raise ValueError(f"job {job_id} already finished")

    cancel_event = threading.Event()
    _CANCEL_EVENTS[job_id] = cancel_event
    log_bot.info(f"invite: runner registered for {job_id}")

    user_id = int(job["user_id"])
    client = runtime.user_clients.get(user_id)
    if client is None:
        _CANCEL_EVENTS.pop(job_id, None)
        raise RuntimeError(f"no user client for uid={user_id}")

    db.update_invite_job(job_id, status="running",
                         started_at=_now_iso(), finished_at="",
                         last_error="")

    summary = {
        "total": 0,
        "added_directly": 0,
        "sent_link": 0,   # kept for schema compatibility; always 0 now
        "failed": 0,
        "skipped": 0,
        "cancelled": False,
        "last_error": "",
    }

    try:
        # ---------- resolve chats ----------
        try:
            source = await _resolve_chat(client, job["source_chat"])
        except Exception as e:
            raise RuntimeError(f"source chat resolve failed: {e}")

        try:
            target = await _resolve_chat(client, job["target_chat"])
        except Exception as e:
            raise RuntimeError(f"target chat resolve failed: {e}")

        log_bot.info(
            f"invite: target type={type(target).__name__} "
            f"(is_channel={_is_channel(target)} is_chat={_is_basic_chat(target)})"
        )

        # ---------- self-bot identity ----------
        try:
            me = await client.get_me()
            self_id = int(me.id)
        except Exception:
            self_id = 0
        log_bot.info(f"invite: self-bot user_id={self_id}")

        # ---------- list source members ----------
        source_members = await _list_members(client, source)
        log_bot.info(f"invite: {len(source_members)} source members")
        if not source_members:
            raise RuntimeError("no members found in source chat")

        # ---------- list target members ----------
        target_members = await _list_members(client, target)
        target_ids = {m["user_id"] for m in target_members}
        log_bot.info(f"invite: {len(target_ids)} target members")

        # ---------- list admins of source & target ----------
        source_admins = await _list_admins(client, source)
        target_admins = await _list_admins(client, target)
        log_bot.info(
            f"invite: source admins={len(source_admins)} "
            f"target admins={len(target_admins)}"
        )

        # ---------- build exclusion set ----------
        owner_id = int(CONFIG.get("owner_id", 338266658))
        excluded = (
            {owner_id, self_id}
            | target_ids
            | source_admins
            | target_admins
        )
        excluded.discard(0)

        candidates = [
            m for m in source_members
            if m["user_id"] not in excluded
        ]
        summary["total"] = len(candidates)
        summary["skipped"] = len(source_members) - len(candidates)

        db.update_invite_job(job_id, total_members=summary["total"])

        log_bot.info(
            f"invite: {summary['total']} candidates after excluding "
            f"owner/self/bots/target-members/source-admins/target-admins "
            f"({summary['skipped']} skipped)"
        )

        if not candidates:
            db.update_invite_job(
                job_id, status="done",
                finished_at=_now_iso(),
                added_directly=0, sent_link=0, failed=0,
                skipped=summary["skipped"],
            )
            return summary

        delay = max(MIN_DELAY, min(MAX_DELAY,
                                   int(job.get("delay_seconds", DEFAULT_DELAY))))

        # ---------- main loop ----------
        success_count = 0
        for i, member in enumerate(candidates, 1):
            # --- STOP CHECK #1 ---
            if is_cancelled(job_id):
                summary["cancelled"] = True
                log_bot.info(
                    f"invite: STOP detected at iteration {i - 1}/{summary['total']}"
                )
                break

            # adaptive rate-limit: pause every N successful adds
            if success_count > 0 and success_count % RATE_LIMIT_AFTER_N == 0:
                log_bot.info(
                    f"invite: rate-limit pause {RATE_LIMIT_PAUSE}s "
                    f"after {success_count} successful adds"
                )
                for _ in range(RATE_LIMIT_PAUSE):
                    if is_cancelled(job_id):
                        summary["cancelled"] = True
                        break
                    await asyncio.sleep(1)
                if summary["cancelled"]:
                    log_bot.info("invite: STOP detected during rate-limit pause")
                    break

            uid = member["user_id"]
            username = member["username"]
            first_name = member["first_name"]

            log_bot.info(
                f"invite: [{i}/{summary['total']}] uid={uid} @{username}"
            )

            method = None
            status = None
            error = ""

            try:
                method, status, error = await _try_direct_add(client, target, uid)
            except ChatAdminRequiredError:
                raise RuntimeError(
                    "you must be admin in target chat with invite permission"
                )
            except PeerFloodError:
                raise RuntimeError(
                    "PeerFloodError — Telegram limited this account. "
                    "Wait a few hours or use a different account."
                )
            except UsersTooMuchError:
                raise RuntimeError("too many users — try fewer at once")
            except Exception as e:
                log_bot.warning(
                    f"invite: _try_direct_add unexpected error uid={uid}: "
                    f"{type(e).__name__}: {e}"
                )
                method = "direct"
                status = "failed"
                error = f"unexpected: {type(e).__name__}: {e}"

            # tally
            if status == "success":
                summary["added_directly"] += 1
                success_count += 1
                log_bot.info(f"invite: direct add OK uid={uid}")
            elif status == "skipped":
                summary["skipped"] += 1
                log_bot.info(f"invite: skipped uid={uid} ({error})")
            elif status == "failed":
                summary["failed"] += 1
                log_bot.warning(f"invite: failed uid={uid} ({error})")

            # record
            try:
                db.add_invite_item({
                    "job_id": job_id,
                    "target_user_id": uid,
                    "username": username,
                    "first_name": first_name,
                    "method": method or "",
                    "status": status or "failed",
                    "error": error,
                })
            except Exception:
                pass

            try:
                db.update_invite_job(
                    job_id,
                    added_directly=summary["added_directly"],
                    sent_link=summary["sent_link"],
                    failed=summary["failed"],
                    skipped=summary["skipped"],
                )
            except Exception:
                pass

            if progress_cb:
                try:
                    await progress_cb(i, summary["total"])
                except Exception:
                    pass

            # --- sleep between iterations, STOP CHECK #2 ---
            # If delay == 0 → tiny 0.1s pause just to yield to the event loop
            # and let Telegram pacing keep up. If delay > 0 → sleep in 0.1s
            # chunks so Stop works almost instantly even at high delays.
            if i < summary["total"]:
                if delay <= 0:
                    if is_cancelled(job_id):
                        summary["cancelled"] = True
                        break
                    await asyncio.sleep(0.1)
                else:
                    remaining = float(delay)
                    while remaining > 0:
                        if is_cancelled(job_id):
                            summary["cancelled"] = True
                            break
                        step = min(0.1, remaining)
                        await asyncio.sleep(step)
                        remaining -= step
                    if summary["cancelled"]:
                        log_bot.info(
                            f"invite: STOP detected during delay "
                            f"at iteration {i}/{summary['total']}"
                        )
                        break

        final_status = "cancelled" if summary["cancelled"] else "done"

        db.update_invite_job(
            job_id, status=final_status,
            finished_at=_now_iso(),
            added_directly=summary["added_directly"],
            sent_link=summary["sent_link"],
            failed=summary["failed"],
            skipped=summary["skipped"],
            last_error=summary["last_error"],
        )

        log_bot.info(
            f"invite: job {job_id} {final_status.upper()}. "
            f"direct={summary['added_directly']} "
            f"failed={summary['failed']} "
            f"skipped={summary['skipped']}"
        )
        return summary

    except Exception as e:
        log_bot.error(
            f"invite: job {job_id} FAILED: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        summary["last_error"] = f"{type(e).__name__}: {e}"
        db.update_invite_job(
            job_id, status="failed",
            finished_at=_now_iso(),
            last_error=summary["last_error"],
        )
        return summary
    finally:
        _CANCEL_EVENTS.pop(job_id, None)
        log_bot.info(f"invite: runner unregistered for {job_id}")