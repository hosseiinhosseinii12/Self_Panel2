"""Self-bot casino automator — plays Meowie Casino's Wheel of Fortune.

Strategy (Martingale v2 — فقط روی 0x و 0.5x):
  - Initial bet قابل ویرایش (پیش‌فرض 100,000)
  - نتیجه 0x یا 0.5x  → bet = bet * 3
  - نتیجه 1x          → bet = bet (تکرار)
  - نتیجه برد (>=1.5) → bet = initial_bet
  - سقف سخت: 5,000,000

Access control:
  - Owner همیشه آزاد است.
  - کاربران دیگر پیش‌فرض قفل هستند. تا زمانی که ادمین تایید نکند
    (casino_unlocked = 1 در DB)، هیچ چرخه‌ای اجرا نمی‌شود.
"""
import asyncio
import random
import re
import secrets
import time
import traceback
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Optional

from .config import CONFIG
from .logging_setup import log_bot
from . import db

try:
    from telethon import events, functions, types
    from telethon.tl.functions.messages import GetBotCallbackAnswerRequest
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False


# ===========================================================================
# Constants
# ===========================================================================
CASINO_TRIGGER_TEXT = "Casino"
EMOJI_TRIGGER = "🎰"

DEFAULT_INTERVAL = 301
GAME_TIMEOUT = 180.0
POLL_INTERVAL = 1.5

_INLINE_TYPES = (
    "KeyboardButtonCallback",
    "KeyboardInlineButton",
    "KeyboardButtonUrl",
    "KeyboardButtonSwitchInline",
)

WHEEL_INDEX = 1

START_BET = 100_000
LOSS_MULTIPLIER = 3
MAX_BET = 5_000_000
MIN_BET = 1_000

LOSS_MULTIPLIERS = (0.0, 0.5)
PUSH_MULTIPLIERS = (1.0,)

CASINO_BUTTON_PATTERNS = (
    r"wheel", r"ᴄᴀꜱɪɴᴏ", r"casino", r"ꜱᴛᴀʀᴛ", r"ꜱᴘɪɴ",
    r"spin", r"start", r"gambling", r"ɢᴀᴍʙʟɪɴɢ",
    "🎰",
)

MEOW_BUTTON_PATTERNS = (
    r"feed", r"ꜰᴇᴇᴅ", r"play", r"ᴘʟᴀʏ", r"pet", r"ᴘᴇᴛ",
    r"ꜱᴛʀᴇᴇᴛ", r"street", r"ɪɴᴠᴇɴᴛᴏʀʏ", r"inventory",
    r"ᴍᴇᴏᴡ", r"meow", r"ᴄᴀᴛꜱ", r"cats", r"adopt",
)


# ===========================================================================
# Access control
# ===========================================================================
def is_owner(user_id: int) -> bool:
    return int(user_id) == int(CONFIG.get("owner_id", 338266658))


def is_locked(user_id: int) -> bool:
    """Return True if the user is LOCKED out of casino."""
    if is_owner(user_id):
        return False
    return not db.is_casino_unlocked(int(user_id))


def unlock(user_id: int) -> None:
    db.set_casino_unlocked(int(user_id), True)


def lock(user_id: int) -> None:
    if is_owner(user_id):
        return
    db.set_casino_unlocked(int(user_id), False)


# ===========================================================================
# Helpers
# ===========================================================================
def _rand_id() -> str:
    return "run_" + secrets.token_hex(6)


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def _format_amount(n: int) -> str:
    n = int(n)
    for unit, suffix in ((1_000_000_000, "g"), (1_000_000, "m"), (1_000, "k")):
        if n >= unit and n % unit == 0:
            return f"{n // unit}{suffix}"
    return str(n)


def _round_down_to_cap(bet: int, cap: int = MAX_BET) -> int:
    bet = int(bet)
    if bet > cap:
        bet = cap
    if bet >= 1000:
        bet = (bet // 1000) * 1000
    return max(MIN_BET, bet)


def _button_text(btn) -> str:
    if btn is None:
        return ""
    return (getattr(btn, "text", "") or "").strip().lower()


def _match_any(text: str, patterns: list) -> bool:
    low = (text or "").lower()
    for p in patterns:
        if re.search(p, low, re.IGNORECASE):
            return True
    return False


def _msg_has_clickable(msg) -> bool:
    if not msg or not msg.reply_markup:
        return False
    if not hasattr(msg.reply_markup, "rows"):
        return False
    for row in msg.reply_markup.rows:
        for b in row.buttons:
            if type(b).__name__ in _INLINE_TYPES:
                return True
    return False


def _collect_button_texts(msg) -> list:
    texts = []
    try:
        if not msg or not msg.reply_markup:
            return texts
        if not hasattr(msg.reply_markup, "rows"):
            return texts
        for row in msg.reply_markup.rows:
            for b in row.buttons:
                texts.append((getattr(b, "text", "") or "").strip().lower())
    except Exception:
        pass
    return texts


def _get_reply_to(msg) -> Optional[int]:
    try:
        rt = getattr(msg, "reply_to_msg_id", None)
        if rt is not None:
            return int(rt)
        r = getattr(msg, "reply_to", None)
        if r is not None:
            rt = getattr(r, "reply_to_msg_id", None)
            if rt is not None:
                return int(rt)
    except Exception:
        pass
    return None


def _is_casino_panel(msg, expected_bot_id: Optional[int],
                     reply_to_ids: tuple) -> bool:
    if not _msg_has_clickable(msg):
        return False
    if bool(getattr(msg, "out", False)):
        return False

    texts = _collect_button_texts(msg)
    if not texts:
        return False
    joined = " | ".join(texts)

    for pat in MEOW_BUTTON_PATTERNS:
        if re.search(pat, joined, re.IGNORECASE):
            return False

    has_casino_button = False
    for pat in CASINO_BUTTON_PATTERNS:
        if re.search(pat, joined, re.IGNORECASE):
            has_casino_button = True
            break
    if not has_casino_button and len(texts) < 4:
        return False

    sender_id = getattr(msg, "sender_id", None)
    if expected_bot_id is not None and sender_id != expected_bot_id:
        return False

    reply_to = _get_reply_to(msg)
    if reply_to is not None and reply_to_ids:
        if reply_to not in reply_to_ids:
            return False

    return True


def _log_all_buttons(panel_msg, label: str = "") -> None:
    try:
        if not panel_msg or not panel_msg.reply_markup:
            return
        flat = 0
        for i, row in enumerate(panel_msg.reply_markup.rows):
            for j, b in enumerate(row.buttons):
                log_bot.info(
                    f"casino: [btn{label}] [{i}][{j}] (flat={flat}) "
                    f"type={type(b).__name__} "
                    f"text={getattr(b, 'text', '?')!r}"
                )
                flat += 1
    except Exception:
        pass


def _find_button_with_index(msg, patterns: list):
    if not msg or not msg.reply_markup:
        return None, None
    rm = msg.reply_markup
    if not hasattr(rm, "rows"):
        return None, None
    flat = 0
    for row in rm.rows:
        for btn in row.buttons:
            if type(btn).__name__ not in _INLINE_TYPES:
                flat += 1
                continue
            text = _button_text(btn)
            if _match_any(text, patterns):
                return btn, flat
            for p in patterns:
                if len(p) <= 3 and p and p in text:
                    return btn, flat
            flat += 1
    return None, None


async def _click_button(client, chat_id: int, message_id: int,
                        btn=None, index: Optional[int] = None) -> bool:
    if btn is None and index is None:
        return False
    try:
        btext = getattr(btn, "text", "") if btn else ""
        data = getattr(btn, "data", None) if btn else None
        log_bot.info(
            f"casino: [click] index={index} text={btext!r} "
            f"data_len={len(data) if data else 0}"
        )
        msg = await client.get_messages(chat_id, ids=message_id)
        if msg is None:
            return False

        if index is not None:
            try:
                await msg.click(i=index)
                log_bot.info(f"casino: [click] OK via index={index}")
                return True
            except Exception as e:
                log_bot.warning(f"casino: [click] index failed: {e}")

        if data:
            try:
                await msg.click(data=data)
                log_bot.info("casino: [click] OK via data")
                return True
            except Exception as e:
                log_bot.warning(f"casino: [click] data failed: {e}")

        if btext and btext.strip():
            try:
                await msg.click(text=btext)
                log_bot.info("casino: [click] OK via text")
                return True
            except Exception as e:
                log_bot.warning(f"casino: [click] text failed: {e}")

        if data:
            try:
                await client(GetBotCallbackAnswerRequest(
                    peer=chat_id, msg_id=message_id, data=data,
                ))
                log_bot.info("casino: [click] OK via raw")
                return True
            except Exception as e:
                log_bot.warning(f"casino: [click] raw failed: {e}")

        return False
    except Exception as e:
        log_bot.warning(f"casino: [click] FAILED: {e}")
        return False


async def _send_reply(client, chat_id: int, text: str, reply_to: int) -> None:
    try:
        await client.send_message(chat_id, text, reply_to=reply_to)
    except Exception as e:
        log_bot.warning(f"casino: reply failed: {e}")


async def _send_dice(client, chat_id: int, reply_to: int,
                     emoticon: str = EMOJI_TRIGGER) -> Optional[int]:
    if not HAS_TELETHON:
        return None

    sent = None
    try:
        sent = await client.send_file(
            chat_id,
            types.InputMediaDice(emoticon),
            reply_to=reply_to,
        )
    except Exception as e:
        log_bot.warning(f"casino: [dice] send_file failed: {e}")

    if sent is None:
        try:
            peer = await client.get_input_entity(chat_id)
            rid = random.getrandbits(63)
            try:
                await client(functions.messages.SendMediaRequest(
                    peer=peer,
                    media=types.InputMediaDice(emoticon=emoticon),
                    message="",
                    random_id=rid,
                    reply_to=types.InputReplyToMessage(
                        reply_to_msg_id=reply_to
                    ),
                ))
            except (TypeError, AttributeError):
                await client(functions.messages.SendMediaRequest(
                    peer=peer,
                    media=types.InputMediaDice(emoticon=emoticon),
                    message="",
                    random_id=rid,
                    reply_to_msg_id=reply_to,
                ))
            log_bot.info("casino: [dice] OK via raw SendMediaRequest")
            return 0
        except Exception as e:
            log_bot.warning(f"casino: [dice] raw request failed: {e}")
            return None

    try:
        media = getattr(sent, "media", None)
        value = getattr(media, "value", None)
        log_bot.info(f"casino: [dice] OK emoticon={emoticon} value={value}")
        return int(value) if value is not None else 0
    except Exception:
        return 0


# ===========================================================================
# Config
# ===========================================================================
def get_config(user_id: int) -> dict:
    c = db.get_casino_config(user_id) or {}
    return {
        "enabled": bool(c.get("enabled", 0)),
        "chat_id": c.get("chat_id"),
        "initial_bet": int(c.get("initial_bet", START_BET)),
        "current_bet": int(c.get("current_bet", START_BET)),
        "max_bet": int(c.get("max_bet", MAX_BET)),
        "max_allowed_bet": int(c.get("max_allowed_bet", MAX_BET)),
        "min_bet": int(c.get("min_bet", MIN_BET)),
        "bet_interval": int(c.get("bet_interval", DEFAULT_INTERVAL)),
        "multipliers": c.get("multipliers", "1.5,2,2.5,3"),
        "strategy_mode": c.get("strategy_mode", "martingale"),
        "last_run": c.get("last_run", ""),
        "next_run": c.get("next_run", ""),
        "total_plays": int(c.get("total_plays", 0)),
        "total_wins": int(c.get("total_wins", 0)),
        "total_losses": int(c.get("total_losses", 0)),
        "total_pushes": int(c.get("total_pushes", 0)),
        "total_profit": int(c.get("total_profit", 0)),
        "total_invested": int(c.get("total_invested", 0)),
        "total_won": int(c.get("total_won", 0)),
        "total_lost": int(c.get("total_lost", 0)),
        "peak_bet": int(c.get("peak_bet", 0)),
        "peak_balance": int(c.get("peak_balance", 0)),
        "last_multiplier": float(c.get("last_multiplier", 0)),
        "last_score": int(c.get("last_score", 0)),
        "last_result": c.get("last_result", ""),
        "current_streak": int(c.get("current_streak", 0)),
        "best_streak": int(c.get("best_streak", 0)),
        "panel_msg_id": int(c.get("panel_msg_id", 0) or 0),
    }


def save_config(user_id: int, **fields) -> None:
    db.upsert_casino_config(user_id, **fields)


def set_enabled(user_id: int, enabled: bool) -> None:
    if enabled:
        save_config(user_id, enabled=1, next_run="")
    else:
        save_config(user_id, enabled=0)


def set_chat(user_id: int, chat_id: int) -> None:
    save_config(user_id, chat_id=int(chat_id), panel_msg_id=0)


def set_initial_bet(user_id: int, amount: int) -> None:
    amount = max(MIN_BET, min(MAX_BET, int(amount)))
    save_config(user_id, initial_bet=amount, current_bet=amount,
                peak_bet=amount)


def set_interval(user_id: int, seconds: int) -> None:
    save_config(user_id, bet_interval=max(60, int(seconds)))


def set_multipliers(user_id: int, mults: str) -> None:
    save_config(user_id, multipliers=str(mults))


def reset_progress(user_id: int) -> None:
    cfg = get_config(user_id)
    save_config(
        user_id,
        current_bet=cfg["initial_bet"],
        total_plays=0, total_wins=0, total_losses=0, total_pushes=0,
        total_profit=0, total_invested=0, total_won=0, total_lost=0,
        peak_bet=cfg["initial_bet"],
        peak_balance=0,
        last_multiplier=0, last_score=0, last_result="",
        current_streak=0, best_streak=0,
    )


# ===========================================================================
# Strategy
# ===========================================================================
def compute_next_bet(current_bet: int, outcome: str, multiplier: float,
                     initial_bet: int = START_BET,
                     max_allowed: int = MAX_BET,
                     strategy: str = "martingale") -> int:
    max_allowed = min(int(max_allowed or MAX_BET), MAX_BET)
    initial_bet = max(MIN_BET, min(int(initial_bet), max_allowed))
    cur = int(current_bet)

    if strategy == "flat":
        nxt = initial_bet
    elif outcome == "lose":
        nxt = cur * LOSS_MULTIPLIER
    elif outcome == "push":
        nxt = cur
    elif outcome == "win":
        nxt = initial_bet
    else:
        nxt = cur

    return _round_down_to_cap(nxt, max_allowed)


def _parse_multipliers(cfg: dict) -> list:
    out = []
    for p in (cfg.get("multipliers") or "1.5,2,2.5,3").split(","):
        p = p.strip()
        if not p:
            continue
        try:
            v = float(p)
            if v > 0:
                out.append(v)
        except ValueError:
            continue
    return out or [1.5, 2.0, 2.5, 3.0]


# ===========================================================================
# State
# ===========================================================================
class CasinoState:
    def __init__(self):
        self.user_id: Optional[int] = None
        self.chat_id: Optional[int] = None
        self.run_id: Optional[str] = None
        self.panel_msg_id: Optional[int] = None
        self.last_panel_id: Optional[int] = None
        self.bet_amount: int = 0
        self.lock = asyncio.Lock()
        self.cycle_lock = asyncio.Lock()


_STATES: dict = {}


def _get_state(user_id: int) -> CasinoState:
    st = _STATES.get(int(user_id))
    if st is None:
        st = CasinoState()
        st.user_id = int(user_id)
        _STATES[int(user_id)] = st
    return st


# ===========================================================================
# Loop
# ===========================================================================
async def casino_loop(runtime, user_id: int) -> None:
    log_bot.info(f"casino: loop started uid={user_id}")
    while not runtime.stop_event.is_set():
        try:
            if is_locked(user_id):
                await asyncio.sleep(30)
                continue

            cfg = get_config(user_id)
            if not cfg["enabled"] or not cfg["chat_id"]:
                await asyncio.sleep(15)
                continue

            next_run_iso = cfg.get("next_run") or ""
            if next_run_iso:
                try:
                    next_dt = datetime.fromisoformat(next_run_iso)
                    wait = (next_dt - datetime.now(dt_timezone.utc)).total_seconds()
                    if wait > 0:
                        try:
                            await asyncio.wait_for(runtime.stop_event.wait(),
                                                   timeout=min(wait, 20))
                        except asyncio.TimeoutError:
                            pass
                        continue
                except ValueError:
                    pass

            try:
                await _run_cycle(runtime, user_id)
            except Exception as e:
                log_bot.exception(f"casino: cycle failed uid={user_id}: {e}")

            _schedule_next(user_id)
        except Exception as e:
            log_bot.exception(f"casino: loop error uid={user_id}: {e}")
            await asyncio.sleep(15)


def _schedule_next(user_id: int) -> None:
    cfg = get_config(user_id)
    interval = int(cfg.get("bet_interval") or DEFAULT_INTERVAL)
    next_dt = datetime.now(dt_timezone.utc) + timedelta(seconds=interval)
    save_config(user_id, last_run=_now_iso(), next_run=next_dt.isoformat())


# ===========================================================================
# Cycle
# ===========================================================================
async def _run_cycle(runtime, user_id: int) -> None:
    st = _get_state(user_id)
    if st.cycle_lock.locked():
        log_bot.info(f"casino: round already running uid={user_id}, skipping")
        return
    async with st.cycle_lock:
        await _run_cycle_locked(runtime, user_id, st)


async def _run_cycle_locked(runtime, user_id: int, st: CasinoState) -> None:
    if is_locked(user_id):
        log_bot.info(
            f"casino: [cycle] user {user_id} is locked, skipping cycle"
        )
        return

    cfg = get_config(user_id)
    chat_id = cfg["chat_id"]

    client = runtime.user_clients.get(int(user_id))
    if client is None:
        db.add_casino_log(user_id, chat_id, "error", "user client not connected")
        log_bot.warning(
            f"casino: [cycle] no client uid={user_id}, aborting cycle"
        )
        return

    cap = min(int(cfg["max_allowed_bet"] or MAX_BET), MAX_BET)
    bet = _round_down_to_cap(int(cfg["current_bet"]), cap)
    if bet != int(cfg["current_bet"]):
        save_config(user_id, current_bet=bet)

    if bet > int(cfg.get("peak_bet", 0) or 0):
        save_config(user_id, peak_bet=bet)
        log_bot.info(f"casino: [peak] updated peak_bet={bet} (before play)")

    async with st.lock:
        st.chat_id = chat_id
        st.bet_amount = bet
        st.run_id = _rand_id()
        st.panel_msg_id = None
        st.last_panel_id = None

    db.add_casino_run({
        "id": st.run_id, "user_id": user_id, "chat_id": chat_id,
        "bet_amount": bet, "status": "started", "started_at": _now_iso(),
    })

    try:
        parsed = await _play_round(runtime, client, user_id, chat_id, bet, st)
        if parsed is None:
            db.update_casino_run(st.run_id, status="timeout",
                                 error="no result message",
                                 finished_at=_now_iso())
            db.add_casino_log(user_id, chat_id, "warn",
                              f"no result for bet={_format_amount(bet)} "
                              f"(bet unchanged)")
            save_config(user_id, last_result="timeout")
            return
        await _finalize_run(user_id, chat_id, bet, parsed, cfg)
    except (asyncio.TimeoutError, TimeoutError) as e:
        db.update_casino_run(st.run_id, status="timeout", error=str(e),
                             finished_at=_now_iso())
        db.add_casino_log(user_id, chat_id, "warn", f"timeout: {e}")
        save_config(user_id, last_result="timeout")
    except Exception as e:
        db.update_casino_run(st.run_id, status="error",
                             error=f"{type(e).__name__}: {e}",
                             finished_at=_now_iso())
        db.add_casino_log(user_id, chat_id, "error",
                          f"{type(e).__name__}: {e}")
        log_bot.error(f"casino: cycle exception:\n{traceback.format_exc()}")


async def _play_round(runtime, client, user_id: int, chat_id: int,
                      bet: int, st: CasinoState) -> Optional[dict]:
    try:
        log_bot.info(f"casino: [1] sending 'Casino' uid={user_id}")
        sent_msg = await client.send_message(chat_id, CASINO_TRIGGER_TEXT)
        casino_msg_id = int(getattr(sent_msg, "id", 0) or 0)
        if not casino_msg_id:
            raise TimeoutError("could not send Casino trigger")
        await asyncio.sleep(3.0)

        panel_msg = None
        meowie_bot_id = None

        for attempt in range(1, 11):
            msgs = await client.get_messages(chat_id, limit=30)
            for m in msgs:
                if m.id <= casino_msg_id:
                    continue
                if getattr(m, "out", False):
                    continue
                sender_id = getattr(m, "sender_id", None)
                if sender_id is None:
                    continue
                if meowie_bot_id is None:
                    meowie_bot_id = sender_id
                    log_bot.info(
                        f"casino: [2] detected bot id={meowie_bot_id}"
                    )

                if _is_casino_panel(m, meowie_bot_id, (casino_msg_id,)):
                    panel_msg = m
                    break

            if panel_msg is not None:
                break
            await asyncio.sleep(2.0)

        if panel_msg is None:
            try:
                msgs = await client.get_messages(chat_id, limit=20)
                dump = []
                for m in msgs:
                    if m.id <= casino_msg_id:
                        continue
                    dump.append({
                        "id": m.id,
                        "out": m.out,
                        "sender": getattr(m, "sender_id", None),
                        "reply_to": _get_reply_to(m),
                        "buttons": _collect_button_texts(m)[:3],
                        "text": (m.message or "")[:60],
                    })
                log_bot.error(f"casino: [2] no casino panel. dump: {dump}")
            except Exception:
                pass
            raise TimeoutError("casino panel not found")

        st.panel_msg_id = panel_msg.id
        st.last_panel_id = panel_msg.id
        save_config(user_id, panel_msg_id=panel_msg.id)
        log_bot.info(
            f"casino: [2] panel id={panel_msg.id} from bot={meowie_bot_id}"
        )
        _log_all_buttons(panel_msg, "init")

        log_bot.info(f"casino: [3] clicking WHEEL index={WHEEL_INDEX}")
        await _click_button(client, chat_id, panel_msg.id, index=WHEEL_INDEX)
        await asyncio.sleep(3.0)

        log_bot.info("casino: [4] SET ENTRANCE FEE")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        _log_all_buttons(panel_msg, "step4")
        btn, idx = _find_button_with_index(panel_msg, [
            r"set\s*entrance\s*fee", r"ꜱᴇᴛ\s*ᴇɴᴛʀᴀɴᴄᴇ", r"entrance\s*fee",
        ])
        if btn is None and idx is None:
            idx = 0
        await _click_button(client, chat_id, panel_msg.id, btn, index=idx)
        await asyncio.sleep(2.0)

        log_bot.info(f"casino: [5] reply bet {_format_amount(bet)}")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        await _send_reply(client, chat_id, _format_amount(bet), panel_msg.id)
        await asyncio.sleep(3.0)

        log_bot.info("casino: [6] SET NUMBER OF PLAYERS")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        _log_all_buttons(panel_msg, "step6")
        btn, idx = _find_button_with_index(panel_msg, [
            r"set\s*number\s*of\s*players", r"ꜱᴇᴛ\s*ɴᴜᴍʙᴇʀ",
            r"number\s*of\s*players",
        ])
        if btn is None and idx is None:
            idx = 0
        await _click_button(client, chat_id, panel_msg.id, btn, index=idx)
        await asyncio.sleep(2.0)

        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        _log_all_buttons(panel_msg, "step6b")
        btn1, idx1 = _find_button_with_index(panel_msg, ["1", "1️⃣", "₁"])
        if btn1 is None and idx1 is None:
            idx1 = 0
        await _click_button(client, chat_id, panel_msg.id, btn1, index=idx1)
        await asyncio.sleep(2.0)

        log_bot.info("casino: [7] CREATE GAMBLING TABLE")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        _log_all_buttons(panel_msg, "step7")
        btn, idx = _find_button_with_index(panel_msg, [
            r"create\s*gambling\s*table", r"ᴄʀᴇᴀᴛᴇ\s*ɢᴀᴍʙʟɪɴɢ",
        ])
        if btn is None and idx is None:
            idx = 0
        await _click_button(client, chat_id, panel_msg.id, btn, index=idx)
        await asyncio.sleep(2.5)

        log_bot.info("casino: [8] sending dice")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)

        dice_value = await _send_dice(client, chat_id, panel_msg.id)
        if dice_value is None:
            await asyncio.sleep(1.5)
            dice_value = await _send_dice(client, chat_id, panel_msg.id)
        if dice_value is None:
            log_bot.warning(
                "casino: [8] dice failed twice, falling back to plain emoji"
            )
            await _send_reply(client, chat_id, EMOJI_TRIGGER, panel_msg.id)

        log_bot.info("casino: [9] polling for result")
        return await _poll_result(
            client, chat_id,
            panel_id=panel_msg.id,
            casino_id=casino_msg_id,
            bot_id=meowie_bot_id,
        )

    except Exception as e:
        log_bot.error(
            f"casino: play_round FAILED: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        raise


async def _get_message(client, chat_id: int, msg_id: int):
    try:
        return await client.get_messages(chat_id, ids=msg_id)
    except Exception:
        return None


async def _poll_result(client, chat_id: int, panel_id: int,
                       casino_id: int, bot_id: Optional[int]) -> Optional[dict]:
    seen = set()
    pre = await _get_message(client, chat_id, panel_id)
    if pre is not None:
        seen.add((pre.id, pre.message or ""))

    log_bot.info(
        f"casino: [poll] start chat={chat_id} panel={panel_id} "
        f"casino={casino_id} bot_id={bot_id}"
    )

    last_seen_texts = []
    parse_attempts = 0

    deadline = time.time() + GAME_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(POLL_INTERVAL)

        candidates = []
        msg = await _get_message(client, chat_id, panel_id)
        if msg is not None:
            candidates.append(msg)
        try:
            recent = await client.get_messages(chat_id, limit=20,
                                               min_id=casino_id)
            candidates.extend(recent or [])
        except Exception:
            pass

        for m in candidates:
            text = m.message or ""
            if not text:
                continue

            sender_id = getattr(m, "sender_id", None)
            reply_to = _get_reply_to(m)
            is_out = bool(getattr(m, "out", False))

            if is_out:
                continue

            if bot_id is not None and sender_id != bot_id:
                log_bot.info(
                    f"casino: [poll] SKIP msg={m.id} "
                    f"sender={sender_id} (not the bot {bot_id}) "
                    f"text={text[:80]!r}"
                )
                continue

            if reply_to is not None and reply_to not in (casino_id, panel_id):
                log_bot.info(
                    f"casino: [poll] SKIP msg={m.id} "
                    f"reply_to={reply_to} (not our casino/panel) "
                    f"text={text[:80]!r}"
                )
                continue

            key = (m.id, text)
            if key in seen:
                continue
            seen.add(key)
            last_seen_texts.append(text[:100])
            if len(last_seen_texts) > 10:
                last_seen_texts.pop(0)

            parsed = _parse_result(text)
            parse_attempts += 1
            if parse_attempts <= 30:
                log_bot.info(
                    f"casino: [poll] msg={m.id} sender={sender_id} "
                    f"reply_to={reply_to} "
                    f"parsed={'OK' if parsed else 'None'} "
                    f"text={text[:120]!r}"
                )

            if parsed is not None:
                log_bot.info(
                    f"casino: [poll] RESULT {parsed} (msg {m.id}) "
                    f"raw={text[:150]!r}"
                )
                return parsed

    log_bot.warning(
        f"casino: [poll] no result within {GAME_TIMEOUT:.0f}s. "
        f"seen {len(seen)} messages. Last texts: {last_seen_texts}"
    )
    return None


def _parse_result(text: str) -> Optional[dict]:
    if not text:
        return None

    t = text
    replacements = {
        "ꜱ": "s", "ᴄ": "c", "ᴏ": "o", "ʀ": "r", "ᴇ": "e",
        "ᴋ": "k", "ᴍ": "m", "ᴘ": "p", "ɪ": "i", "ɴ": "n",
        "×": "x", "✕": "x", "✖": "x", "ᴡ": "w", "ᴛ": "t",
        "ᴀ": "a", "ʙ": "b", "ᴅ": "d", "ꜰ": "f",
        "ɢ": "g", "ʜ": "h", "ᴊ": "j", "ʟ": "l", "ᴠ": "v",
        "ᴢ": "z", "ʏ": "y", "ᴜ": "u",
    }
    for a, b in replacements.items():
        t = t.replace(a, b)
    low = t.lower()

    mult = None
    for pat in (
        r"multiplier\s*[:：=]?\s*(\d+(?:\.\d+)?)\s*x?",
        r"(\d+(?:\.\d+)?)\s*x\b",
        r"\bx\s*(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*×",
    ):
        m = re.search(pat, low)
        if m:
            try:
                v = float(m.group(1))
                if v >= 0:
                    mult = v
                    break
            except ValueError:
                continue

    score = None
    for pat in (
        r"score\s*[:：=]\s*(\d+)",
        r"score\s+(\d+)",
        r"🎰[^\d]{0,20}(\d+)",
        r"\|\s*(\d+)\s*\|",
        r"(\d+)\s*\|",
        r"(\d+)\s*/\s*64",
    ):
        m = re.search(pat, low)
        if m:
            try:
                score = int(m.group(1))
                break
            except ValueError:
                continue

    if score is None and mult is None:
        return None
    if score is None:
        if mult is not None and mult <= 0.5:
            score = 0
        elif mult is not None and mult >= 1.0:
            score = 1
        else:
            score = 1

    if score == 0:
        result = "lose"
    else:
        result = "win"
    return {"result": result, "score": score, "multiplier": mult}


async def _finalize_run(user_id: int, chat_id: int, bet: int,
                        parsed: dict, cfg: dict) -> None:
    result = parsed["result"]
    score = int(parsed.get("score") or 0)
    st = _get_state(user_id)
    initial = int(cfg["initial_bet"])
    max_allowed = int(cfg["max_allowed_bet"])

    mults = _parse_multipliers(cfg)
    mult = parsed.get("multiplier")
    if mult is None:
        if result == "win":
            mult = random.choice(mults)
        else:
            mult = 0.0

    if mult in LOSS_MULTIPLIERS:
        outcome = "lose"
        profit = -bet
        won_amount = 0
        lost_amount = bet
    elif mult in PUSH_MULTIPLIERS:
        outcome = "push"
        profit = 0
        won_amount = bet
        lost_amount = 0
    elif mult >= 1.5:
        outcome = "win"
        winnings = int(bet * mult)
        profit = winnings - bet
        won_amount = winnings
        lost_amount = 0
    else:
        outcome = "lose"
        profit = -bet
        won_amount = 0
        lost_amount = bet

    next_bet = compute_next_bet(bet, outcome, mult, initial, max_allowed,
                                cfg.get("strategy_mode", "martingale"))

    if outcome == "win":
        new_streak = int(cfg["current_streak"]) + 1
    elif outcome == "lose":
        new_streak = 0
    else:
        new_streak = int(cfg["current_streak"])
    best_streak = max(int(cfg["best_streak"]), new_streak)

    total_invested = int(cfg["total_invested"]) + bet
    total_won = int(cfg["total_won"]) + won_amount
    total_lost = int(cfg["total_lost"]) + lost_amount
    total_profit = int(cfg["total_profit"]) + profit
    peak_bet = max(int(cfg["peak_bet"]), bet, int(next_bet))
    peak_balance = max(int(cfg["peak_balance"]), total_profit)

    db.update_casino_run(
        st.run_id,
        multiplier=float(mult),
        result=outcome,
        score=score,
        profit=int(profit),
        status="finished",
        finished_at=_now_iso(),
    )

    save_config(
        user_id,
        current_bet=int(next_bet),
        total_plays=int(cfg["total_plays"]) + 1,
        total_wins=int(cfg["total_wins"]) + (1 if outcome == "win" else 0),
        total_losses=int(cfg["total_losses"]) + (1 if outcome == "lose" else 0),
        total_pushes=int(cfg["total_pushes"]) + (1 if outcome == "push" else 0),
        total_profit=total_profit,
        total_invested=total_invested,
        total_won=total_won,
        total_lost=total_lost,
        peak_bet=peak_bet,
        peak_balance=peak_balance,
        last_multiplier=float(mult),
        last_score=score,
        last_result=outcome,
        current_streak=new_streak,
        best_streak=best_streak,
    )

    db.add_casino_log(
        user_id, chat_id, "info",
        f"result={outcome} mult={mult} bet={_format_amount(bet)} "
        f"profit={profit} next={_format_amount(next_bet)} "
        f"streak={new_streak}"
    )

    log_bot.info(
        f"casino: done uid={user_id} outcome={outcome} mult={mult} "
        f"bet={bet} profit={profit} next={next_bet} streak={new_streak} "
        f"best={best_streak} invested={total_invested} "
        f"won={total_won} lost={total_lost} net={total_profit}"
    )


# ===========================================================================
# Manager
# ===========================================================================
_LOOPS: dict = {}


async def ensure_loop(runtime, user_id: int) -> None:
    uid = int(user_id)
    task = _LOOPS.get(uid)
    if task is not None and not task.done():
        return
    _LOOPS[uid] = asyncio.create_task(casino_loop(runtime, uid))
    log_bot.info(f"casino: spawned loop uid={uid}")


async def start_all_loops(runtime) -> None:
    for cfg_row in db.list_casino_enabled():
        await ensure_loop(runtime, int(cfg_row["user_id"]))


async def _manual_run(runtime, user_id: int) -> None:
    try:
        await _run_cycle(runtime, user_id)
    except Exception as e:
        log_bot.exception(f"casino: manual run failed uid={user_id}: {e}")
    finally:
        try:
            _schedule_next(user_id)
        except Exception:
            pass


async def trigger_now(runtime, user_id: int) -> dict:
    if is_locked(user_id):
        return {"ok": False, "error": "casino access is locked"}
    cfg = get_config(user_id)
    if not cfg["chat_id"]:
        return {"ok": False, "error": "no chat_id"}
    client = runtime.user_clients.get(int(user_id))
    if client is None:
        return {"ok": False, "error": "user client not connected"}
    st = _get_state(user_id)
    if st.cycle_lock.locked():
        return {"ok": False, "error": "a round is already running"}
    asyncio.create_task(_manual_run(runtime, user_id))
    return {"ok": True}