"""Self-bot casino automator — plays Meowie Casino's Wheel of Fortune.

Strategy (Martingale v2 — فقط روی 0x و 0.5x):
  - Initial bet قابل ویرایش (پیش‌فرض 100,000)
  - نتیجه 0x یا 0.5x  → bet = bet * 3
  - نتیجه 1x          → bet = bet (تکرار)
  - نتیجه برد (>=1.5) → bet = initial_bet
  - سقف سخت: 5,000,000 — هر عدد بالاتر به 5M گرد می‌شود (round DOWN)
  - هر شرط باید REPLY به پیام پنل Meowie باشد

ترتیب ارسال:
  - اگر زمان «میوهام» رسیده باشد (یا last_meow خالی/قدیمی باشد):
    «میوهام» → ۱۰ ثانیه صبر → «Casino»
  - در غیر این صورت مستقیماً «Casino»

Profile (از پاسخ پنل Meowie پارس می‌شود):
  - Meow Points  → meow_points
  - Level        → level
  - Level EXP    → exp_current / exp_needed
  - Street Cats  → street_cats

همه در casino_config ذخیره و در پنل وب نمایش داده می‌شود.
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
MEOW_CALL_TEXT = "میوهام"

DEFAULT_INTERVAL = 301
MEOW_INTERVAL = 600              # هر 10 دقیقه یک بار «میوهام»
MEOW_FORCE_AFTER = 3600          # اگر > 1 ساعت گذشته، حتماً بفرست
MEOW_TO_CASINO_DELAY = 10.0      # صبر بعد از «میوهام» قبل از «Casino»
GAME_TIMEOUT = 120.0
POLL_INTERVAL = 1.5

_INLINE_TYPES = (
    "KeyboardButtonCallback",
    "KeyboardInlineButton",
    "KeyboardButtonUrl",
    "KeyboardButtonSwitchInline",
)

WHEEL_INDEX = 1

# Strategy constants
START_BET = 100_000
LOSS_MULTIPLIER = 3
MAX_BET = 5_000_000
MIN_BET = 1_000

# Classification
LOSS_MULTIPLIERS = (0.0, 0.5)
PUSH_MULTIPLIERS = (1.0,)


# ===========================================================================
# Helpers
# ===========================================================================
def _rand_id() -> str:
    return "run_" + secrets.token_hex(6)


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def _format_amount(n: int) -> str:
    """Exact amount for the game bot (2,700,000 -> '2700k', NOT '2.7m')."""
    n = int(n)
    for unit, suffix in ((1_000_000_000, "g"), (1_000_000, "m"), (1_000, "k")):
        if n >= unit and n % unit == 0:
            return f"{n // unit}{suffix}"
    return str(n)


def _round_down_to_cap(bet: int, cap: int = MAX_BET) -> int:
    """Round bet DOWN to a multiple of 1000, never above cap."""
    bet = int(bet)
    if bet > cap:
        bet = cap
    if bet >= 1000:
        bet = (bet // 1000) * 1000
    return max(MIN_BET, bet)


def _parse_human_number(s: str) -> Optional[float]:
    """Parse '23.88ᴍ' -> 23_880_000, '2.75ᴋ' -> 2750, '5.99ᴋ' -> 5990."""
    if not s:
        return None
    s = s.strip().lower()
    mult = 1.0
    if s.endswith("ᴍ") or s.endswith("m"):
        mult = 1_000_000.0
        s = s[:-1]
    elif s.endswith("ᴋ") or s.endswith("k"):
        mult = 1_000.0
        s = s[:-1]
    elif s.endswith("ɢ") or s.endswith("g") or s.endswith("ʙ") or s.endswith("b"):
        mult = 1_000_000_000.0
        s = s[:-1]
    try:
        return float(s.replace(",", "").strip()) * mult
    except ValueError:
        return None


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
    """Send a real Telegram dice (animated 🎰) as a reply to the panel."""
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
        "last_meow": c.get("last_meow", ""),
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
        # Meowie profile
        "meow_points": int(c.get("meow_points", 0) or 0),
        "level": int(c.get("level", 0) or 0),
        "exp_current": int(c.get("exp_current", 0) or 0),
        "exp_needed": int(c.get("exp_needed", 0) or 0),
        "street_cats": int(c.get("street_cats", 0) or 0),
        "profile_updated": c.get("profile_updated", ""),
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
# Strategy Engine
# ===========================================================================
def compute_next_bet(current_bet: int, outcome: str, multiplier: float,
                     initial_bet: int = START_BET,
                     max_allowed: int = MAX_BET,
                     strategy: str = "martingale") -> int:
    """استراتژی:
      - outcome == 'lose' و mult در {0, 0.5}  → current * 3
      - outcome == 'push' و mult == 1.0       → current (تکرار)
      - outcome == 'win'  و mult >= 1.5       → initial_bet
      سقف 5M، گرد به پایین.
    """
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
# Profile parser
# ===========================================================================
_PROFILE_RE = re.compile(
    r"ᴍᴇᴏᴡ\s*ᴘᴏɪɴᴛꜱ\s*[:：]\s*([0-9.,]+\s*[ᴍᴋɢʙmkbg]?)",
    re.IGNORECASE,
)
_LEVEL_RE = re.compile(r"ʟᴇᴠᴇʟ\s*[:：]\s*(\d+)", re.IGNORECASE)
_EXP_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*[ᴋᴍkm]?)\s*/\s*(\d+(?:\.\d+)?\s*[ᴋᴍkm]?)",
    re.IGNORECASE,
)
_CATS_RE = re.compile(r"ꜱᴛʀᴇᴇᴛ\s*ᴄᴀᴛꜱ\s*[:：]\s*(\d+)", re.IGNORECASE)


def parse_profile(text: str) -> Optional[dict]:
    """Parse a Meowie Profile reply. Returns dict or None."""
    if not text:
        return None
    out = {}

    m = _PROFILE_RE.search(text)
    if m:
        pts = _parse_human_number(m.group(1))
        if pts is not None:
            out["meow_points"] = int(pts)

    m = _LEVEL_RE.search(text)
    if m:
        try:
            out["level"] = int(m.group(1))
        except ValueError:
            pass

    m = _EXP_RE.search(text)
    if m:
        cur = _parse_human_number(m.group(1))
        need = _parse_human_number(m.group(2))
        if cur is not None:
            out["exp_current"] = int(cur)
        if need is not None:
            out["exp_needed"] = int(need)

    m = _CATS_RE.search(text)
    if m:
        try:
            out["street_cats"] = int(m.group(1))
        except ValueError:
            pass

    return out or None


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
        self.listener_installed: bool = False


_STATES: dict = {}


def _get_state(user_id: int) -> CasinoState:
    st = _STATES.get(int(user_id))
    if st is None:
        st = CasinoState()
        st.user_id = int(user_id)
        _STATES[int(user_id)] = st
    return st


# ===========================================================================
# Listener
# ===========================================================================
def install_casino_listener(runtime, user_id: int, client) -> None:
    if client is None:
        return
    if getattr(client, "_casino_listener_installed", False):
        return

    st = _get_state(user_id)

    @client.on(events.NewMessage(incoming=True))
    async def _on_new(event):
        try:
            text = event.raw_text or ""
            _try_parse_profile(text)
            if not _msg_has_clickable(event):
                return
            if not event.is_private and not event.is_group:
                return
            if st.chat_id and int(event.chat_id or 0) != int(st.chat_id):
                return
            st.last_panel_id = event.id
            try:
                save_config(user_id, panel_msg_id=event.id)
            except Exception:
                pass
        except Exception:
            pass

    @client.on(events.MessageEdited(incoming=True))
    async def _on_edit(event):
        try:
            text = event.raw_text or ""
            _try_parse_profile(text)
            if not _msg_has_clickable(event):
                return
            if not event.is_private and not event.is_group:
                return
            if st.chat_id and int(event.chat_id or 0) != int(st.chat_id):
                return
            st.last_panel_id = event.id
            try:
                save_config(user_id, panel_msg_id=event.id)
            except Exception:
                pass
        except Exception:
            pass

    client._casino_listener_installed = True
    st.listener_installed = True
    log_bot.info(f"casino: listener installed uid={user_id}")


def _try_parse_profile(text: str) -> None:
    """Parse a Meowie Profile block from any text and persist it."""
    if not text:
        return
    low = text.lower()
    if "ᴍᴇᴏᴡ" not in text and "meow" not in low:
        return
    parsed = parse_profile(text)
    if not parsed:
        return
    try:
        owner_id = int(CONFIG.get("owner_id", 338266658))
        parsed["profile_updated"] = _now_iso()
        save_config(owner_id, **parsed)
        log_bot.info(f"casino: profile updated {parsed}")
    except Exception as e:
        log_bot.warning(f"casino: profile save failed: {e}")


# ===========================================================================
# Loop
# ===========================================================================
async def casino_loop(runtime, user_id: int) -> None:
    log_bot.info(f"casino: loop started uid={user_id}")
    while not runtime.stop_event.is_set():
        try:
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


async def _maybe_send_meow(runtime, user_id: int, cfg: dict,
                            force: bool = False) -> bool:
    """اگر زمان «میوهام» رسیده باشد (یا force=True)، آن را await کن.

    True یعنی «میوهام» همین الان ارسال شد و caller باید
    MEOW_TO_CASINO_DELAY ثانیه صبر کند قبل از «Casino».
    False یعنی ارسال نشد (زمانش نرسیده، client نیست، یا خطا داشت).
    """
    chat_id = cfg.get("chat_id")
    if not chat_id:
        return False

    if not force:
        last = cfg.get("last_meow") or ""
        now = datetime.now(dt_timezone.utc)
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if (now - last_dt).total_seconds() < MEOW_INTERVAL:
                    return False
            except ValueError:
                pass

    client = runtime.user_clients.get(int(user_id))
    if client is None:
        log_bot.warning(f"casino: meow skipped — no client uid={user_id}")
        return False

    try:
        await client.send_message(int(chat_id), MEOW_CALL_TEXT)
        save_config(user_id, last_meow=_now_iso())
        log_bot.info(f"casino: sent 'میوهام' uid={user_id} (force={force})")
        return True
    except Exception as e:
        log_bot.warning(f"casino: meow send failed: {e}")
        return False


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
    cfg = get_config(user_id)
    chat_id = cfg["chat_id"]

    client = runtime.user_clients.get(int(user_id))
    if client is None:
        db.add_casino_log(user_id, chat_id, "error", "user client not connected")
        return

    try:
        install_casino_listener(runtime, user_id, client)
    except Exception:
        pass

    # -------------------------------------------------------------------
    # ۱) اگر زمان «میوهام» رسیده (یا last_meow خالی/قدیمی)، اول آن را
    #    بزن، ۱۰ ثانیه صبر کن، بعد کازینو را شروع کن.
    # -------------------------------------------------------------------
    last_meow = cfg.get("last_meow") or ""
    force_meow = False
    if not last_meow:
        force_meow = True
    else:
        try:
            last_dt = datetime.fromisoformat(last_meow)
            if (datetime.now(dt_timezone.utc) - last_dt).total_seconds() > MEOW_FORCE_AFTER:
                force_meow = True
        except ValueError:
            force_meow = True

    meow_sent = False
    try:
        meow_sent = await _maybe_send_meow(runtime, user_id, cfg,
                                            force=force_meow)
    except Exception as e:
        log_bot.warning(f"casino: meow before cycle failed: {e}")

    if meow_sent:
        log_bot.info(
            f"casino: 'میوهام' sent (force={force_meow}), waiting "
            f"{MEOW_TO_CASINO_DELAY:.0f}s before 'Casino' uid={user_id}"
        )
        try:
            await asyncio.wait_for(
                runtime.stop_event.wait(),
                timeout=MEOW_TO_CASINO_DELAY,
            )
        except asyncio.TimeoutError:
            pass
        # cfg ممکن است در این ۱۰ ثانیه عوض شده باشد
        cfg = get_config(user_id)
        chat_id = cfg["chat_id"]

    # -------------------------------------------------------------------
    # ۲) حالا کازینو
    # -------------------------------------------------------------------
    cap = min(int(cfg["max_allowed_bet"] or MAX_BET), MAX_BET)

    bet = _round_down_to_cap(int(cfg["current_bet"]), cap)
    if bet != int(cfg["current_bet"]):
        save_config(user_id, current_bet=bet)

    async with st.lock:
        st.chat_id = chat_id
        st.bet_amount = bet
        st.run_id = _rand_id()
        st.panel_msg_id = None
        if cfg.get("panel_msg_id"):
            st.last_panel_id = int(cfg["panel_msg_id"])

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
        base_id = int(getattr(sent_msg, "id", 0) or 0)
        await asyncio.sleep(3.0)

        panel_msg = None
        for attempt in range(1, 11):
            msgs = await client.get_messages(chat_id, limit=30)
            for m in msgs:
                if m.id > base_id and not m.out and _msg_has_clickable(m):
                    panel_msg = m
                    break
            if panel_msg is not None:
                break
            await asyncio.sleep(2.0)

        if panel_msg is None and st.last_panel_id:
            cached = await _get_message(client, chat_id, st.last_panel_id)
            if _msg_has_clickable(cached):
                log_bot.warning("casino: [2] no new panel, using cached one")
                panel_msg = cached

        if panel_msg is None:
            raise TimeoutError("panel not found")

        st.panel_msg_id = panel_msg.id
        st.last_panel_id = panel_msg.id
        save_config(user_id, panel_msg_id=panel_msg.id)
        log_bot.info(f"casino: [2] panel id={panel_msg.id}")
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

        log_bot.info("casino: [8] sending 🎰 dice")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)

        dice_value = await _send_dice(client, chat_id, panel_msg.id)
        if dice_value is None:
            await asyncio.sleep(1.5)
            dice_value = await _send_dice(client, chat_id, panel_msg.id)
        if dice_value is None:
            log_bot.warning(
                "casino: [8] dice failed twice, falling back to plain 🎰"
            )
            await _send_reply(client, chat_id, EMOJI_TRIGGER, panel_msg.id)

        log_bot.info("casino: [9] polling for result")
        return await _poll_result(client, chat_id, panel_msg.id, base_id)

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
                       after_id: int) -> Optional[dict]:
    seen = set()
    pre = await _get_message(client, chat_id, panel_id)
    if pre is not None:
        seen.add((pre.id, pre.message or ""))

    last_seen_texts = []
    deadline = time.time() + GAME_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(POLL_INTERVAL)

        candidates = []
        msg = await _get_message(client, chat_id, panel_id)
        if msg is not None:
            candidates.append(msg)
        try:
            recent = await client.get_messages(chat_id, limit=20,
                                               min_id=after_id)
            candidates.extend(recent or [])
        except Exception:
            pass

        for m in candidates:
            text = m.message or ""
            if not text:
                continue
            key = (m.id, text)
            if key in seen:
                continue
            seen.add(key)
            last_seen_texts.append(text[:80])
            if len(last_seen_texts) > 5:
                last_seen_texts.pop(0)

            parsed = _parse_result(text)
            if parsed is not None:
                log_bot.info(
                    f"casino: result parsed {parsed} (msg {m.id}) "
                    f"raw={text[:120]!r}"
                )
                return parsed

    log_bot.warning(
        f"casino: no result within {GAME_TIMEOUT:.0f}s. "
        f"Recent texts: {last_seen_texts}"
    )
    return None


def _parse_result(text: str) -> Optional[dict]:
    """Parse Meowie result. Handles many formats.

    Examples:
      "Score: 2 | 1.5x"
      "ꜱᴄᴏʀᴇ: 2 | x1.5"
      "🎰 Score 2 | Multiplier 1.5x"
      "Score:0|0x"
    """
    if not text:
        return None

    # نرمال‌سازی حروف یونیکد به ascii
    t = text
    replacements = {
        "ꜱ": "s", "ᴄ": "c", "ᴏ": "o", "ʀ": "r", "ᴇ": "e",
        "ᴋ": "k", "ᴍ": "m", "ᴘ": "p", "ɪ": "i", "ɴ": "n",
        "×": "x", "✕": "x", "✖": "x",
    }
    for a, b in replacements.items():
        t = t.replace(a, b)
    low = t.lower()

    # --- score ---
    score = None
    for pat in (
        r"score\s*[:：=]\s*(\d+)",
        r"score\s+(\d+)",
        r"🎰[^\d]{0,20}(\d+)",
        r"\|\s*(\d+)\s*\|",
        r"(\d+)\s*\|",
    ):
        m = re.search(pat, low)
        if m:
            try:
                score = int(m.group(1))
                break
            except ValueError:
                continue
    if score is None:
        return None

    # --- multiplier ---
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
        # regex مولتیپلایر را پیدا نکرد — از روی score نتیجه بگیر
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
    peak_bet = max(int(cfg["peak_bet"]), bet)
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