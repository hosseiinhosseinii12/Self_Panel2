"""Self-bot casino automator — plays Meowie Casino's Wheel of Fortune.

Uses reply_to_msg_id to find the panel message: when we send "Casino",
the bot replies to our message with the panel. This is the most reliable
way to identify the panel.

Also accepts KeyboardInlineButton (Telethon's placeholder) as clickable.
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
    from telethon import events
    from telethon.tl.functions.messages import GetBotCallbackAnswerRequest
    from telethon.tl.types import (
        ReplyInlineMarkup,
        KeyboardButtonCallback,
        KeyboardButtonUrl,
    )
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False


# ===========================================================================
# Constants
# ===========================================================================
CASINO_TRIGGER_TEXT = "Casino"
WHEEL_BUTTON_PATTERNS = [
    r"wheel\s*of\s*fortune",
    r"ᴡʜᴇᴇʟ\s*ᴏꜰ\s*ꜰᴏʀᴛᴜɴᴇ",
    r"🎰",
]
SET_ENTRANCE_PATTERNS = [
    r"set\s*entrance\s*fee",
    r"ꜱᴇᴛ\s*ᴇɴᴛʀᴀɴᴄᴇ",
    r"entrance\s*fee",
]
SET_PLAYERS_PATTERNS = [
    r"set\s*number\s*of\s*players",
    r"ꜱᴇᴛ\s*ɴᴜᴍʙᴇʀ",
    r"number\s*of\s*players",
]
CREATE_TABLE_PATTERNS = [
    r"create\s*gambling\s*table",
    r"ᴄʀᴇᴀᴛᴇ\s*ɢᴀᴍʙʟɪɴɢ",
]
EMOJI_TRIGGER = "🎰"

DEFAULT_INTERVAL = 301
GAME_TIMEOUT = 120.0
POLL_INTERVAL = 0.4

# Inline button types we accept as "clickable"
_INLINE_TYPES = (
    "KeyboardButtonCallback",
    "KeyboardInlineButton",
    "KeyboardButtonUrl",
    "KeyboardButtonSwitchInline",
)


# ===========================================================================
# Helpers
# ===========================================================================
def _rand_id() -> str:
    return "run_" + secrets.token_hex(6)


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def _format_amount(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n // 1_000_000_000}g"
    if n >= 1_000_000:
        return f"{n // 1_000_000}m"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


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
    """Check if a message has at least one inline button.
    Accepts KeyboardInlineButton (Telethon's placeholder).
    """
    if not msg or not msg.reply_markup:
        return False
    if not hasattr(msg.reply_markup, "rows"):
        return False
    for row in msg.reply_markup.rows:
        for b in row.buttons:
            if type(b).__name__ in _INLINE_TYPES:
                return True
    return False


def _find_button(msg, patterns: list):
    """Find the first inline button whose text matches any pattern."""
    if not msg or not msg.reply_markup:
        return None
    rm = msg.reply_markup
    if not hasattr(rm, "rows"):
        return None
    for row in rm.rows:
        for btn in row.buttons:
            if type(btn).__name__ not in _INLINE_TYPES:
                continue
            text = _button_text(btn)
            if _match_any(text, patterns):
                return btn
            for p in patterns:
                if len(p) <= 2 and p and ord(p[0]) > 0x2000:
                    if p in text:
                        return btn
    return None


def _find_button_by_text(msg, exact_texts: list):
    """Find inline button whose text equals one of exact_texts."""
    if not msg or not msg.reply_markup:
        return None
    rm = msg.reply_markup
    if not hasattr(rm, "rows"):
        return None
    wanted = {t.strip().lower() for t in exact_texts}
    for row in rm.rows:
        for btn in row.buttons:
            if type(btn).__name__ not in _INLINE_TYPES:
                continue
            if _button_text(btn) in wanted:
                return btn
    return None


def _find_button_by_position(msg, row: int = 0, col: int = 0):
    """Find an inline button by row/column position (0-indexed)."""
    if not msg or not msg.reply_markup:
        return None
    rm = msg.reply_markup
    if not hasattr(rm, "rows"):
        return None
    rows = rm.rows
    if row >= len(rows):
        return None
    buttons = rows[row].buttons
    if col >= len(buttons):
        return None
    btn = buttons[col]
    if type(btn).__name__ not in _INLINE_TYPES:
        return None
    return btn


def _log_all_buttons(panel_msg) -> None:
    try:
        if not panel_msg:
            log_bot.info("casino: [debug] panel_msg is None")
            return
        if not panel_msg.reply_markup:
            log_bot.info("casino: [debug] panel_msg has no reply_markup")
            return
        log_bot.info(f"casino: [debug] panel msg id={panel_msg.id}")
        log_bot.info(f"casino: [debug] markup type={type(panel_msg.reply_markup).__name__}")
        for i, row in enumerate(panel_msg.reply_markup.rows):
            for j, b in enumerate(row.buttons):
                text = getattr(b, "text", "?")
                data = getattr(b, "data", b"")
                tname = type(b).__name__
                log_bot.info(
                    f"casino: [debug] button [{i}][{j}] "
                    f"text={text!r} type={tname} "
                    f"data_len={len(data) if data else 0}"
                )
    except Exception as e:
        log_bot.warning(f"casino: log buttons failed: {e}")


async def _click_button(client, chat_id: int, message_id: int, btn) -> bool:
    """Click an inline button. Multi-method fallback:
      1) msg.click(data=...) — works for KeyboardButtonCallback
      2) btn.click() — native button method
      3) msg.click(text=...) — click by button text
    """
    if btn is None:
        log_bot.warning("casino: click called with None button")
        return False
    try:
        btype = type(btn).__name__
        btext = getattr(btn, "text", "")
        data = getattr(btn, "data", None)
        log_bot.info(
            f"casino: [click] button type={btype} text={btext!r} "
            f"data_len={len(data) if data else 0}"
        )

        msg = await client.get_messages(chat_id, ids=message_id)
        if msg is None:
            log_bot.warning("casino: [click] message not found")
            return False

        # Method 1: msg.click with explicit data
        if data:
            try:
                await msg.click(data=data)
                log_bot.info("casino: [click] OK via msg.click(data=...)")
                return True
            except Exception as e:
                log_bot.warning(f"casino: [click] data click failed: {type(e).__name__}: {e}")

        # Method 2: native btn.click()
        if hasattr(btn, "click") and callable(getattr(btn, "click")):
            try:
                await btn.click()
                log_bot.info("casino: [click] OK via btn.click()")
                return True
            except Exception as e:
                log_bot.warning(f"casino: [click] btn.click() failed: {type(e).__name__}: {e}")

        # Method 3: msg.click by text
        if btext:
            try:
                await msg.click(text=btext)
                log_bot.info("casino: [click] OK via msg.click(text=...)")
                return True
            except Exception as e:
                log_bot.warning(f"casino: [click] text click failed: {type(e).__name__}: {e}")

        # Method 4: raw GetBotCallbackAnswerRequest with data
        if data:
            try:
                await client(GetBotCallbackAnswerRequest(
                    peer=chat_id,
                    msg_id=message_id,
                    data=data,
                ))
                log_bot.info("casino: [click] OK via raw GetBotCallbackAnswer")
                return True
            except Exception as e:
                log_bot.warning(f"casino: [click] raw answer failed: {type(e).__name__}: {e}")

        log_bot.error("casino: [click] all methods failed")
        return False
    except Exception as e:
        log_bot.warning(
            f"casino: [click] FAILED text={getattr(btn, 'text', '?')!r} "
            f"{type(e).__name__}: {e}"
        )
        return False


# ===========================================================================
# Config
# ===========================================================================
def get_config(user_id: int) -> dict:
    c = db.get_casino_config(user_id) or {}
    return {
        "enabled": bool(c.get("enabled", 0)),
        "chat_id": c.get("chat_id"),
        "initial_bet": int(c.get("initial_bet", 100000)),
        "current_bet": int(c.get("current_bet", 100000)),
        "max_bet": int(c.get("max_bet", 100_000_000)),
        "min_bet": int(c.get("min_bet", 1000)),
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
        "peak_bet": int(c.get("peak_bet", 0)),
        "panel_msg_id": int(c.get("panel_msg_id", 0) or 0),
    }


def save_config(user_id: int, **fields) -> None:
    db.upsert_casino_config(user_id, **fields)


def set_enabled(user_id: int, enabled: bool) -> None:
    save_config(user_id, enabled=1 if enabled else 0)


def set_chat(user_id: int, chat_id: int) -> None:
    save_config(user_id, chat_id=int(chat_id), panel_msg_id=0)


def set_initial_bet(user_id: int, amount: int) -> None:
    save_config(
        user_id,
        initial_bet=int(amount),
        current_bet=int(amount),
        peak_bet=int(amount),
    )


def set_interval(user_id: int, seconds: int) -> None:
    save_config(user_id, bet_interval=max(60, int(seconds)))


def set_multipliers(user_id: int, mults: str) -> None:
    save_config(user_id, multipliers=str(mults))


def reset_progress(user_id: int) -> None:
    save_config(
        user_id,
        current_bet=get_config(user_id)["initial_bet"],
        total_plays=0,
        total_wins=0,
        total_losses=0,
        total_pushes=0,
        total_profit=0,
        peak_bet=get_config(user_id)["initial_bet"],
    )


# ===========================================================================
# Strategy
# ===========================================================================
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


def _next_bet(cfg: dict, result: str, multiplier: float) -> int:
    current = int(cfg.get("current_bet") or cfg["initial_bet"])
    initial = int(cfg["initial_bet"])
    max_bet = int(cfg["max_bet"])
    mults = _parse_multipliers(cfg)

    if result == "lose":
        factor = max(mults) if mults else 3.0
        nxt = int(current * factor)
    elif result == "win":
        nxt = initial
    elif result == "push":
        nxt = current
    else:
        nxt = current

    if nxt < int(cfg["min_bet"]):
        nxt = int(cfg["min_bet"])
    if nxt > max_bet:
        nxt = max_bet
    return nxt


# ===========================================================================
# State
# ===========================================================================
class CasinoState:
    def __init__(self):
        self.user_id: Optional[int] = None
        self.chat_id: Optional[int] = None
        self.run_id: Optional[str] = None
        self.stage: str = "idle"
        self.bet_amount: int = 0
        self.panel_msg_id: Optional[int] = None
        self.last_panel_id: Optional[int] = None
        self.timer_msg_id: Optional[int] = None
        self.started_at: float = 0.0
        self.deadline: float = 0.0
        self.result: str = ""
        self.multiplier: float = 0.0
        self.score: int = 0
        self.emoji: str = ""
        self.waiting: Optional[asyncio.Future] = None
        self.lock = asyncio.Lock()
        self.listener_installed: bool = False


_STATES: dict = {}


def _get_state(user_id: int) -> CasinoState:
    st = _STATES.get(int(user_id))
    if st is None:
        st = CasinoState()
        st.user_id = int(user_id)
        _STATES[int(user_id)] = st
    return st


def _get_state_by_chat(chat_id: int) -> Optional[CasinoState]:
    for st in _STATES.values():
        if st.chat_id == chat_id:
            return st
    return None


# ===========================================================================
# Persistent listener
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
            if not _msg_has_clickable(event):
                return
            st.last_panel_id = event.id
            try:
                save_config(user_id, panel_msg_id=event.id)
            except Exception:
                pass
            log_bot.info(
                f"casino: [listener] new panel captured uid={user_id} "
                f"msg_id={event.id}"
            )
        except Exception as e:
            log_bot.debug(f"casino: listener new error: {e}")

    @client.on(events.MessageEdited(incoming=True))
    async def _on_edit(event):
        try:
            if not _msg_has_clickable(event):
                return
            st.last_panel_id = event.id
            try:
                save_config(user_id, panel_msg_id=event.id)
            except Exception:
                pass
            log_bot.info(
                f"casino: [listener] edited panel captured uid={user_id} "
                f"msg_id={event.id}"
            )
        except Exception as e:
            log_bot.debug(f"casino: listener edit error: {e}")

    client._casino_listener_installed = True
    st.listener_installed = True
    log_bot.info(f"casino: listener installed on user client uid={user_id}")


# ===========================================================================
# Scheduler loop
# ===========================================================================
async def casino_loop(runtime, user_id: int) -> None:
    log_bot.info(f"casino: loop started for uid={user_id}")
    st = _get_state(user_id)

    while not runtime.stop_event.is_set():
        cfg = get_config(user_id)
        if not cfg["enabled"] or not cfg["chat_id"]:
            await asyncio.sleep(15)
            continue

        next_run_iso = cfg.get("next_run") or ""
        if next_run_iso:
            try:
                next_dt = datetime.fromisoformat(next_run_iso)
                now = datetime.now(dt_timezone.utc)
                wait = (next_dt - now).total_seconds()
                if wait > 0:
                    try:
                        await asyncio.wait_for(runtime.stop_event.wait(),
                                                timeout=min(wait, 60))
                    except asyncio.TimeoutError:
                        pass
                    continue
            except Exception:
                pass

        try:
            await _run_cycle(runtime, user_id)
        except Exception as e:
            log_bot.exception(f"casino: cycle failed for uid={user_id}: {e}")
            try:
                db.add_casino_log(user_id, cfg["chat_id"], "error",
                                  f"cycle failed: {e}")
            except Exception:
                pass

        cfg = get_config(user_id)
        interval = int(cfg.get("bet_interval") or DEFAULT_INTERVAL)
        next_dt = datetime.now(dt_timezone.utc) + timedelta(seconds=interval)
        save_config(user_id, last_run=_now_iso(), next_run=next_dt.isoformat())

        try:
            await asyncio.wait_for(runtime.stop_event.wait(),
                                    timeout=min(interval, 30))
        except asyncio.TimeoutError:
            pass

    log_bot.info(f"casino: loop stopped for uid={user_id}")


# ===========================================================================
# Single cycle
# ===========================================================================
async def _run_cycle(runtime, user_id: int) -> None:
    cfg = get_config(user_id)
    chat_id = cfg["chat_id"]
    bet = int(cfg["current_bet"])
    client = runtime.user_clients.get(int(user_id))
    if client is None:
        db.add_casino_log(user_id, chat_id, "error", "user client not connected")
        return

    try:
        install_casino_listener(runtime, user_id, client)
    except Exception as e:
        log_bot.warning(f"casino: late listener install failed: {e}")

    st = _get_state(user_id)
    async with st.lock:
        st.chat_id = chat_id
        st.bet_amount = bet
        st.run_id = _rand_id()
        st.stage = "starting"
        st.started_at = time.time()
        st.deadline = st.started_at + GAME_TIMEOUT
        st.result = ""
        st.multiplier = 0.0
        st.score = 0
        st.emoji = ""
        st.panel_msg_id = None

        if cfg.get("panel_msg_id"):
            st.last_panel_id = int(cfg["panel_msg_id"])

    db.add_casino_run({
        "id": st.run_id,
        "user_id": user_id,
        "chat_id": chat_id,
        "bet_amount": bet,
        "status": "started",
        "started_at": _now_iso(),
    })
    db.add_casino_log(user_id, chat_id, "info",
                      f"run started bet={_format_amount(bet)}")

    try:
        result = await _play_round(runtime, client, user_id, chat_id, bet, st)
        await _finalize_run(user_id, chat_id, bet, result)
    except asyncio.TimeoutError as e:
        db.add_casino_log(user_id, chat_id, "error", f"timeout: {e}")
        db.update_casino_run(st.run_id, status="timeout",
                              error=str(e), finished_at=_now_iso())
    except Exception as e:
        tb = traceback.format_exc()
        db.add_casino_log(user_id, chat_id, "error", f"play failed: {e}")
        db.update_casino_run(st.run_id, status="error",
                              error=f"{type(e).__name__}: {e}",
                              finished_at=_now_iso())
        log_bot.error(f"casino: cycle exception:\n{tb}")


async def _play_round(runtime, client, user_id: int, chat_id: int,
                       bet: int, st: CasinoState) -> str:
    try:
        # ---------- 1. Send "Casino" ----------
        log_bot.info(f"casino: [1] sending '{CASINO_TRIGGER_TEXT}' uid={user_id}")
        sent_msg = await client.send_message(chat_id, CASINO_TRIGGER_TEXT)
        sent_msg_id = sent_msg.id
        log_bot.info(f"casino: [1] sent OK (msg_id={sent_msg_id})")

        await asyncio.sleep(3.0)

        # ---------- 2. Find panel ----------
        panel_msg = None

        # Strategy A: cached panel id from listener or DB
        if st.last_panel_id:
            log_bot.info(
                f"casino: [2] checking cached panel id={st.last_panel_id}"
            )
            cached = await _get_message(client, chat_id, st.last_panel_id)
            if _msg_has_clickable(cached):
                panel_msg = cached
                log_bot.info(
                    f"casino: [2] cached panel id={cached.id} is valid"
                )
            else:
                log_bot.info(
                    f"casino: [2] cached panel id={st.last_panel_id} not valid"
                )
                st.last_panel_id = None

        # Strategy B: retry loop with reply detection
        if panel_msg is None:
            log_bot.info("casino: [2] finding panel message (retry loop)")
            for attempt in range(1, 11):
                await asyncio.sleep(2.0)
                log_bot.info(f"casino: [2] attempt {attempt}/10")
                panel_msg = await _find_panel_after_message(
                    client, chat_id, sent_msg_id
                )
                if panel_msg is not None:
                    log_bot.info(f"casino: [2] found panel on attempt {attempt}")
                    break
                log_bot.info(f"casino: [2] attempt {attempt} - not found yet")

        if panel_msg is None:
            log_bot.error("casino: [2] FAILED - panel not found after 10 attempts")
            raise TimeoutError("casino panel not found after trigger")

        st.panel_msg_id = panel_msg.id
        st.last_panel_id = panel_msg.id
        save_config(user_id, panel_msg_id=panel_msg.id)
        log_bot.info(f"casino: [2] panel msg id={panel_msg.id}")

        _log_all_buttons(panel_msg)

        # ---------- 3. Click "WHEEL OF FORTUNE" ----------
        log_bot.info("casino: [3] looking for wheel button")
        btn = _find_button(panel_msg, WHEEL_BUTTON_PATTERNS)
        if btn is None:
            log_bot.info("casino: [3] wheel not found by text, trying position [0][0]")
            btn = _find_button_by_position(panel_msg, row=0, col=0)
        if btn is None:
            log_bot.error("casino: [3] FAILED - wheel button not found")
            raise TimeoutError("wheel of fortune button not found")
        log_bot.info(
            f"casino: [3] clicking wheel button "
            f"(text={getattr(btn, 'text', '?')!r} "
            f"data_len={len(getattr(btn, 'data', b'')) or 0})"
        )
        ok = await _click_button(client, chat_id, panel_msg.id, btn)
        log_bot.info(f"casino: [3] click returned {ok}")
        await asyncio.sleep(3.0)

        # ---------- 4. Click "SET ENTRANCE FEE" ----------
        log_bot.info("casino: [4] looking for set-entrance button")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        _log_all_buttons(panel_msg)
        btn = _find_button(panel_msg, SET_ENTRANCE_PATTERNS)
        if btn is None:
            log_bot.info("casino: [4] not found by text, trying position [0][0]")
            btn = _find_button_by_position(panel_msg, row=0, col=0)
        if btn is not None:
            log_bot.info(f"casino: [4] clicking set-entrance "
                         f"(text={getattr(btn, 'text', '?')!r})")
            await _click_button(client, chat_id, panel_msg.id, btn)
            await asyncio.sleep(2.0)
        else:
            log_bot.warning("casino: [4] set-entrance-fee button not found, skipping")

        # ---------- 5. Reply with bet amount ----------
        log_bot.info(f"casino: [5] replying with bet {_format_amount(bet)}")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        await client.send_message(
            chat_id,
            _format_amount(bet),
            reply_to=panel_msg.id,
        )
        log_bot.info("casino: [5] reply sent")
        await asyncio.sleep(3.0)

        # ---------- 6. Click "SET NUMBER OF PLAYERS" ----------
        log_bot.info("casino: [6] looking for set-players button")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        _log_all_buttons(panel_msg)
        btn = _find_button(panel_msg, SET_PLAYERS_PATTERNS)
        if btn is None:
            log_bot.info("casino: [6] not found by text, trying position [0][1]")
            btn = _find_button_by_position(panel_msg, row=0, col=1)
        if btn is not None:
            log_bot.info("casino: [6] clicking set-players")
            await _click_button(client, chat_id, panel_msg.id, btn)
            await asyncio.sleep(2.0)

            log_bot.info("casino: [6b] looking for '1' button")
            panel_msg = await _get_message(client, chat_id, panel_msg.id)
            _log_all_buttons(panel_msg)
            btn = _find_button_by_text(panel_msg, ["1", "1️⃣", "₁"])
            if btn is None:
                log_bot.info("casino: [6b] not found by text, trying position [0][0]")
                btn = _find_button_by_position(panel_msg, row=0, col=0)
            if btn is not None:
                log_bot.info("casino: [6b] clicking '1'")
                await _click_button(client, chat_id, panel_msg.id, btn)
                await asyncio.sleep(2.0)

        # ---------- 7. Click "CREATE GAMBLING TABLE" ----------
        log_bot.info("casino: [7] looking for create-table button")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        _log_all_buttons(panel_msg)
        btn = _find_button(panel_msg, CREATE_TABLE_PATTERNS)
        if btn is None:
            log_bot.info("casino: [7] not found by text, trying position [0][0]")
            btn = _find_button_by_position(panel_msg, row=0, col=0)
        if btn is not None:
            log_bot.info("casino: [7] clicking create-table")
            await _click_button(client, chat_id, panel_msg.id, btn)
            await asyncio.sleep(2.5)

        # ---------- 8. Reply with 🎰 emoji ----------
        log_bot.info(f"casino: [8] sending emoji {EMOJI_TRIGGER}")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        await client.send_message(
            chat_id,
            EMOJI_TRIGGER,
            reply_to=panel_msg.id,
        )
        log_bot.info("casino: [8] emoji sent")
        st.timer_msg_id = panel_msg.id

        # ---------- 9. Poll for result ----------
        log_bot.info("casino: [9] polling for result")
        return await _poll_result(client, chat_id, panel_msg.id, st)

    except Exception as e:
        log_bot.error(
            f"casino: play_round FAILED: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        raise


async def _find_panel_after_message(client, chat_id: int, sent_msg_id: int):
    """Find the panel message.
    Priority:
      1. Reply to our trigger (sent_msg_id) — MOST RELIABLE
      2. Cached last_panel_id (checked by caller)
      3. Newest msg with clickable buttons, newer than trigger
      4. Broad search of last 200
    """
    st = _get_state_by_chat(chat_id)
    try:
        # Get a chunk of recent messages
        msgs = await client.get_messages(chat_id, limit=50)

        # ---- Priority 1: reply to our trigger ----
        log_bot.info(
            f"casino: [panel] looking for reply to trigger {sent_msg_id}"
        )
        reply_candidates = []
        for m in msgs:
            reply_to = getattr(m, "reply_to_msg_id", None)
            if reply_to and reply_to == sent_msg_id:
                reply_candidates.append(m)

        if reply_candidates:
            log_bot.info(
                f"casino: [panel] found {len(reply_candidates)} replies "
                f"to trigger"
            )
            for m in reply_candidates:
                log_bot.info(
                    f"casino: [panel] reply msg id={m.id} "
                    f"has_markup={bool(m.reply_markup)} "
                    f"text={(m.message or '')[:40]!r}"
                )
                if _msg_has_clickable(m):
                    log_bot.info(
                        f"casino: [panel] FOUND clickable reply id={m.id}"
                    )
                    if st:
                        st.last_panel_id = m.id
                    return m
            log_bot.info("casino: [panel] replies found but none clickable")

        # ---- Priority 2: newest clickable newer than trigger ----
        best = None
        best_id = 0
        for m in msgs:
            if m.id <= sent_msg_id:
                continue
            if not _msg_has_clickable(m):
                continue
            if m.id > best_id:
                best = m
                best_id = m.id

        if best is not None:
            log_bot.info(
                f"casino: [panel] found newer clickable msg id={best.id} "
                f"(trigger={sent_msg_id})"
            )
            if st:
                st.last_panel_id = best.id
            return best

        # ---- Priority 3: broad search ----
        log_bot.info("casino: [panel] falling back to search last 200")
        found = await _find_latest_panel(client, chat_id)
        if found and st:
            st.last_panel_id = found.id
        return found
    except Exception as e:
        log_bot.warning(f"casino: [panel] failed: {type(e).__name__}: {e}")
        return None


async def _find_latest_panel(client, chat_id: int):
    """Find the newest message with a clickable inline button."""
    try:
        msgs = await client.get_messages(chat_id, limit=200)
        log_bot.info(f"casino: [panel] fetched {len(msgs)} messages")

        best = None
        best_id = 0
        markup_count = 0

        for m in msgs:
            if not m.reply_markup:
                continue
            markup_count += 1
            if markup_count <= 10:
                btns = []
                if hasattr(m.reply_markup, "rows"):
                    for row in m.reply_markup.rows:
                        for b in row.buttons:
                            btype = type(b).__name__
                            btns.append(f"{btype}:{getattr(b, 'text', '')[:10]}")
                log_bot.info(
                    f"casino: [panel] msg {m.id} markup has buttons: {btns}"
                )
            if not _msg_has_clickable(m):
                continue
            if m.id > best_id:
                best = m
                best_id = m.id

        log_bot.info(
            f"casino: [panel] found {markup_count} msgs with reply_markup total"
        )

        if best is None:
            log_bot.warning("casino: [panel] no clickable panel found")
            return None

        log_bot.info(f"casino: [panel] selected msg id={best.id}")
        for i, row in enumerate(best.reply_markup.rows):
            for j, b in enumerate(row.buttons):
                text = getattr(b, "text", "?")
                data = getattr(b, "data", b"")
                tname = type(b).__name__
                log_bot.info(
                    f"casino: [panel] button [{i}][{j}] "
                    f"text={text!r} type={tname} "
                    f"data_len={len(data) if data else 0}"
                )
        return best
    except Exception as e:
        log_bot.warning(f"casino: [panel] failed: {type(e).__name__}: {e}")
        return None


async def _get_message(client, chat_id: int, msg_id: int):
    try:
        return await client.get_messages(chat_id, ids=msg_id)
    except Exception:
        return None


async def _poll_result(client, chat_id: int, msg_id: int,
                        st: CasinoState) -> str:
    last_text = ""
    deadline = time.time() + GAME_TIMEOUT

    while time.time() < deadline:
        await asyncio.sleep(POLL_INTERVAL)
        msg = await _get_message(client, chat_id, msg_id)
        if msg is None:
            continue
        text = msg.message or ""
        if not text or text == last_text:
            continue
        last_text = text

        result = _parse_result(text)
        if result is not None:
            st.result = result["result"]
            st.multiplier = result["multiplier"]
            st.score = result["score"]
            st.emoji = result["emoji"]
            log_bot.info(f"casino: result parsed {result}")
            return result["result"]

    return "timeout"


def _parse_result(text: str) -> Optional[dict]:
    if not text:
        return None
    low = text.lower()

    m = re.search(r"score\s*[:：]\s*(\d+)\s*\|\s*(.+)", low)
    if not m:
        return None
    score = int(m.group(1))
    tail = m.group(2).strip()

    emoji = ""
    for ch in tail:
        if ord(ch) > 0x2000:
            emoji = ch
            break

    win_symbols = ["🍇", "🍒", "bar", "💎", "🎰", "7️⃣", "7"]
    lose_symbols = ["0", "❌", "💀", "empty", "no win", "lose"]

    if any(s in tail for s in win_symbols):
        return {"result": "win", "multiplier": 0, "score": score, "emoji": emoji}
    if any(s in tail for s in lose_symbols):
        return {"result": "lose", "multiplier": 0, "score": score, "emoji": emoji}

    return {"result": "push", "multiplier": 0, "score": score, "emoji": emoji}


async def _finalize_run(user_id: int, chat_id: int, bet: int,
                         result: str) -> None:
    cfg = get_config(user_id)
    st = _get_state(user_id)
    mults = _parse_multipliers(cfg)

    if result == "win":
        mult = random.choice(mults) if mults else 2.0
    elif result == "push":
        mult = 1.0
    else:
        mult = 0.0

    if result == "win":
        profit = int(bet * (mult - 1))
    elif result == "push":
        profit = 0
    else:
        profit = -bet

    next_bet = _next_bet(cfg, result, mult)

    db.update_casino_run(
        st.run_id,
        multiplier=float(mult),
        result=result,
        score=st.score,
        emoji=st.emoji,
        profit=int(profit),
        status="finished",
        finished_at=_now_iso(),
    )

    total_plays = cfg["total_plays"] + 1
    total_wins = cfg["total_wins"] + (1 if result == "win" else 0)
    total_losses = cfg["total_losses"] + (1 if result == "lose" else 0)
    total_pushes = cfg["total_pushes"] + (1 if result == "push" else 0)
    total_profit = cfg["total_profit"] + int(profit)
    peak_bet = max(cfg["peak_bet"], bet)

    save_config(
        user_id,
        current_bet=int(next_bet),
        total_plays=total_plays,
        total_wins=total_wins,
        total_losses=total_losses,
        total_pushes=total_pushes,
        total_profit=total_profit,
        peak_bet=peak_bet,
    )

    db.add_casino_log(
        user_id, chat_id, "info",
        f"result={result} bet={_format_amount(bet)} "
        f"mult={mult} profit={profit} next={_format_amount(next_bet)}"
    )
    log_bot.info(
        f"casino: run done uid={user_id} result={result} "
        f"profit={profit} next_bet={next_bet}"
    )


# ===========================================================================
# Manager
# ===========================================================================
async def start_all_loops(runtime) -> None:
    for cfg_row in db.list_casino_enabled():
        uid = int(cfg_row["user_id"])
        asyncio.create_task(casino_loop(runtime, uid))
        log_bot.info(f"casino: spawned loop for uid={uid}")


async def ensure_loop(runtime, user_id: int) -> None:
    st = _STATES.get(int(user_id))
    if st is None:
        asyncio.create_task(casino_loop(runtime, user_id))
        log_bot.info(f"casino: spawned loop for uid={user_id}")


async def trigger_now(runtime, user_id: int) -> dict:
    cfg = get_config(user_id)
    if not cfg["chat_id"]:
        return {"ok": False, "error": "no chat_id set"}
    client = runtime.user_clients.get(int(user_id))
    if client is None:
        return {"ok": False, "error": "user client not connected"}

    save_config(user_id, next_run="")

    try:
        await _run_cycle(runtime, user_id)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}