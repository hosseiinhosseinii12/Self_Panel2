"""Invite members from a source chat to a target chat.

Workflow:
  1. List all members of source_chat.
  2. Exclude: bots, deleted, owner, self-bot account, target members,
     admins of source, admins of target.
  3. For each remaining user:
       a. Try `InviteToChannel` (direct add).
       b. If direct add fails (privacy / not mutual), send invite link via PV.
       c. If send link fails (blocked), mark as failed.
  4. Each invite link is used for AT MOST `batch_size` (default 10) users.
  5. After that, revoke and create a new invite link.
  6. Delay between actions to avoid FloodWait.
  7. Adaptive rate-limit: pause every N successful sends.

Cancellation:
  - A running job can be cancelled via `cancel_job(job_id)` which sets an
    in-memory flag. The run loop checks `is_cancelled(job_id)` at the top
    of every iteration and stops gracefully (status="cancelled").
"""
import asyncio
import secrets
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
    from telethon.tl.types import ChannelParticipantsAdmins
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False


# ===========================================================================
# Constants
# ===========================================================================
DEFAULT_BATCH_SIZE = 10
MAX_BATCH_SIZE = 10
DEFAULT_DELAY = 30
MIN_DELAY = 5
MAX_DELAY = 600

# After N successful sends, pause for RATE_LIMIT_PAUSE seconds
RATE_LIMIT_AFTER_N = 30
RATE_LIMIT_PAUSE = 60

LINK_TTL_HOURS = 24

# In-memory cancel flags: {job_id: True}
_CANCEL_FLAGS: dict = {}


def _rand_id() -> str:
    return "inv_" + secrets.token_hex(6)


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


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
    _CANCEL_FLAGS.pop(job_id, None)
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


def cancel_job(job_id: str) -> tuple:
    job = db.get_invite_job(job_id)
    if not job:
        return False, "Job not found."
    status = (job.get("status") or "").lower()
    if status != "running":
        return False, f"Job is not running (current status: {status})."
    _CANCEL_FLAGS[job_id] = True
    log_bot.info(f"invite: cancel requested for job {job_id}")
    return True, "Cancellation requested. Job will stop after the current member."


def is_cancelled(job_id: str) -> bool:
    return bool(_CANCEL_FLAGS.get(job_id, False))


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
    """Return set of user_ids who are admins/creator in the chat."""
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
# Invite link management
# ===========================================================================
class InviteLinkManager:
    def __init__(self, client, target_chat, batch_size: int = 10,
                 ttl_hours: int = LINK_TTL_HOURS):
        self.client = client
        self.target_chat = target_chat
        self.batch_size = min(MAX_BATCH_SIZE, max(1, int(batch_size)))
        self.ttl_seconds = ttl_hours * 3600
        self.current_link: Optional[str] = None
        self.current_uses: int = 0
        self._lock = asyncio.Lock()

    async def _create_link(self) -> str:
        try:
            result = await self.client(functions.messages.ExportChatInviteRequest(
                peer=self.target_chat,
                usage_limit=self.batch_size,
                expire_date=int(datetime.now(dt_timezone.utc).timestamp()) +
                             self.ttl_seconds,
                title=f"batch-{secrets.token_hex(3)}",
            ))
            link = result.link
            log_bot.info(
                f"invite: created link (limit={self.batch_size}) {link[:40]}..."
            )
            return link
        except Exception as e:
            log_bot.error(f"invite: create link failed: {e}")
            raise

    async def _revoke_link(self, link: str) -> None:
        try:
            await self.client(functions.messages.EditExportedChatInviteRequest(
                peer=self.target_chat,
                link=link,
                revoked=True,
            ))
            log_bot.info(f"invite: revoked link {link[:40]}...")
        except Exception as e:
            log_bot.warning(f"invite: revoke link failed: {e}")

    async def get_link(self) -> str:
        async with self._lock:
            if (self.current_link is None or
                    self.current_uses >= self.batch_size):
                if self.current_link:
                    await self._revoke_link(self.current_link)
                self.current_link = await self._create_link()
                self.current_uses = 0
            return self.current_link

    async def mark_used(self) -> None:
        async with self._lock:
            self.current_uses += 1
            log_bot.info(
                f"invite: link used {self.current_uses}/{self.batch_size}"
            )


# ===========================================================================
# Send helpers
# ===========================================================================
async def _send_invite_pv(client, uid: int, first_name: str,
                          link: str) -> bool:
    """Send an invite link via private message. Returns True on success."""
    msg = (
        f"👋 Hi {first_name or 'there'}!\n\n"
        f"Join our group:\n{link}\n\n"
        f"This link works for a limited number of people."
    )
    try:
        await client.send_message(uid, msg)
        return True
    except UserIsBlockedError:
        log_bot.warning(f"invite: user {uid} has blocked this account")
        return False
    except YouBlockedUserError:
        log_bot.warning(f"invite: this account has blocked user {uid}")
        return False
    except InputUserDeactivatedError:
        log_bot.warning(f"invite: user {uid} is deactivated")
        return False
    except FloodWaitError as e:
        log_bot.warning(f"invite: flood wait {e.seconds}s sending to {uid}")
        await asyncio.sleep(e.seconds + 3)
        try:
            await client.send_message(uid, msg)
            return True
        except Exception as e2:
            log_bot.warning(f"invite: retry send to {uid} failed: {e2}")
            return False
    except Exception as e:
        log_bot.warning(f"invite: send to {uid} failed: {type(e).__name__}: {e}")
        return False


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

    _CANCEL_FLAGS.pop(job_id, None)

    user_id = int(job["user_id"])
    client = runtime.user_clients.get(user_id)
    if client is None:
        raise RuntimeError(f"no user client for uid={user_id}")

    db.update_invite_job(job_id, status="running",
                         started_at=_now_iso(), finished_at="",
                         last_error="")

    summary = {
        "total": 0,
        "added_directly": 0,
        "sent_link": 0,
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

        link_mgr = InviteLinkManager(
            client, target,
            batch_size=job.get("batch_size", DEFAULT_BATCH_SIZE),
        )
        delay = max(MIN_DELAY, min(MAX_DELAY,
                                   int(job.get("delay_seconds", DEFAULT_DELAY))))

        # ---------- main loop ----------
        sent_count = 0
        for i, member in enumerate(candidates, 1):
            if is_cancelled(job_id):
                summary["cancelled"] = True
                log_bot.info(
                    f"invite: job {job_id} cancelled at "
                    f"{i - 1}/{summary['total']}"
                )
                break

            # adaptive rate-limiting: pause every N successful sends
            if sent_count > 0 and sent_count % RATE_LIMIT_AFTER_N == 0:
                log_bot.info(
                    f"invite: rate-limit pause {RATE_LIMIT_PAUSE}s "
                    f"after {sent_count} sends"
                )
                try:
                    for _ in range(RATE_LIMIT_PAUSE):
                        if is_cancelled(job_id):
                            break
                        await asyncio.sleep(1)
                except asyncio.CancelledError:
                    raise

            uid = member["user_id"]
            username = member["username"]
            first_name = member["first_name"]

            log_bot.info(
                f"invite: [{i}/{summary['total']}] uid={uid} @{username}"
            )

            method = None
            status = None
            error = ""

            # ---------- 1. try direct add ----------
            try:
                await client(functions.channels.InviteToChannelRequest(
                    channel=target,
                    users=[uid],
                ))
                method = "direct"
                status = "success"
                summary["added_directly"] += 1
                log_bot.info(f"invite: direct add OK uid={uid}")
            except UserPrivacyRestrictedError:
                method = "link"
                status = "success"
                error = "privacy_restricted -> link"
            except UserNotMutualContactError:
                method = "link"
                status = "success"
                error = "not_mutual -> link"
            except UserChannelsTooMuchError:
                method = "direct"
                status = "failed"
                error = "user in too many channels"
                summary["failed"] += 1
            except UserAlreadyParticipantError:
                method = "direct"
                status = "skipped"
                error = "already participant"
                summary["skipped"] += 1
            except UserKickedError:
                method = "direct"
                status = "failed"
                error = "user kicked from target"
                summary["failed"] += 1
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
            except FloodWaitError as e:
                log_bot.warning(
                    f"invite: flood wait {e.seconds}s before direct add uid={uid}"
                )
                await asyncio.sleep(e.seconds + 5)
                try:
                    await client(functions.channels.InviteToChannelRequest(
                        channel=target, users=[uid],
                    ))
                    method = "direct"
                    status = "success"
                    summary["added_directly"] += 1
                except Exception as e2:
                    method = "link"
                    status = "success"
                    error = f"flood -> link: {e2}"
            except Exception as e:
                method = "link"
                status = "success"
                error = f"direct_failed: {type(e).__name__}"
                log_bot.info(
                    f"invite: direct add fallback to link uid={uid} "
                    f"({type(e).__name__}: {e})"
                )

            # ---------- 2. send link via PV ----------
            if method == "link" and status == "success":
                try:
                    link = await link_mgr.get_link()
                    ok = await _send_invite_pv(
                        client, uid, first_name, link,
                    )
                    if ok:
                        await link_mgr.mark_used()
                        summary["sent_link"] += 1
                        sent_count += 1
                        log_bot.info(f"invite: link sent to uid={uid}")
                    else:
                        status = "failed"
                        error = "send link failed (blocked/deactivated)"
                        summary["failed"] += 1
                except Exception as e:
                    status = "failed"
                    error = f"send link failed: {e}"
                    summary["failed"] += 1
                    log_bot.warning(
                        f"invite: send link failed uid={uid}: {e}"
                    )

            # ---------- 3. record ----------
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

            if i < summary["total"]:
                remaining = delay
                while remaining > 0:
                    if is_cancelled(job_id):
                        break
                    step = min(1.0, remaining)
                    await asyncio.sleep(step)
                    remaining -= step
                if is_cancelled(job_id):
                    summary["cancelled"] = True
                    log_bot.info(
                        f"invite: job {job_id} cancelled during delay "
                        f"at {i}/{summary['total']}"
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
            f"link={summary['sent_link']} "
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
        _CANCEL_FLAGS.pop(job_id, None)