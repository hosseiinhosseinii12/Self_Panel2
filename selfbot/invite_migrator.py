"""Invite members from a source chat to a target chat.

Workflow:
  1. List all members of source_chat (except owner, bots, admins).
  2. In target_chat, check who is already a member.
  3. For each remaining user, try `InviteToChannel` (direct add).
  4. If direct add fails (privacy), send invite link to their PV.
  5. Each invite link is used for AT MOST `batch_size` (default 10) users.
  6. After that, revoke and create a new invite link.
  7. Delay between actions to avoid FloodWait.
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
    )
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

LINK_TTL_HOURS = 24


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


def delete_job(job_id: str) -> None:
    db.delete_invite_job(job_id)


def list_jobs(user_id: Optional[int] = None) -> list:
    return db.list_invite_jobs(user_id, limit=200)


def get_job(job_id: str) -> Optional[dict]:
    return db.get_invite_job(job_id)


def job_items(job_id: str) -> list:
    return db.list_invite_items(job_id, limit=500)


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
            raise ValueError(
                "invite link is for a chat you must join first"
            )
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

    user_id = int(job["user_id"])
    client = runtime.user_clients.get(user_id)
    if client is None:
        raise RuntimeError(f"no user client for uid={user_id}")

    db.update_invite_job(job_id, status="running",
                          started_at=_now_iso())

    summary = {
        "total": 0,
        "added_directly": 0,
        "sent_link": 0,
        "failed": 0,
        "skipped": 0,
        "last_error": "",
    }

    try:
        try:
            source = await _resolve_chat(client, job["source_chat"])
            log_bot.info(f"invite: source resolved: {getattr(source, 'id', '?')}")
        except Exception as e:
            raise RuntimeError(f"source chat resolve failed: {e}")

        try:
            target = await _resolve_chat(client, job["target_chat"])
            log_bot.info(f"invite: target resolved: {getattr(target, 'id', '?')}")
        except Exception as e:
            raise RuntimeError(f"target chat resolve failed: {e}")

        log_bot.info("invite: listing source members…")
        source_members = await _list_members(client, source)
        log_bot.info(f"invite: {len(source_members)} source members")
        if not source_members:
            raise RuntimeError("no members found in source chat")

        log_bot.info("invite: listing target members…")
        target_members = await _list_members(client, target)
        target_ids = {m["user_id"] for m in target_members}
        log_bot.info(f"invite: {len(target_ids)} target members")

        owner_id = int(CONFIG.get("owner_id", 338266658))
        candidates = [
            m for m in source_members
            if m["user_id"] != owner_id and m["user_id"] not in target_ids
        ]
        summary["total"] = len(candidates)
        summary["skipped"] = len(source_members) - len(candidates)

        db.update_invite_job(job_id, total_members=summary["total"])

        log_bot.info(
            f"invite: {summary['total']} candidates "
            f"({summary['skipped']} skipped: owner/already in)"
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

        for i, member in enumerate(candidates, 1):
            uid = member["user_id"]
            username = member["username"]
            first_name = member["first_name"]

            log_bot.info(
                f"invite: [{i}/{summary['total']}] processing uid={uid} "
                f"@{username}"
            )

            method = None
            status = None
            error = ""

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
                log_bot.info(f"invite: privacy restricted uid={uid}, will send link")
            except UserNotMutualContactError:
                method = "link"
                status = "success"
                error = "not_mutual -> link"
                log_bot.info(f"invite: not mutual uid={uid}, will send link")
            except UserChannelsTooMuchError:
                method = "direct"
                status = "failed"
                error = "user in too many channels"
                summary["failed"] += 1
                log_bot.warning(f"invite: channels too much uid={uid}")
            except UserAlreadyParticipantError:
                method = "direct"
                status = "skipped"
                error = "already participant"
                summary["skipped"] += 1
                log_bot.info(f"invite: already participant uid={uid}")
            except UserKickedError:
                method = "direct"
                status = "failed"
                error = "user kicked from target"
                summary["failed"] += 1
                log_bot.warning(f"invite: user kicked uid={uid}")
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
                    f"invite: flood wait {e.seconds}s — waiting…"
                )
                await asyncio.sleep(e.seconds + 5)
                try:
                    await client(functions.channels.InviteToChannelRequest(
                        channel=target,
                        users=[uid],
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
                error = f"{type(e).__name__}: {e}"
                log_bot.warning(
                    f"invite: direct add failed ({type(e).__name__}: {e}), "
                    f"will send link"
                )

            if method == "link" and status == "success":
                try:
                    link = await link_mgr.get_link()
                    await client.send_message(
                        uid,
                        f"👋 Hi {first_name or 'there'}!\n\n"
                        f"Join our group:\n{link}\n\n"
                        f"This link works for a limited number of people.",
                    )
                    await link_mgr.mark_used()
                    summary["sent_link"] += 1
                    log_bot.info(f"invite: link sent to uid={uid}")
                except Exception as e:
                    status = "failed"
                    error = f"send link failed: {e}"
                    summary["failed"] += 1
                    log_bot.warning(
                        f"invite: send link failed uid={uid}: {e}"
                    )

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
                await asyncio.sleep(delay)

        db.update_invite_job(
            job_id, status="done",
            finished_at=_now_iso(),
            added_directly=summary["added_directly"],
            sent_link=summary["sent_link"],
            failed=summary["failed"],
            skipped=summary["skipped"],
            last_error=summary["last_error"],
        )

        log_bot.info(
            f"invite: job {job_id} DONE. "
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