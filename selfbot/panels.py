"""Inline keyboard panels for the Telegram bot."""
from typing import List, Optional

from .config import CONFIG
from .users import get_user, get_user_settings

HAS_PEER_PICKER = False
KeyboardButtonRequestPeer = None
RequestPeerTypeChat = None

try:
    from telethon.tl.types import (
        KeyboardButtonRequestPeer,
        RequestPeerTypeChat,
    )
    HAS_PEER_PICKER = True
except Exception:
    try:
        from telethon.tl.types.bots import (  # type: ignore
            KeyboardButtonRequestPeer,
            RequestPeerTypeChat,
        )
        HAS_PEER_PICKER = True
    except Exception:
        pass

try:
    from telethon import Button
except ImportError:
    Button = None  # type: ignore


def _sample_name_font(font: str) -> str:
    try:
        from .fonts import apply_name_font
        return apply_name_font("Abc", font, custom="")
    except Exception:
        return "Abc"


def _sample_clock_font(font: str) -> str:
    try:
        from .fonts import apply_clock_font
        return apply_clock_font("123", font, custom="")
    except Exception:
        return "123"


def _back_btn(data: bytes = b"nav:main"):
    return Button.inline("◀️ Back", data)


# ===========================================================================
# Login
# ===========================================================================
def login_required_buttons() -> List[List]:
    return [
        [Button.inline("🔐 Login with phone", b"auth:start_login", style="success")],
        [Button.inline("📱 Login with QR code", b"auth:qr_login", style="primary")],
        [Button.inline("❓ Help", b"nav:help", style="primary")],
    ]


def login_required_text() -> str:
    return (
        "**🔐 Login required**\n\n"
        "You need to log in to your Telegram account first.\n\n"
        "**Two ways:**\n"
        "📱 **QR code** — fastest, no SMS\n"
        "📞 **Phone number** — enter number and code"
    )


# ===========================================================================
# Main panel
# ===========================================================================
def main_panel_buttons() -> List[List]:
    return [
        [Button.inline("🟢 Clock ON", b"clock:on", style="success"),
         Button.inline("🔴 Clock OFF", b"clock:off", style="danger")],
        [Button.inline("📊 Status", b"nav:status", style="primary"),
         Button.inline("✨ Text style", b"nav:textformat", style="primary")],
        [Button.inline("⚙️ Settings", b"nav:settings", style="primary"),
         Button.inline("🎨 Appearance", b"nav:appearance", style="primary")],
        [Button.inline("🔁 Jobs", b"nav:jobs", style="primary"),
         Button.inline("💎 Account", b"nav:account", style="primary")],
        [Button.inline("🌐 Web Panel", b"nav:webotp", style="primary"),
         Button.inline("❓ Help", b"nav:help", style="primary")],
    ]


def main_panel_text(user_id: int = None) -> str:
    from .clock import last_written
    from .economy import cost_clock, cost_job, get_balance
    from .text_format import active_list

    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    settings = get_user_settings(uid)
    state = "🟢 ON" if settings.get("clock_on") else "🔴 OFF"
    base = settings.get("base_name", "User")
    interval = settings.get("interval", 5)
    tz = settings.get("timezone", "Asia/Tehran")
    last = last_written(uid) or "—"
    bal = get_balance(uid)
    tf = active_list(uid)
    tf_str = " · ".join(tf) if tf else "None"

    return (
        f"**🎛 Control Panel**\n\n"
        f"**Clock:** {state}\n"
        f"**Interval:** {interval} min\n"
        f"**Timezone:** {tz}\n"
        f"**Base name:** `{base}`\n"
        f"**Last name:** `{last}`\n\n"
        f"💎 **Balance:** {bal}\n"
        f"✨ **Text style:** {tf_str}\n\n"
        f"💸 Costs: update {cost_clock()} 💎 · new job {cost_job()} 💎"
    )


# ===========================================================================
# Status / Settings / Appearance
# ===========================================================================
def status_buttons() -> List[List]:
    return [[_back_btn(b"nav:main")]]


def status_text(user_id: int = None) -> str:
    from .clock import last_written
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    s = get_user_settings(uid)
    state = "🟢 ON" if s.get("clock_on") else "🔴 OFF"
    return (
        f"**📊 Status**\n\n"
        f"**Clock:** {state}\n"
        f"**Interval:** {s.get('interval', 5)} min\n"
        f"**Timezone:** {s.get('timezone', 'Asia/Tehran')}\n"
        f"**Base name:** `{s.get('base_name', 'User')}`\n"
        f"**Name font:** {s.get('name_font', 'normal')}\n"
        f"**Clock font:** {s.get('clock_font', 'double')}\n"
        f"**Last written:** `{last_written(uid) or '—'}`"
    )


def settings_buttons() -> List[List]:
    return [
        [Button.inline("⏱ Interval", b"set:interval", style="primary"),
         Button.inline("🌍 Timezone", b"set:timezone", style="primary")],
        [Button.inline("🗣 Language", b"set:language", style="primary")],
        [_back_btn(b"nav:main")],
    ]


def settings_text(user_id: int = None) -> str:
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    s = get_user_settings(uid)
    return (
        f"**⚙️ Settings**\n\n"
        f"**Interval:** {s.get('interval', 5)} min\n"
        f"**Timezone:** {s.get('timezone', 'Asia/Tehran')}\n"
        f"**Language:** {CONFIG.get('language', 'en')}"
    )


def appearance_buttons() -> List[List]:
    return [
        [Button.inline("✏️ Base name", b"app:base", style="primary")],
        [Button.inline("🔤 Name font", b"app:namefont", style="primary"),
         Button.inline("🕐 Clock font", b"app:clockfont", style="primary")],
        [Button.inline("🅰️ Custom name font", b"app:customname", style="primary"),
         Button.inline("0️⃣ Custom clock font", b"app:customclock", style="primary")],
        [_back_btn(b"nav:main")],
    ]


def appearance_text(user_id: int = None) -> str:
    from .fonts import apply_clock_font, apply_name_font
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    s = get_user_settings(uid)
    preview_name = apply_name_font(
        s.get("base_name", "User"), s.get("name_font", "normal"),
        custom=s.get("custom_name_font", "") or "",
    )
    preview_clock = apply_clock_font(
        "12:34", s.get("clock_font", "double"),
        custom=s.get("custom_clock_font", "") or "",
    )
    return (
        f"**🎨 Appearance**\n\n"
        f"**Base name:** `{s.get('base_name', 'User')}`\n"
        f"**Name font:** `{s.get('name_font', 'normal')}`\n"
        f"**Clock font:** `{s.get('clock_font', 'double')}`\n"
        f"**Custom name:** {'✅' if s.get('custom_name_font') else '—'}\n"
        f"**Custom clock:** {'✅' if s.get('custom_clock_font') else '—'}\n\n"
        f"**Preview:** `{preview_name} {preview_clock}`"
    )


# ===========================================================================
# Jobs
# ===========================================================================
def jobs_buttons() -> List[List]:
    from .jobs import active_count
    return [
        [Button.inline(f"➕ New job ({active_count()})", b"job:new", style="success")],
        [Button.inline("📋 List jobs", b"job:list", style="primary"),
         Button.inline("⏹ Stop all", b"job:stopall", style="danger")],
        [_back_btn(b"nav:main")],
    ]


def jobs_text(user_id: int = None) -> str:
    from .jobs import active_jobs
    from .economy import cost_job
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    lines = ["**🔁 Jobs**", ""]
    jobs = active_jobs(owner_id=uid)
    if not jobs:
        lines.append("_No active jobs._")
    else:
        for j in jobs:
            dur = "∞" if int(j.get("duration", 0) or 0) == 0 else f"{j['duration']}m"
            lines.append(
                f"• `{j['id']}` every {j['interval']}s for {dur} — {j['sent']} sent"
            )
    lines.append("")
    lines.append(f"💸 Cost per job: **{cost_job()} 💎**")
    return "\n".join(lines)


# ===========================================================================
# Text format panel
# ===========================================================================
def textformat_buttons(user_id: int = None) -> List[List]:
    from .text_format import get_settings
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    s = get_settings(uid)

    def mark(k):
        return "✅" if s.get(k) else "⬜"

    return [
        [Button.inline(f"{mark('bold')} Bold", b"tf:bold", style="primary"),
         Button.inline(f"{mark('italic')} Italic", b"tf:italic", style="primary")],
        [Button.inline(f"{mark('underline')} Underline", b"tf:underline",
                       style="primary"),
         Button.inline(f"{mark('strike')} Strike", b"tf:strike", style="primary")],
        [Button.inline(f"{mark('spoiler')} Spoiler", b"tf:spoiler", style="primary")],
        [Button.inline("📋 Chat filter", b"tf:filter", style="primary")],
        [_back_btn(b"nav:main")],
    ]


def textformat_text(user_id: int = None) -> str:
    from .text_format import get_settings, active_list
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    s = get_settings(uid)
    flt = s.get("chats_filter") or "all chats"
    active = active_list(uid)
    status = " · ".join(active) if active else "None"

    return (
        "**✨ Text styling**\n\n"
        "Automatically **re-writes** your sent messages with the styles you enable.\n\n"
        f"**Active:** {status}\n"
        f"**Chats:** `{flt}`\n\n"
        "_Note: edits happen within a second of sending._"
    )


def otp_text(user_id: int = None, code: str = "", ttl: int = 300) -> str:
    return (
        "**🔐 Web panel access**\n\n"
        "Use this **one-time password** to log in to your web panel:\n\n"
        f"`{code}`\n\n"
        f"⏱ Expires in **{ttl // 60} minutes**.\n"
        "Do not share it with anyone."
    )


# ===========================================================================
# Account
# ===========================================================================
def account_buttons() -> List[List]:
    return [
        [Button.inline("💎 Request diamonds", b"acc:reqdiamonds", style="primary")],
        [Button.inline("📜 My requests", b"acc:myreq", style="primary"),
         Button.inline("🤝 Referral", b"acc:referral", style="primary")],
        [Button.inline("🔔 Notifications", b"acc:notify", style="primary")],
        [_back_btn(b"nav:main")],
    ]


def account_text(user_id: int = None) -> str:
    from .economy import cost_clock, cost_job, get_balance
    uid = int(user_id or CONFIG.get("owner_id", 338266658))
    u = get_user(uid) or {}
    bal = get_balance(uid)
    return (
        f"**💎 Account**\n\n"
        f"💎 **Balance:** {bal}\n"
        f"🆔 **ID:** `{uid}`\n"
        f"🤝 **Referral:** `{u.get('referral_code', '—')}`\n\n"
        f"💸 Costs: update {cost_clock()} 💎 · job {cost_job()} 💎"
    )


# ===========================================================================
# Help / Language / Notifications
# ===========================================================================
def help_buttons() -> List[List]:
    return [[_back_btn(b"nav:main")]]


def help_text() -> str:
    return (
        "**❓ Help**\n\n"
        "**Commands:**\n"
        "`/start` — open the panel\n"
        "`/help` — this message\n"
        "`/login` — log in to Telegram\n"
        "`/logout` — log out\n"
        "`/web` — get OTP for web panel\n"
        "`/refer <code>` — apply a referral\n\n"
        "**Features:**\n"
        "• 🟢 Clock — auto-update profile name\n"
        "• 🔁 Jobs — repeat messages\n"
        "• ✨ Text style — auto-format your messages\n"
        "• 🌐 Web Panel — manage from browser"
    )


def language_buttons() -> List[List]:
    return [
        [Button.inline("English", b"set:lang:en", style="primary"),
         Button.inline("فارسی", b"set:lang:fa", style="primary")],
        [_back_btn(b"nav:settings")],
    ]


def language_text() -> str:
    return f"**🗣 Language / زبان**\n\nCurrent: {CONFIG.get('language', 'en')}"


def notify_buttons() -> List[List]:
    owner = int(CONFIG.get("owner_id", 338266658))
    u = get_user(owner) or {}
    notify = u.get("notify") or {}

    def mark(k):
        return "✅" if notify.get(k, True) else "❌"

    return [
        [Button.inline(f"{mark('job_completion')} Job completion",
                       b"notify:job_completion", style="primary")],
        [Button.inline(f"{mark('diamond_changes')} Diamond changes",
                       b"notify:diamond_changes", style="primary")],
        [Button.inline(f"{mark('subscription_expiry')} Subscription expiry",
                       b"notify:subscription_expiry", style="primary")],
        [_back_btn(b"nav:account")],
    ]


def notify_text() -> str:
    return "**🔔 Notifications**"


# ===========================================================================
# Peer picker (used in job flow)
# ===========================================================================
def make_peer_picker_button(button_id: int, label: str = "🎯 Select a group"):
    if not HAS_PEER_PICKER:
        return None
    if KeyboardButtonRequestPeer is None or RequestPeerTypeChat is None:
        return None
    try:
        return KeyboardButtonRequestPeer(
            text=label,
            button_id=button_id,
            peer_type=RequestPeerTypeChat(),
            max_quantity=1,
        )
    except Exception as e:
        try:
            from .logging_setup import log_bot
            log_bot.warning(f"could not construct KeyboardButtonRequestPeer: {e}")
        except Exception:
            pass
        return None