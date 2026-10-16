"""Telegram bot runtime — login-first with QR, per-user, group-first jobs.

Includes: text-format auto-edit hook (bold/italic/underline/strike/spoiler)
attached to USER clients (self-bot), not the bot client.
Also includes /casino command to open the user casino web panel.
Casino listener is installed on the user client after login.
"""
import asyncio
import io
import os
import random
import re
import secrets
import string
import time
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Optional

from .config import CONFIG, DB_PATH, save_config
from .logging_setup import log, log_bot
from .rate_limit import user_limiter
from .store import user_state_store

from . import panels as P
from . import jobs as J
from . import economy as E
from . import requests_mod as R
from . import referrals as REF
from . import users as U
from . import clock as CLK
from . import qr_login as QRL
from . import text_format as TF
from . import user_otp as OTP
from . import casino as CAS

try:
    from telethon import Button, TelegramClient, events, functions
    from telethon.errors import (
        FloodWaitError, MessageNotModifiedError, QueryIdInvalidError,
        SessionPasswordNeededError, PhoneCodeInvalidError,
        PhoneCodeExpiredError, PhoneNumberInvalidError, PasswordHashInvalidError,
    )
except ImportError:
    TelegramClient = None  # type: ignore


# ===========================================================================
# Job flow helpers
# ===========================================================================
def _job_interval_text(target_id: int) -> str:
    return (
        "**🔁 New Repeat Job**\n\n"
        "**Step 2/4 — Interval**\n\n"
        f"✅ Target: `{target_id}`\n\n"
        "How often should the message be sent?"
    )


def _build_job_interval_buttons():
    return [
        [Button.inline("⚡ 1 min", b"job:interval:1", style="danger"),
         Button.inline("🕐 5 min", b"job:interval:5", style="primary")],
        [Button.inline("⏰ 15 min", b"job:interval:15", style="primary"),
         Button.inline("🕓 30 min", b"job:interval:30", style="primary")],
        [Button.inline("🕕 60 min", b"job:interval:60", style="primary")],
        [Button.inline("✏️ Custom interval", b"job:interval:custom", style="success")],
        [Button.inline("◀️ Back", b"job:setup:back")],
    ]


def _build_job_duration_buttons():
    return [
        [Button.inline("⏱ 1 min", b"job:duration:1", style="primary"),
         Button.inline("⏱ 5 min", b"job:duration:5", style="primary")],
        [Button.inline("⏱ 30 min", b"job:duration:30", style="primary"),
         Button.inline("⏱ 1 hour", b"job:duration:60", style="primary")],
        [Button.inline("⏱ 3 hours", b"job:duration:180", style="primary"),
         Button.inline("⏱ 6 hours", b"job:duration:360", style="primary")],
        [Button.inline("⏱ 12 hours", b"job:duration:720", style="primary")],
        [Button.inline("♾ Unlimited (until stop)", b"job:duration:0", style="success")],
        [Button.inline("✏️ Custom duration", b"job:duration:custom", style="success")],
        [Button.inline("◀️ Back", b"job:setup:back")],
    ]


def _job_duration_text(target_id: int, interval_min: int) -> str:
    return (
        "**🔁 New Repeat Job**\n\n"
        "**Step 3/4 — Duration**\n\n"
        f"✅ Target: `{target_id}`\n"
        f"✅ Interval: {interval_min} min\n\n"
        "How long should the job run?\n\n"
        "Tap **♾ Unlimited** to run until you stop it."
    )


def _job_text_buttons():
    return [
        [Button.inline("✏️ Enter text", b"job:text:enter", style="primary")],
        [Button.inline("📎 Upload .txt", b"job:text:upload", style="success")],
        [Button.inline("◀️ Back", b"job:setup:back")],
    ]


def _job_text_prompt(target_id: int = None, interval_s: int = 0,
                     duration_m: int = 0) -> str:
    lines = ["**🔁 New Repeat Job**", "", "**Step 4/4 — Message**", ""]
    if target_id:
        lines.append(f"✅ Target: `{target_id}`")
    if interval_s:
        lines.append(f"✅ Interval: {interval_s}s")
    if duration_m is not None:
        dur_str = "∞" if duration_m == 0 else f"{duration_m} min"
        lines.append(f"✅ Duration: {dur_str}")
    lines.append("")
    lines.append("Send the text you want to repeat.")
    lines.append("")
    lines.append("**Variables:** `{time}` `{date}` `{job_id}` `{sent}` `{name}`")
    return "\n".join(lines)


def _job_confirm_text(job: dict) -> str:
    from .economy import cost_job
    interval = job.get("interval", 0)
    duration = job.get("duration", 0)
    text = job.get("text", "")
    target = job.get("target")
    duration_str = "∞ (unlimited)" if duration == 0 else f"{duration} min"
    warnings = []
    if target is None:
        warnings.append("❌ No target group")
    if interval < 60:
        warnings.append("❌ Min interval: 60 seconds")
    if not text:
        warnings.append("❌ No message text set")
    status = "\n".join(warnings) if warnings else "✅ Ready to start"
    return (
        "**🔁 Job Summary**\n\n"
        f"🎯 **Target:** `{target if target else '— not selected —'}`\n"
        f"⏱ **Interval:** `{interval}` s\n"
        f"⏳ **Duration:** `{duration_str}`\n"
        f"💸 **Cost:** `{cost_job()}` 💎\n\n"
        f"📝 **Message:**\n`{text[:200] or '(empty)'}`\n\n"
        f"{status}"
    )


def _job_confirm_buttons(job: dict):
    interval = job.get("interval", 0)
    text = job.get("text", "")
    target = job.get("target")
    can_start = (target is not None and interval >= 60 and text)
    rows = []
    if can_start:
        rows.append([Button.inline("✅ Start job", b"job:confirm:start", style="success")])
    else:
        rows.append([Button.inline("⚠️ Cannot start", b"job:confirm:blocked", style="danger")])
    rows.append([Button.inline("◀️ Back", b"job:setup:back")])
    rows.append([Button.inline("❌ Cancel", b"nav:jobs", style="danger")])
    return rows


def _job_group_buttons(has_recent: bool = False):
    rows = []
    if has_recent:
        rows.append([Button.inline("🔁 Reuse last group", b"job:group:reuse", style="primary")])
    if P.HAS_PEER_PICKER:
        rows.append([Button.inline("🎯 Select a group", b"job:group:select", style="success")])
    else:
        rows.append([Button.inline("✏️ Enter chat ID", b"job:group:manual", style="success")])
    rows.append([Button.inline("◀️ Back", b"nav:jobs")])
    return rows


def _job_group_text(has_recent: bool = False, recent_id: int = None) -> str:
    txt = (
        "**🔁 New Repeat Job**\n\n"
        "**Step 1/4 — Target group**\n\n"
        "Which group should the messages be sent to?\n\n"
    )
    if P.HAS_PEER_PICKER:
        txt += "Tap **🎯 Select a group** and pick from the Telegram UI."
    else:
        txt += "Tap **✏️ Enter chat ID** and send the group's ID."
    if has_recent and recent_id:
        txt += f"\n\n_Last used: `{recent_id}`_"
    return txt


# ===========================================================================
# Runtime
# ===========================================================================
class BotRuntime:
    def __init__(self):
        self.bot_client: Optional[TelegramClient] = None
        self.user_clients: dict = {}
        self.pairing_code: str = ""
        self.owner_id: int = int(CONFIG.get("owner_id", 338266658))
        self.started: bool = False
        self.stop_event = asyncio.Event()
        self.watchdog_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._state: dict = {}
        self._recent_groups: dict = {}
        self._qr_clients: dict = {}

    def state_for(self, user_id: int) -> dict:
        return self._state.setdefault(int(user_id), {
            "step": "idle",
            "phone": None,
            "hash": None,
            "awaiting": {},
            "job_draft": {},
            "_resend_tried": False,
            "awaiting_peer": False,
        })

    def save_state(self) -> None:
        try:
            user_state_store.replace(self._state)
        except Exception:
            pass

    def load_state(self) -> None:
        st = user_state_store.all() or {}
        if isinstance(st, dict):
            self._state = {int(k): v for k, v in st.items() if str(k).isdigit()}

    def is_logged_in(self, user_id: int) -> bool:
        return self.user_clients.get(int(user_id)) is not None

    def sender_for(self, user_id: int):
        return self.user_clients.get(int(user_id)) or self.bot_client

    def recent_group(self, user_id: int) -> Optional[int]:
        return self._recent_groups.get(int(user_id))

    def set_recent_group(self, user_id: int, chat_id: int) -> None:
        self._recent_groups[int(user_id)] = int(chat_id)


def build_bot_runtime() -> BotRuntime:
    rt = BotRuntime()
    rt.load_state()
    rt.pairing_code = "".join(random.choices(string.digits, k=6))
    return rt


def _guarded(runtime: BotRuntime):
    def deco(fn):
        async def wrapper(event, *a, **kw):
            uid = event.sender_id or 0
            if uid and U.is_banned(uid):
                return
            if uid and not user_limiter.allow(uid):
                try:
                    await event.respond("Rate limited. Please slow down.")
                except Exception:
                    pass
                return
            if uid:
                U.touch(uid)
            return await fn(event, *a, **kw)
        return wrapper
    return deco


async def _safe_answer(event, text: str = "", alert: bool = False) -> None:
    try:
        await asyncio.wait_for(event.answer(text, alert=alert), timeout=5.0)
    except Exception:
        pass


async def _tidy(event) -> None:
    try:
        await event.delete()
    except Exception:
        pass


def _public_base_url() -> str:
    base = os.environ.get("PUBLIC_URL") or ""
    if base:
        return base.rstrip("/")
    dom = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    if dom:
        return f"https://{dom}"
    return "http://localhost:8080"


def _register_handlers(rt: BotRuntime) -> None:
    bot = rt.bot_client
    if bot is None:
        return

    # ---------------------------------------------------------------------
    # /start
    # ---------------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/start$", incoming=True))
    @_guarded(rt)
    async def _start(event):
        uid = event.sender_id
        try:
            U.ensure_user(uid)
        except Exception:
            pass
        st = rt.state_for(uid)
        st["job_draft"] = {}
        st["awaiting"] = {}
        st["awaiting_peer"] = False
        rt.save_state()
        try:
            await event.respond(" ", buttons=Button.clear())
            try:
                await event.delete()
            except Exception:
                pass
        except Exception:
            pass
        if not rt.is_logged_in(uid):
            await event.respond(P.login_required_text(),
                                buttons=P.login_required_buttons())
            return
        await event.respond(P.main_panel_text(uid), buttons=P.main_panel_buttons())

    # ---------------------------------------------------------------------
    # /help
    # ---------------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/help$", incoming=True))
    @_guarded(rt)
    async def _help(event):
        await event.respond(P.help_text(), buttons=P.help_buttons())

    # ---------------------------------------------------------------------
    # /login
    # ---------------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/login$", incoming=True))
    @_guarded(rt)
    async def _login(event):
        uid = event.sender_id
        try:
            U.ensure_user(uid)
        except Exception:
            pass
        if rt.is_logged_in(uid):
            await event.respond("✅ Already logged in.")
            return
        await event.respond(P.login_required_text(),
                            buttons=P.login_required_buttons())

    # ---------------------------------------------------------------------
    # /logout
    # ---------------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/logout$", incoming=True))
    @_guarded(rt)
    async def _logout(event):
        uid = event.sender_id
        client = rt.user_clients.get(uid)
        try:
            if client:
                CLK.stop_clock(uid)
                await client.log_out()
        except Exception as e:
            log_bot.warning(f"logout error: {e}")
        session_file = DB_PATH / f"user_{uid}.session"
        if session_file.exists():
            session_file.unlink()
        rt.user_clients.pop(uid, None)
        st = rt.state_for(uid)
        st["step"] = "idle"
        st["phone"] = None
        st["hash"] = None
        st["awaiting"] = {}
        st["job_draft"] = {}
        st["awaiting_peer"] = False
        rt.save_state()
        await event.respond("✅ Logged out and session deleted.")

    # ---------------------------------------------------------------------
    # /account
    # ---------------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/account$", incoming=True))
    @_guarded(rt)
    async def _account(event):
        uid = event.sender_id
        await event.respond(P.account_text(uid), buttons=P.account_buttons())

    # ---------------------------------------------------------------------
    # /web — issue OTP for user panel
    # ---------------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/web$", incoming=True))
    @_guarded(rt)
    async def _web(event):
        uid = event.sender_id
        try:
            U.ensure_user(uid)
        except Exception:
            pass
        code = OTP.generate_for_user(uid)
        base = _public_base_url()
        await event.respond(
            P.otp_text(uid, code, OTP.get_ttl()),
            buttons=[
                [Button.url("🌐 Open Web Panel", f"{base}/user/login")],
            ],
            parse_mode="md",
        )

    # ---------------------------------------------------------------------
    # /casino — open casino web panel + get OTP
    # ---------------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/casino$", incoming=True))
    @_guarded(rt)
    async def _casino(event):
        uid = event.sender_id
        try:
            U.ensure_user(uid)
        except Exception:
            pass
        code = OTP.generate_for_user(uid)
        base = _public_base_url()
        cfg = CAS.get_config(uid)
        status_emoji = "🟢" if cfg.get("enabled") else "🔴"
        status_text = "Running" if cfg.get("enabled") else "Stopped"
        chat_info = f"`{cfg['chat_id']}`" if cfg.get("chat_id") else "— not set —"
        await event.respond(
            f"**🎰 Casino Automator**\n\n"
            f"Status: {status_emoji} **{status_text}**\n"
            f"Target chat: {chat_info}\n"
            f"Current bet: **{cfg['current_bet']:,}**\n"
            f"Total plays: **{cfg['total_plays']}**\n\n"
            f"**🔐 Your access code:**\n\n"
            f"`{code}`\n\n"
            f"⏱ Valid for **5 minutes**.\n"
            f"Open the web panel below and enter this code.",
            buttons=[
                [Button.url("🌐 Open Casino Panel", f"{base}/user/login")],
                [Button.url("📊 Web Panel", f"{base}/user/casino")],
            ],
            parse_mode="md",
        )

    # ---------------------------------------------------------------------
    # /refer
    # ---------------------------------------------------------------------
    @bot.on(events.NewMessage(pattern=r"^/refer(?:\s+(\S+))?$", incoming=True))
    @_guarded(rt)
    async def _refer(event):
        code = event.pattern_match.group(1)
        uid = event.sender_id
        if not code:
            stats = REF.stats(uid)
            await event.respond(f"**🤝 Your referral code**\n\n`{stats['code']}`")
            return
        ok, msg = REF.apply_referral(uid, code)
        if ok:
            await event.respond(f"✅ Referral applied. +{E.referral_bonus()} 💎 to both sides.")
        else:
            await event.respond("❌ Invalid or already-used referral code.")

    # ---------------------------------------------------------------------
    # Raw handler (peer picker)
    # ---------------------------------------------------------------------
    @bot.on(events.Raw)
    async def raw_handler(update):
        try:
            msg = getattr(update, "message", None)
            if msg is None:
                return
            action = getattr(msg, "action", None)
            if action is None:
                return
            peers = getattr(action, "peers", None)
            if not peers:
                return
            peer = peers[0]
            chat_id = (getattr(peer, "chat_id", None)
                       or getattr(peer, "channel_id", None))
            if chat_id is None:
                return

            log_bot.info(f"peer picker: chat_id={chat_id}")
            try:
                await bot.send_message(rt.owner_id, "✅ Group selected.",
                                        buttons=Button.clear())
            except Exception:
                pass

            st = rt.state_for(rt.owner_id)
            st["pending_target"] = int(chat_id)
            st["peer_request_open"] = False
            st["awaiting"] = {}
            draft = st.setdefault("job_draft", {})
            draft["target"] = int(chat_id)
            rt.set_recent_group(rt.owner_id, int(chat_id))
            rt.save_state()

            await bot.send_message(
                rt.owner_id, _job_interval_text(int(chat_id)),
                buttons=_build_job_interval_buttons(), parse_mode="md",
            )
        except Exception as e:
            log_bot.error(f"raw_handler error: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------------
    # Callback router
    # ---------------------------------------------------------------------
    @bot.on(events.CallbackQuery())
    @_guarded(rt)
    async def _cb(event):
        await _safe_answer(event)
        data = event.data.decode() if event.data else ""
        try:
            await _route_callback(rt, event, data)
        except (QueryIdInvalidError, MessageNotModifiedError):
            pass
        except Exception as e:
            log_bot.warning(f"callback error data={data}: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------------
    # Text handler (private messages)
    # ---------------------------------------------------------------------
    @bot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    @_guarded(rt)
    async def _text(event):
        text = (event.raw_text or "").strip()
        if not text or text.startswith(("/", ".")):
            return

        uid = event.sender_id
        st = rt.state_for(uid)
        step = st.get("step") or "idle"
        awaiting = st.get("awaiting") or {}

        # ---------- QR 2FA ----------
        if awaiting.get("qr_2fa_password"):
            awaiting.pop("qr_2fa_password", None)
            rt.save_state()
            try:
                from .qr_login import finish_qr_2fa
                ok, msg = await finish_qr_2fa(rt, uid, text)
                await event.respond("✅ Logged in." if ok else f"❌ {msg}")
            except Exception as e:
                log_bot.error(f"qr 2fa failed: {e}")
                await event.respond(f"❌ 2FA failed: {e}")
            return

        # ---------- Manual chat ID (job flow) ----------
        if awaiting.get("manual_chat_id"):
            awaiting.pop("manual_chat_id", None)
            rt.save_state()
            try:
                chat_id = int(text.strip())
            except Exception:
                await event.respond("❌ Invalid chat ID.")
                return
            rt.set_recent_group(uid, chat_id)
            draft = st.setdefault("job_draft", {})
            draft["target"] = chat_id
            rt.save_state()
            await event.respond(_job_interval_text(chat_id),
                                buttons=_build_job_interval_buttons(),
                                parse_mode="md")
            return

        # ---------- Job custom interval ----------
        if awaiting.get("job_interval_custom"):
            awaiting.pop("job_interval_custom", None)
            rt.save_state()
            try:
                n = int(text)
                if n < 60:
                    await event.respond("⚠️ Minimum is 60 seconds.")
                    return
                draft = st.setdefault("job_draft", {})
                draft["interval"] = n
                rt.save_state()
                await event.respond(_job_duration_text(draft.get("target"), n // 60 or 1),
                                    buttons=_build_job_duration_buttons(),
                                    parse_mode="md")
            except Exception:
                await event.respond("❌ Invalid number.")
            return

        # ---------- Job custom duration ----------
        if awaiting.get("job_duration_custom"):
            awaiting.pop("job_duration_custom", None)
            rt.save_state()
            try:
                n = int(text)
                if n < 0:
                    await event.respond("⚠️ Send 0 (unlimited) or a positive number.")
                    return
                draft = st.setdefault("job_draft", {})
                draft["duration"] = n
                rt.save_state()
                await event.respond(_job_text_prompt(draft.get("target"),
                                                     draft.get("interval", 0), n),
                                    buttons=_job_text_buttons(), parse_mode="md")
            except Exception:
                await event.respond("❌ Invalid number.")
            return

        # ---------- Job text ----------
        if awaiting.get("job_text"):
            awaiting.pop("job_text", None)
            rt.save_state()
            draft = st.setdefault("job_draft", {})
            draft["text"] = text
            rt.save_state()
            await event.respond(_job_confirm_text(draft),
                                buttons=_job_confirm_buttons(draft),
                                parse_mode="md")
            return

        # ---------- Job text file ----------
        if awaiting.get("job_text_file") and event.document:
            awaiting.pop("job_text_file", None)
            rt.save_state()
            try:
                data_bytes = await event.download_media(bytes)
                file_text = data_bytes.decode("utf-8", errors="ignore")[:4000]
                draft = st.setdefault("job_draft", {})
                draft["text"] = file_text
                rt.save_state()
                await event.respond(_job_confirm_text(draft),
                                    buttons=_job_confirm_buttons(draft),
                                    parse_mode="md")
            except Exception as e:
                await event.respond(f"❌ Could not read file: {e}")
            return

        # ---------- Login: phone ----------
        if step == "phone":
            phone = re.sub(r"[^\d+]", "", text)
            if not phone:
                await event.respond("Send your phone number with country code.")
                return
            try:
                client = rt.user_clients.get(uid)
                if client is None:
                    from .bootstrap import make_user_client_for_login
                    client = await make_user_client_for_login(uid)
                    rt.user_clients[uid] = client

                sent = await client.send_code_request(phone)
                phone_code_hash = sent.phone_code_hash

                if not st.get("_resend_tried"):
                    st["_resend_tried"] = True
                    try:
                        sms_result = await client(functions.auth.ResendCodeRequest(
                            phone_number=phone, phone_code_hash=phone_code_hash,
                        ))
                        phone_code_hash = sms_result.phone_code_hash
                    except Exception:
                        pass

                st["step"] = "code"
                st["phone"] = phone
                st["hash"] = phone_code_hash
                rt.save_state()
                await event.respond(
                    "**🔐 Login — Step 2/2**\n\n"
                    "📨 Code sent. Check Telegram app → chat with Telegram.\n\n"
                    "Send the code with dashes, e.g. `1-2-3-4-5`."
                )
            except PhoneNumberInvalidError:
                await event.respond("❌ Invalid phone number.")
            except FloodWaitError as e:
                await event.respond(f"⏸ Rate-limited. Wait {e.seconds}s.")
            except Exception as e:
                log_bot.error(f"send_code_request failed: {type(e).__name__}: {e}")
                await event.respond(f"❌ Error: `{type(e).__name__}`")
            return

        # ---------- Login: code ----------
        if step == "code":
            code = re.sub(r"\D", "", text)
            await _tidy(event)
            client = rt.user_clients.get(uid)
            if client is None:
                await event.respond("❌ Session expired. Use /login again.")
                return
            try:
                await client.sign_in(st["phone"], code, phone_code_hash=st["hash"])
            except SessionPasswordNeededError:
                st["step"] = "2fa"
                rt.save_state()
                await event.respond("**🔐 2FA required.**\n\nSend your 2FA password.")
                return
            except PhoneCodeInvalidError:
                await event.respond("❌ Wrong code. Try again.")
                return
            except PhoneCodeExpiredError:
                st["step"] = "idle"
                st["phone"] = None
                st["hash"] = None
                st["_resend_tried"] = False
                rt.save_state()
                await event.respond("⚠️ Code expired. Use /login again.")
                return
            except Exception as e:
                log_bot.error(f"sign_in failed: {type(e).__name__}: {e}")
                await event.respond(f"❌ Error: `{type(e).__name__}`")
                return
            await _finish_login(rt, uid, event, st)
            return

        # ---------- Login: 2FA ----------
        if step == "2fa":
            await _tidy(event)
            client = rt.user_clients.get(uid)
            if client is None:
                await event.respond("❌ Session expired. Use /login again.")
                return
            try:
                await client.sign_in(password=text)
            except PasswordHashInvalidError:
                await event.respond("❌ Wrong password.")
                return
            except Exception as e:
                log_bot.error(f"2fa sign_in failed: {type(e).__name__}: {e}")
                await event.respond(f"❌ Error: `{type(e).__name__}`")
                return
            await _finish_login(rt, uid, event, st)
            return

        # ---------- Interval setting ----------
        if awaiting.get("interval"):
            awaiting.pop("interval", None)
            rt.save_state()
            try:
                n = int(text)
                if 1 <= n <= 60:
                    U.update_user_settings(uid, interval=n)
                    await event.respond(f"✅ Interval set to {n} min.")
                else:
                    await event.respond("⚠️ Must be 1–60.")
            except Exception:
                await event.respond("❌ Invalid number.")
            return

        # ---------- Timezone ----------
        if awaiting.get("timezone"):
            awaiting.pop("timezone", None)
            rt.save_state()
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(text)
                U.update_user_settings(uid, timezone=text)
                await event.respond(f"✅ Timezone set to `{text}`.")
            except Exception:
                await event.respond("❌ Invalid timezone.")
            return

        # ---------- Base name ----------
        if awaiting.get("base_name"):
            awaiting.pop("base_name", None)
            rt.save_state()
            U.update_user_settings(uid, base_name=text)
            await event.respond("✅ Base name updated.")
            return

        # ---------- Custom name font ----------
        if awaiting.get("custom_name_font"):
            awaiting.pop("custom_name_font", None)
            rt.save_state()
            if len(text) < 26:
                await event.respond("⚠️ Need at least 26 characters (A–Z).")
            else:
                U.update_user_settings(uid, custom_name_font=text[:26])
                await event.respond("✅ Custom name font saved.")
            return

        # ---------- Custom clock font ----------
        if awaiting.get("custom_clock_font"):
            awaiting.pop("custom_clock_font", None)
            rt.save_state()
            if len(text) < 10:
                await event.respond("⚠️ Need at least 10 characters (0–9).")
            else:
                U.update_user_settings(uid, custom_clock_font=text[:10])
                await event.respond("✅ Custom clock font saved.")
            return

        # ---------- Text format chat filter ----------
        if awaiting.get("tf_filter"):
            awaiting.pop("tf_filter", None)
            rt.save_state()
            if text.strip().lower() in ("all", "*", "none", ""):
                TF.set_filter(uid, "")
                await event.respond("✅ Filter cleared (all chats).",
                                    buttons=P.textformat_buttons(uid))
            else:
                TF.set_filter(uid, text.strip())
                await event.respond("✅ Filter saved.",
                                    buttons=P.textformat_buttons(uid))
            return

        # ---------- User request diamonds ----------
        user_key = f"user_reqdiamonds:{uid}"
        if awaiting.get(user_key):
            awaiting.pop(user_key, None)
            rt.save_state()
            try:
                amount = int(text)
                if not (1 <= amount <= 10000):
                    raise ValueError
            except Exception:
                await event.respond("❌ Invalid amount. Send a number 1–10000.")
                return
            req = R.create_diamond_request(uid, amount)
            await event.respond(f"✅ Request `{req['id']}` sent to admin.")
            return


def _register_user_client_format_hooks(rt: BotRuntime, user_id: int, client):
    """Attach the text-format hook to a user (self-bot) client."""
    if client is None:
        return
    if getattr(client, "_format_hook_installed", False):
        return

    @client.on(events.NewMessage(outgoing=True))
    async def _user_outgoing_format(event):
        try:
            chat_id = event.chat_id
            if chat_id is None:
                return
            text = event.raw_text or ""
            if not text.strip():
                return
            if text.startswith(("/", ".")):
                return
            log_bot.info(
                f"user outgoing: uid={user_id} chat={chat_id} "
                f"msg={event.id} text={text[:40]!r}"
            )
            await TF.handle_own_message(rt, user_id, chat_id, event.id, text)
        except Exception as e:
            log_bot.warning(
                f"user client format hook error: {type(e).__name__}: {e}"
            )

    client._format_hook_installed = True
    log_bot.info(f"format hook installed on user client uid={user_id}")


async def _finish_login(rt: BotRuntime, uid: int, event, st: dict) -> None:
    st["step"] = "idle"
    st["phone"] = None
    st["hash"] = None
    st["_resend_tried"] = False
    rt.save_state()
    try:
        client = rt.user_clients.get(uid)
        me = await client.get_me()
        log_bot.info(f"Logged in user {uid} as {me.first_name} ({me.id})")
    except Exception:
        pass

    try:
        from .session_manager import save_from_client
        client = rt.user_clients.get(uid)
        if client:
            save_from_client(uid, client)
            try:
                _register_user_client_format_hooks(rt, uid, client)
            except Exception as e:
                log_bot.warning(f"could not install format hook after login: {e}")
            try:
                from .casino import install_casino_listener
                install_casino_listener(rt, uid, client)
            except Exception as e:
                log_bot.warning(f"could not install casino listener after login: {e}")
    except Exception as e:
        log_bot.warning(f"could not persist session string: {e}")

    await event.respond(
        "✅ **Logged in!**\n\nYou can now use the bot:",
        buttons=P.main_panel_buttons(), parse_mode="md",
    )


# ===========================================================================
# Callback router
# ===========================================================================
async def _route_callback(rt: BotRuntime, event, data: str) -> None:
    bot = rt.bot_client
    uid = event.sender_id
    st = rt.state_for(uid)
    logged_in = rt.is_logged_in(uid)

    async def edit(text: str, buttons=None):
        try:
            await event.edit(text, buttons=buttons, parse_mode="md")
        except MessageNotModifiedError:
            pass
        except Exception:
            try:
                await bot.send_message(event.chat_id, text, buttons=buttons)
            except Exception:
                pass

    # ---------- auth gate ----------
    if data.startswith("nav:") and data != "nav:help" and not logged_in:
        allowed_without_login = (
            "nav:main", "nav:help", "nav:account", "nav:status",
            "nav:settings", "nav:appearance", "nav:jobs",
            "nav:textformat", "nav:webotp",
        )
        if data not in allowed_without_login:
            await edit(P.login_required_text(), P.login_required_buttons())
            return

    # ---------- auth actions ----------
    if data == "auth:start_login":
        st["step"] = "phone"
        st["phone"] = None
        st["hash"] = None
        st["_resend_tried"] = False
        rt.save_state()
        await edit("**🔐 Login — Step 1/2**\n\n📱 Send your phone number.\n\nExample: `+989121234567`")
        return

    if data == "auth:qr_login":
        await _handle_qr_login(rt, uid, event)
        return

    # ---------- navigation ----------
    if data == "nav:main":
        await edit(P.main_panel_text(uid), P.main_panel_buttons()); return
    if data == "nav:status":
        await edit(P.status_text(uid), P.status_buttons()); return
    if data == "nav:settings":
        await edit(P.settings_text(uid), P.settings_buttons()); return
    if data == "nav:appearance":
        await edit(P.appearance_text(uid), P.appearance_buttons()); return
    if data == "nav:jobs":
        st["job_draft"] = {}
        st["awaiting"] = {}
        st["awaiting_peer"] = False
        rt.save_state()
        await edit(P.jobs_text(uid), P.jobs_buttons()); return
    if data == "nav:account":
        await edit(P.account_text(uid), P.account_buttons()); return
    if data == "nav:help":
        await edit(P.help_text(), P.help_buttons()); return

    if data == "nav:webotp":
        code = OTP.generate_for_user(uid)
        base = _public_base_url()
        await edit(
            P.otp_text(uid, code, OTP.get_ttl()),
            [
                [Button.url("🌐 Open Web Panel", f"{base}/user/login")],
                [P._back_btn(b"nav:main")],
            ],
        )
        return

    # ---------- clock ----------
    if data == "clock:on":
        client = rt.user_clients.get(uid)
        if not client:
            await edit(P.login_required_text(), P.login_required_buttons())
            return
        U.update_user_settings(uid, clock_on=True)
        CLK.start_clock(client, uid)
        await edit(P.main_panel_text(uid), P.main_panel_buttons()); return

    if data == "clock:off":
        client = rt.user_clients.get(uid)
        if client:
            await CLK.stop_clock_async(client, uid)
        else:
            CLK.stop_clock(uid)
        await edit(P.main_panel_text(uid), P.main_panel_buttons()); return

    # ---------- settings ----------
    if data == "set:interval":
        st["awaiting"]["interval"] = True
        rt.save_state()
        await edit("⏱ Send interval (1–60):", [[P._back_btn(b"nav:settings")]]); return
    if data == "set:timezone":
        st["awaiting"]["timezone"] = True
        rt.save_state()
        await edit("🌍 Send timezone (e.g. Asia/Tehran):", [[P._back_btn(b"nav:settings")]]); return
    if data == "set:language":
        await edit(P.language_text(), P.language_buttons()); return
    if data.startswith("set:lang:"):
        lang = data.split(":")[-1]
        CONFIG["language"] = lang
        save_config(CONFIG)
        await edit(P.language_text(), P.language_buttons()); return

    # ---------- appearance ----------
    if data == "app:base":
        st["awaiting"]["base_name"] = True
        rt.save_state()
        await edit("✏️ Send new base name:", [[P._back_btn(b"nav:appearance")]]); return

    if data == "app:namefont":
        from .fonts import NAME_FONTS
        rows = []
        for f in NAME_FONTS.keys():
            sample = P._sample_name_font(f)
            rows.append([Button.inline(f"{f}  →  {sample}",
                                        f"app:namefont:{f}".encode(), style="primary")])
        rows.append([P._back_btn(b"nav:appearance")])
        await edit("**🔤 Pick a name font:**", rows); return

    if data.startswith("app:namefont:"):
        font = data.split(":")[-1]
        U.update_user_settings(uid, name_font=font)
        await edit(P.appearance_text(uid), P.appearance_buttons()); return

    if data == "app:clockfont":
        from .fonts import DIGIT_FONTS
        rows = []
        for f in DIGIT_FONTS.keys():
            sample = P._sample_clock_font(f)
            rows.append([Button.inline(f"{f}  →  {sample}",
                                        f"app:clockfont:{f}".encode(), style="primary")])
        rows.append([P._back_btn(b"nav:appearance")])
        await edit("**🕐 Pick a clock font:**", rows); return

    if data.startswith("app:clockfont:"):
        font = data.split(":")[-1]
        U.update_user_settings(uid, clock_font=font)
        await edit(P.appearance_text(uid), P.appearance_buttons()); return

    if data == "app:customname":
        st["awaiting"]["custom_name_font"] = True
        rt.save_state()
        await edit("🅰️ Send 26 characters for A–Z:", [[P._back_btn(b"nav:appearance")]]); return
    if data == "app:customclock":
        st["awaiting"]["custom_clock_font"] = True
        rt.save_state()
        await edit("0️⃣ Send 10 characters for 0–9:", [[P._back_btn(b"nav:appearance")]]); return

    # ---------- jobs ----------
    if data == "job:new":
        st["job_draft"] = {}
        st["awaiting"] = {}
        st["awaiting_peer"] = False
        rt.save_state()
        recent = rt.recent_group(uid)
        await edit(_job_group_text(has_recent=recent is not None, recent_id=recent),
                   _job_group_buttons(has_recent=recent is not None))
        return

    if data == "job:group:reuse":
        recent = rt.recent_group(uid)
        if not recent:
            await edit(_job_group_text(), _job_group_buttons()); return
        st.setdefault("job_draft", {})["target"] = recent
        rt.save_state()
        await edit(_job_interval_text(recent), _build_job_interval_buttons()); return

    if data == "job:group:select":
        btn = None
        if P.HAS_PEER_PICKER:
            btn = P.make_peer_picker_button(button_id=secrets.randbelow(10**6),
                                            label="🎯 Group")
        if btn is None:
            st["awaiting"]["manual_chat_id"] = True
            rt.save_state()
            await edit("**🎯 Enter group chat ID**\n\nSend the ID here, e.g. `-1001234567890`.",
                       [[P._back_btn(b"nav:jobs")]])
            return
        st["awaiting_peer"] = True
        rt.save_state()
        try:
            await bot.send_message(
                event.chat_id,
                "**🎯 Select Target Group**\n\nTap the **🎯 Group** button below.",
                buttons=[[btn]], parse_mode="md",
            )
        except Exception as e:
            log_bot.error(f"peer picker send failed: {e}")
            st["awaiting"]["manual_chat_id"] = True
            rt.save_state()
            await edit("**🎯 Enter group chat ID**\n\nSend e.g. `-1001234567890`.",
                       [[P._back_btn(b"nav:jobs")]])
        return

    if data == "job:group:manual":
        st["awaiting"]["manual_chat_id"] = True
        rt.save_state()
        await edit("**🎯 Enter group chat ID**\n\nSend the ID, e.g. `-1001234567890`.",
                   [[P._back_btn(b"nav:jobs")]])
        return

    if data.startswith("job:interval:"):
        draft = st.get("job_draft", {})
        target = draft.get("target")
        if not target:
            await edit(_job_group_text(), _job_group_buttons()); return
        val = data.split(":")[2]
        if val == "custom":
            st["awaiting"]["job_interval_custom"] = True
            rt.save_state()
            await edit("**Custom interval**\n\nSend a number (seconds, min 60):",
                       [[P._back_btn(b"job:setup:back")]])
            return
        try:
            interval_min = int(val)
        except ValueError:
            return
        draft["interval"] = interval_min * 60
        rt.save_state()
        await edit(_job_duration_text(target, interval_min),
                   _build_job_duration_buttons())
        return

    if data.startswith("job:duration:"):
        draft = st.get("job_draft", {})
        val = data.split(":")[2]
        if val == "custom":
            st["awaiting"]["job_duration_custom"] = True
            rt.save_state()
            await edit("**Custom duration**\n\nSend minutes, or 0 for unlimited:",
                       [[P._back_btn(b"job:setup:back")]])
            return
        try:
            duration = int(val)
        except ValueError:
            return
        draft["duration"] = duration
        rt.save_state()
        await edit(_job_text_prompt(draft.get("target"),
                                     draft.get("interval", 0), duration),
                   _job_text_buttons())
        return

    if data == "job:text:enter":
        st["awaiting"]["job_text"] = True
        rt.save_state()
        await edit("📝 Send the message text now:", [[P._back_btn(b"job:setup:back")]]); return
    if data == "job:text:upload":
        st["awaiting"]["job_text_file"] = True
        rt.save_state()
        await edit("📎 Send a `.txt` file with the message:",
                   [[P._back_btn(b"job:setup:back")]]); return

    if data == "job:confirm:start":
        draft = st.get("job_draft", {})
        interval = draft.get("interval", 0)
        duration = draft.get("duration", 0)
        text = draft.get("text", "")
        target = draft.get("target")

        if not target:
            await edit("❌ No target group.", [[P._back_btn(b"nav:jobs")]]); return
        if interval < 60:
            await edit("❌ Interval too small (min 60s).",
                       [[P._back_btn(b"job:setup:back")]]); return
        if not text:
            await edit("❌ No message text.",
                       [[P._back_btn(b"job:setup:back")]]); return

        ok, res = J.create_job(uid, target, interval, duration, text)
        if not ok:
            if res == "insufficient":
                bal = E.get_balance(uid)
                await edit(f"❌ Insufficient diamonds. Need {E.cost_job()}, you have {bal}.",
                           [[P._back_btn(b"job:setup:back")]])
            else:
                await edit(f"❌ {res}", [[P._back_btn(b"job:setup:back")]])
            return

        job = res
        job_id = job["id"]
        sender = rt.sender_for(uid)
        try:
            task = asyncio.create_task(J.run_job(sender, job))
            J.register_task(job_id, task)
        except Exception as e:
            await edit(f"❌ Failed to start job: {e}",
                       [[P._back_btn(b"job:setup:back")]])
            return

        st["job_draft"] = {}
        rt.save_state()

        dur_str = "∞ (unlimited)" if duration == 0 else f"{duration} min"
        await edit(
            f"✅ **Job started!**\n\n"
            f"🆔 `{job_id}`\n"
            f"🎯 Target: `{target}`\n"
            f"⏱ Interval: `{interval}` s\n"
            f"⏳ Duration: `{dur_str}`\n\n"
            f"_First message goes out in a moment._",
            [[Button.inline("📋 Jobs", b"nav:jobs", style="primary")]],
        )
        return

    if data == "job:setup:back":
        draft = st.get("job_draft", {})
        if draft.get("text") is not None and "duration" in draft:
            draft.pop("text", None)
            rt.save_state()
            await edit(_job_text_prompt(draft.get("target"),
                                         draft.get("interval", 0),
                                         draft.get("duration", 0)),
                       _job_text_buttons())
            return
        if "duration" in draft:
            draft.pop("duration", None)
            rt.save_state()
            await edit(_job_duration_text(draft.get("target"),
                                           draft.get("interval", 60) // 60 or 1),
                       _build_job_duration_buttons())
            return
        if draft.get("interval"):
            draft.pop("interval", None)
            rt.save_state()
            await edit(_job_interval_text(draft.get("target")),
                       _build_job_interval_buttons())
            return
        st["job_draft"] = {}
        rt.save_state()
        recent = rt.recent_group(uid)
        await edit(_job_group_text(has_recent=recent is not None, recent_id=recent),
                   _job_group_buttons(has_recent=recent is not None))
        return

    if data == "job:list":
        jobs = J.active_jobs(owner_id=uid)
        if not jobs:
            await edit("_No active jobs._", [[P._back_btn(b"nav:jobs")]]); return
        lines = ["**🔁 Active jobs**", ""]
        for j in jobs:
            dur = "∞" if int(j.get("duration", 0) or 0) == 0 else f"{j['duration']}m"
            lines.append(
                f"• `{j['id']}` → `{j['chat_id']}` "
                f"| ⏱ {j['interval']}s | ⏳ {dur} | sent {j['sent']}"
            )
        rows = []
        for j in jobs[:10]:
            rows.append([Button.inline(
                f"⏹ Stop {j['id']}", f"job:stop:{j['id']}".encode(), style="danger",
            )])
        rows.append([P._back_btn(b"nav:jobs")])
        await edit("\n".join(lines), rows)
        return

    if data.startswith("job:stop:"):
        job_id = data.split(":", 2)[2]
        ok = J.stop_job(job_id)
        if ok:
            await edit(f"⏹ Job `{job_id}` stopped.", [[P._back_btn(b"nav:jobs")]])
        else:
            await edit(f"❌ Job `{job_id}` not found or already stopped.",
                       [[P._back_btn(b"nav:jobs")]])
        return

    if data == "job:stopall":
        n = J.stop_all(owner_id=uid)
        await edit(f"⏹ Stopped {n} jobs.", [[P._back_btn(b"nav:jobs")]]); return

    # ---------- text format ----------
    if data == "nav:textformat":
        await edit(P.textformat_text(uid), P.textformat_buttons(uid))
        return

    if data.startswith("tf:"):
        key = data.split(":", 1)[1]
        if key == "filter":
            st["awaiting"]["tf_filter"] = True
            rt.save_state()
            await edit(
                "**📋 Chat filter**\n\n"
                "Send comma-separated chat IDs (e.g. `-1001234,-1005678`), "
                "or `all` to disable filter:",
                [[P._back_btn(b"nav:textformat")]],
            )
            return
        if key in TF.FORMAT_PAIRS:
            TF.toggle(uid, key)
            await edit(P.textformat_text(uid), P.textformat_buttons(uid))
            return

    # ---------- account ----------
    if data == "acc:reqdiamonds":
        st["awaiting"][f"user_reqdiamonds:{uid}"] = True
        rt.save_state()
        await edit("💎 **Request diamonds**\n\nSend the amount (1–10000):",
                   [[P._back_btn(b"nav:account")]])
        return
    if data == "acc:myreq":
        items = R.for_user(uid)[:20]
        if not items:
            await edit("_No requests._", [[P._back_btn(b"nav:account")]]); return
        txt = "\n".join(f"• `{r['id']}` — {r['type']} — {r['status']}" for r in items)
        await edit(txt, [[P._back_btn(b"nav:account")]]); return
    if data == "acc:referral":
        stats = REF.stats(uid)
        await edit(f"**🤝 My referral code**\n\n`{stats['code']}`\n\n"
                   f"**Total referrals:** {stats['count']}",
                   [[P._back_btn(b"nav:account")]]); return
    if data == "acc:notify":
        await edit(P.notify_text(), P.notify_buttons()); return
    if data.startswith("notify:"):
        key = data.split(":", 1)[1]
        u = U.get_user(uid) or {}
        n = (u.get("notify") or {})
        new_val = not bool(n.get(key, True))
        U.set_notify(uid, key, new_val)
        await edit(P.notify_text(), P.notify_buttons()); return


async def _handle_qr_login(rt: BotRuntime, uid: int, event) -> None:
    bot = rt.bot_client
    try:
        from .qr_login import diagnose
        d = diagnose()
        if not d["telethon"]:
            await bot.send_message(event.chat_id,
                                   f"❌ QR Login unavailable.\nTelethon error: `{d['telethon_error']}`")
            return
        if not d["qrcode"]:
            await bot.send_message(event.chat_id,
                                   f"❌ QR Login unavailable.\nqrcode error: `{d['qrcode_error']}`")
            return
    except Exception as e:
        log_bot.error(f"qr diagnose failed: {type(e).__name__}: {e}")

    try:
        ok, msg, png, expires_at = await QRL.start_qr_login(rt, uid)
    except Exception as e:
        log_bot.error(f"QR login raised: {type(e).__name__}: {e}")
        try:
            await bot.send_message(event.chat_id, f"QR login failed: {type(e).__name__}: {e}")
        except Exception:
            pass
        return

    if not ok:
        try:
            await bot.send_message(event.chat_id, f"QR login error: {msg}")
        except Exception:
            pass
        return

    try:
        remaining = max(0, int(expires_at - time.time()))
        caption = (
            "**🔐 QR Code Login**\n\n"
            "1. Open **Telegram** on your phone.\n"
            "2. Go to **Settings → Devices → Link Desktop Device**.\n"
            "3. Scan this QR code.\n\n"
            f"⏱ Expires in {remaining} seconds."
        )
        await bot.send_file(event.chat_id, io.BytesIO(png), caption=caption,
                            parse_mode="md", force_document=False)
    except Exception as e:
        log_bot.error(f"QR image send failed: {type(e).__name__}: {e}")


async def _watchdog(rt: BotRuntime) -> None:
    while not rt.stop_event.is_set():
        try:
            if rt.bot_client and not rt.bot_client.is_connected():
                await rt.bot_client.connect()
            for uid, client in list(rt.user_clients.items()):
                if client and not client.is_connected():
                    try:
                        await client.connect()
                        try:
                            if not await client.is_user_authorized():
                                log_bot.warning(f"session expired for uid={uid}")
                                try:
                                    await rt.bot_client.send_message(
                                        uid,
                                        "⚠️ Your session expired. Please /login again."
                                    )
                                except Exception:
                                    pass
                                rt.user_clients.pop(uid, None)
                        except Exception:
                            pass
                    except Exception:
                        pass
        except Exception as e:
            log_bot.warning(f"watchdog: {e}")
        try:
            await asyncio.wait_for(rt.stop_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            pass


async def start_watchdog(rt: BotRuntime) -> None:
    rt.watchdog_task = asyncio.create_task(_watchdog(rt))


async def stop_watchdog(rt: BotRuntime) -> None:
    rt.stop_event.set()
    if rt.watchdog_task:
        rt.watchdog_task.cancel()
        try:
            await rt.watchdog_task
        except Exception:
            pass