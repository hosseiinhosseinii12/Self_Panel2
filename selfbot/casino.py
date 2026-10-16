"""Self-bot casino automator — plays Meowie Casino's Wheel of Fortune.

Runs on the USER client (self-bot). Every N seconds (default 301s = 5m 1s)
after the previous game ends, it:
  1. Sends the literal text "Casino" to the configured chat
  2. Waits for the bot's panel message (with retry)
  3. Clicks the "WHEEL OF FORTUNE" inline button
  4. Confirms the click (edits happen on the same message)
  5. Sets entrance fee (replies with the current bet amount)
  6. Clicks "1" for number of players
  7. Clicks "Create Gambling Table"
  8. Replies with the 🎰 emoji in the final panel
  9. Reads the result from the edited message
 10. Applies the martingale multiplier strategy
 11. Schedules the next run

All bot messages from Meowie Casino arrive on the SAME message and get
edited in-place. We track them via a per-run state machine.
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

DEFAULT_INTERVAL = 301  # 5 min 1 sec
BOT_WAIT_TIMEOUT = 30.0
GAME_TIMEOUT = 120.0
POLL_INTERVAL = 0.4


# ===========================================================================
# Helpers
# ===========================================================================
def _rand_id() -> str:
    return "run_" + secrets.token_hex(6)


def _now_iso() -> str:
    return datetime.now(dt_timezone.utc).isoformat()


def _parse_amount(text: str) -> Optional[int]:
    """Parse '100k', '1k', '500', '2.5m' etc. Returns int or None."""
    if not text:
        return None
    t = text.strip().lower().replace(",", "").replace(" ", "")
    m = re.match(r"^(\d+(?:\.\d+)?)([kmg]?)$", t)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2)
    if suffix == "k":
        num *= 1_000
    elif suffix == "m":
        num *= 1_000_000
    elif suffix == "g":
        num *= 1_000_000_000
    return int(num)


def _format_amount(n: int) -> str:
    """Format 100000 → '100k'."""
    if n >= 1_000_000_000:
        return f"{n // 1_000_000_000}g"
    if n >= 1_000_000:
        return f"{n // 1_000_000}m"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


def _button_text(btn) -> str:
    """Extract text from an inline button."""
    if btn is None:
        return ""
    return (getattr(btn, "text", "") or "").strip().lower()


def _match_any(text: str, patterns: list) -> bool:
    low = (text or "").lower()
    for p in patterns:
        if re.search(p, low, re.IGNORECASE):
            return True
    return False


def _find_button(msg, patterns: list):
    """Find the first inline button whose text matches any pattern.
    Also handles buttons whose text is emoji-only, and buttons
    represented as `KeyboardInlineButton` (Telegram's placeholder).
    """
    if not msg or not msg.reply_markup:
        return None
    rm = msg.reply_markup
    if not hasattr(rm, "rows"):
        return None
    for row in rm.rows:
        for btn in row.buttons:
            if not getattr(btn, "data", None):
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
            if not getattr(btn, "data", None):
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
    if not getattr(btn, "data", None):
        return None
    return btn


def _log_all_buttons(panel_msg) -> None:
    """Debug: log every inline button in the panel message."""
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
    """Click an inline callback button using Telethon's native msg.click()."""
    if btn is None:
        log_bot.warning("casino: click called with None button")
        return False
    try:
        data = getattr(btn, "data", None)
        if not data:
            log_bot.warning("casino: button has no data, cannot click")
            return False

        log_bot.info(
            f"casino: [click] fetching message for click "
            f"peer={chat_id} msg={message_id} "
            f"text={getattr(btn, 'text', '?')!r} data_len={len(data)}"
        )

        msg = await client.get_messages(chat_id, ids=message_id)
        if msg is None:
            log_bot.warning("casino: [click] message not found")
            return False

        result = await msg.click(data=data)
        log_bot.info(
            f"casino: [click] OK text={getattr(btn, 'text', '?')!r} "
            f"result_type={type(result).__name__}"
        )
        return True
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
    }


def save_config(user_id: int, **fields) -> None:
    db.upsert_casino_config(user_id, **fields)


def set_enabled(user_id: int, enabled: bool) -> None:
    save_config(user_id, enabled=1 if enabled else 0)


def set_chat(user_id: int, chat_id: int) -> None:
    save_config(user_id, chat_id=int(chat_id))


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
    """Return the next bet amount based on the last result."""
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
# Scheduler loop
# ===========================================================================
class CasinoState:
    """In-memory state for the currently running casino cycle."""

    def __init__(self):
        self.user_id: Optional[int] = None
        self.chat_id: Optional[int] = None
        self.run_id: Optional[str] = None
        self.stage: str = "idle"
        self.bet_amount: int = 0
        self.panel_msg_id: Optional[int] = None
        self.timer_msg_id: Optional[int] = None
        self.started_at: float = 0.0
        self.deadline: float = 0.0
        self.result: str = ""
        self.multiplier: float = 0.0
        self.score: int = 0
        self.emoji: str = ""
        self.waiting: Optional[asyncio.Future] = None
        self.lock = asyncio.Lock()


_STATES: dict = {}  # user_id -> CasinoState


def _get_state(user_id: int) -> CasinoState:
    st = _STATES.get(int(user_id))
    if st is None:
        st = CasinoState()
        st.user_id = int(user_id)
        _STATES[int(user_id)] = st
    return st


async def casino_loop(runtime, user_id: int) -> None:
    """Main loop for a single user's casino automation."""
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
    """Run one full game cycle. Returns 'win' | 'lose' | 'push' | 'timeout'."""
    try:
        # ---------- 1. Send "Casino" ----------
        log_bot.info(f"casino: [1] sending '{CASINO_TRIGGER_TEXT}' uid={user_id}")
        await client.send_message(chat_id, CASINO_TRIGGER_TEXT)
        log_bot.info("casino: [1] sent OK")

        # ---------- 2. Find panel with retry (up to 7 attempts = 14s) ----------
        log_bot.info("casino: [2] finding panel message (retry loop)")
        panel_msg = None
        for attempt in range(1, 8):
            await asyncio.sleep(2.0)
            log_bot.info(f"casino: [2] attempt {attempt}/7")
            panel_msg = await _find_latest_panel(client, chat_id)
            if panel_msg is not None:
                log_bot.info(f"casino: [2] found panel on attempt {attempt}")
                break
            log_bot.info(f"casino: [2] attempt {attempt} - not found yet")

        if panel_msg is None:
            log_bot.error("casino: [2] FAILED - panel not found after 7 attempts")
            raise TimeoutError("casino panel not found after trigger")

        st.panel_msg_id = panel_msg.id
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
            f"data_len={len(btn.data) if getattr(btn, 'data', None) else 0})"
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


async def _find_latest_panel(client, chat_id: int):
    """Find the panel message sent by Meowie Casino.
    Handles in-place edits and delayed responses.
    Accepts any inline button that has `data` (regardless of type name).
    """
    try:
        msgs = await client.get_messages(chat_id, limit=50)
        log_bot.info(f"casino: [panel] fetched {len(msgs)} messages")

        best = None
        best_id = 0

        for m in msgs:
            if not m.reply_markup:
                continue
            if not hasattr(m.reply_markup, "rows"):
                continue

            has_cb = False
            for row in m.reply_markup.rows:
                for b in row.buttons:
                    data = getattr(b, "data", None)
                    if data:
                        has_cb = True
                        break
                if has_cb:
                    break

            if not has_cb:
                continue

            if m.id > best_id:
                best = m
                best_id = m.id

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
    """Poll the panel message until a result appears."""
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
    """Try to parse the game result from the panel text."""
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
    """Spawn a casino loop for each user with casino enabled."""
    for cfg_row in db.list_casino_enabled():
        uid = int(cfg_row["user_id"])
        asyncio.create_task(casino_loop(runtime, uid))
        log_bot.info(f"casino: spawned loop for uid={uid}")


async def ensure_loop(runtime, user_id: int) -> None:
    """Ensure a casino loop is running for a given user."""
    st = _STATES.get(int(user_id))
    if st is None:
        asyncio.create_task(casino_loop(runtime, user_id))
        log_bot.info(f"casino: spawned loop for uid={user_id}")


# ===========================================================================
# Manual trigger
# ===========================================================================
async def trigger_now(runtime, user_id: int) -> dict:
    """Manually run one cycle now. Returns a summary."""
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