"""Self-bot casino automator — plays Meowie Casino's Wheel of Fortune.

Button positions in the main casino panel (from testing):
  [0][0] = 🍷 MEOWIE GAMBLE
  [0][1] = 🎰 WHEEL OF FORTUNE  ← what we want
  [0][2] = 🎲 DICE
  [1][0] = 💎 DIAMOND MINE
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
EMOJI_TRIGGER = "🎰"

DEFAULT_INTERVAL = 301
GAME_TIMEOUT = 120.0
POLL_INTERVAL = 0.4

_INLINE_TYPES = (
    "KeyboardButtonCallback",
    "KeyboardInlineButton",
    "KeyboardButtonUrl",
    "KeyboardButtonSwitchInline",
)

# Position of WHEEL OF FORTUNE in the main panel
WHEEL_ROW = 0
WHEEL_COL = 1


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
    if not msg or not msg.reply_markup:
        return False
    if not hasattr(msg.reply_markup, "rows"):
        return False
    for row in msg.reply_markup.rows:
        for b in row.buttons:
            if type(b).__name__ in _INLINE_TYPES:
                return True
    return False


def _find_button_by_text(msg, exact_texts: list):
    """Find inline button whose text equals one of exact_texts (case-insensitive)."""
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
            text = _button_text(btn)
            if text in wanted:
                return btn
            # also check if any wanted text is a substring
            for w in wanted:
                if w and w in text:
                    return btn
    return None


def _find_button_by_pattern(msg, patterns: list):
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
    return None


def _find_button_by_position(msg, row: int = 0, col: int = 0):
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


def _log_all_buttons(panel_msg, label: str = "") -> None:
    try:
        if not panel_msg or not panel_msg.reply_markup:
            return
        for i, row in enumerate(panel_msg.reply_markup.rows):
            for j, b in enumerate(row.buttons):
                log_bot.info(
                    f"casino: [btn{label}] [{i}][{j}] "
                    f"type={type(b).__name__} "
                    f"text={getattr(b, 'text', '?')!r}"
                )
    except Exception:
        pass


async def _click_button(client, chat_id: int, message_id: int, btn) -> bool:
    if btn is None:
        log_bot.warning("casino: click called with None button")
        return False
    try:
        btext = getattr(btn, "text", "")
        data = getattr(btn, "data", None)
        log_bot.info(
            f"casino: [click] text={btext!r} "
            f"data_len={len(data) if data else 0}"
        )
        msg = await client.get_messages(chat_id, ids=message_id)
        if msg is None:
            return False

        # try data click first
        if data:
            try:
                await msg.click(data=data)
                log_bot.info("casino: [click] OK via data")
                return True
            except Exception as e:
                log_bot.warning(f"casino: [click] data failed: {e}")

        # try text click
        if btext and btext.strip():
            try:
                await msg.click(text=btext)
                log_bot.info("casino: [click] OK via text")
                return True
            except Exception as e:
                log_bot.warning(f"casino: [click] text failed: {e}")

        # last resort: raw request
        if data:
            try:
                await client(GetBotCallbackAnswerRequest(
                    peer=chat_id, msg_id=message_id, data=data,
                ))
                log_bot.info("casino: [click] OK via raw")
                return True
            except Exception as e:
                log_bot.warning(f"casino: [click] raw failed: {e}")

        log_bot.error("casino: [click] all failed")
        return False
    except Exception as e:
        log_bot.warning(f"casino: [click] FAILED: {e}")
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
    save_config(user_id, initial_bet=int(amount), current_bet=int(amount),
                peak_bet=int(amount))


def set_interval(user_id: int, seconds: int) -> None:
    save_config(user_id, bet_interval=max(60, int(seconds)))


def set_multipliers(user_id: int, mults: str) -> None:
    save_config(user_id, multipliers=str(mults))


def reset_progress(user_id: int) -> None:
    save_config(
        user_id,
        current_bet=get_config(user_id)["initial_bet"],
        total_plays=0, total_wins=0, total_losses=0, total_pushes=0,
        total_profit=0, peak_bet=get_config(user_id)["initial_bet"],
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
        self.panel_msg_id: Optional[int] = None
        self.last_panel_id: Optional[int] = None
        self.bet_amount: int = 0
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
            if not _msg_has_clickable(event):
                return
            if not event.is_private and not event.is_group:
                return
            if int(event.chat_id or 0) != int(st.chat_id or 0):
                return
            st.last_panel_id = event.id
            try:
                save_config(user_id, panel_msg_id=event.id)
            except Exception:
                pass
            log_bot.info(
                f"casino: [listener] new panel uid={user_id} msg_id={event.id}"
            )
        except Exception as e:
            log_bot.debug(f"casino: listener new error: {e}")

    @client.on(events.MessageEdited(incoming=True))
    async def _on_edit(event):
        try:
            if not _msg_has_clickable(event):
                return
            if not event.is_private and not event.is_group:
                return
            if int(event.chat_id or 0) != int(st.chat_id or 0):
                return
            st.last_panel_id = event.id
            try:
                save_config(user_id, panel_msg_id=event.id)
            except Exception:
                pass
            log_bot.info(
                f"casino: [listener] edited panel uid={user_id} msg_id={event.id}"
            )
        except Exception as e:
            log_bot.debug(f"casino: listener edit error: {e}")

    client._casino_listener_installed = True
    st.listener_installed = True
    log_bot.info(f"casino: listener installed on user client uid={user_id}")


# ===========================================================================
# Loop
# ===========================================================================
async def casino_loop(runtime, user_id: int) -> None:
    log_bot.info(f"casino: loop started for uid={user_id}")
    while not runtime.stop_event.is_set():
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
                                                timeout=min(wait, 60))
                    except asyncio.TimeoutError:
                        pass
                    continue
            except Exception:
                pass

        try:
            await _run_cycle(runtime, user_id)
        except Exception as e:
            log_bot.exception(f"casino: cycle failed uid={user_id}: {e}")

        cfg = get_config(user_id)
        interval = int(cfg.get("bet_interval") or DEFAULT_INTERVAL)
        next_dt = datetime.now(dt_timezone.utc) + timedelta(seconds=interval)
        save_config(user_id, last_run=_now_iso(), next_run=next_dt.isoformat())

        try:
            await asyncio.wait_for(runtime.stop_event.wait(),
                                    timeout=min(interval, 30))
        except asyncio.TimeoutError:
            pass


# ===========================================================================
# Cycle
# ===========================================================================
async def _run_cycle(runtime, user_id: int) -> None:
    cfg = get_config(user_id)
    chat_id = cfg["chat_id"]
    bet = int(cfg["current_bet"])
    client = runtime.user_clients.get(int(user_id))
    if client is None:
        return

    try:
        install_casino_listener(runtime, user_id, client)
    except Exception:
        pass

    st = _get_state(user_id)
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
        result = await _play_round(runtime, client, user_id, chat_id, bet, st)
        await _finalize_run(user_id, chat_id, bet, result)
    except asyncio.TimeoutError as e:
        db.update_casino_run(st.run_id, status="timeout", error=str(e),
                              finished_at=_now_iso())
    except Exception as e:
        db.update_casino_run(st.run_id, status="error",
                              error=f"{type(e).__name__}: {e}",
                              finished_at=_now_iso())
        log_bot.error(f"casino: cycle exception:\n{traceback.format_exc()}")


async def _play_round(runtime, client, user_id: int, chat_id: int,
                       bet: int, st: CasinoState) -> str:
    try:
        # ---------- 1. Send "Casino" ----------
        log_bot.info(f"casino: [1] sending 'Casino' uid={user_id}")
        sent = await client.send_message(chat_id, CASINO_TRIGGER_TEXT)
        sent_id = sent.id
        await asyncio.sleep(3.0)

        # ---------- 2. Find panel ----------
        panel_msg = None
        if st.last_panel_id:
            cached = await _get_message(client, chat_id, st.last_panel_id)
            if _msg_has_clickable(cached):
                panel_msg = cached

        if panel_msg is None:
            for attempt in range(1, 11):
                await asyncio.sleep(2.0)
                panel_msg = await _find_panel(client, chat_id, sent_id)
                if panel_msg is not None:
                    break

        if panel_msg is None:
            raise TimeoutError("panel not found")

        st.panel_msg_id = panel_msg.id
        st.last_panel_id = panel_msg.id
        save_config(user_id, panel_msg_id=panel_msg.id)
        log_bot.info(f"casino: [2] panel id={panel_msg.id}")
        _log_all_buttons(panel_msg, "init")

        # ---------- 3. Click WHEEL OF FORTUNE = [0][1] ----------
        log_bot.info("casino: [3] clicking WHEEL [0][1]")
        btn = _find_button_by_position(panel_msg, row=WHEEL_ROW, col=WHEEL_COL)
        if btn is None:
            log_bot.warning("casino: [3] [0][1] not found, fallback")
            btn = _find_button_by_position(panel_msg, row=0, col=0)
        if btn is None:
            raise TimeoutError("wheel button not found")
        await _click_button(client, chat_id, panel_msg.id, btn)
        await asyncio.sleep(2.5)

        # ---------- 4. Click SET ENTRANCE FEE ----------
        log_bot.info("casino: [4] clicking SET ENTRANCE FEE")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        _log_all_buttons(panel_msg, "step4")
        btn = _find_button_by_pattern(panel_msg, [
            r"set\s*entrance\s*fee", r"ꜱᴇᴛ\s*ᴇɴᴛʀᴀɴᴄᴇ", r"entrance\s*fee",
        ])
        if btn is not None:
            await _click_button(client, chat_id, panel_msg.id, btn)
            await asyncio.sleep(2.0)
        else:
            log_bot.warning("casino: [4] set-entrance button not found")

        # ---------- 5. Reply with bet amount ----------
        log_bot.info(f"casino: [5] replying with bet {_format_amount(bet)}")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        await client.send_message(chat_id, _format_amount(bet), reply_to=panel_msg.id)
        await asyncio.sleep(3.0)

        # ---------- 6. Click SET NUMBER OF PLAYERS ----------
        log_bot.info("casino: [6] clicking SET NUMBER OF PLAYERS")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        _log_all_buttons(panel_msg, "step6")
        btn = _find_button_by_pattern(panel_msg, [
            r"set\s*number\s*of\s*players", r"ꜱᴇᴛ\s*ɴᴜᴍʙᴇʀ", r"number\s*of\s*players",
        ])
        if btn is not None:
            await _click_button(client, chat_id, panel_msg.id, btn)
            await asyncio.sleep(2.0)
            # then click "1" or first button
            panel_msg = await _get_message(client, chat_id, panel_msg.id)
            _log_all_buttons(panel_msg, "step6b")
            btn1 = _find_button_by_text(panel_msg, ["1", "1️⃣"])
            if btn1 is None:
                btn1 = _find_button_by_position(panel_msg, row=0, col=0)
            if btn1 is not None:
                await _click_button(client, chat_id, panel_msg.id, btn1)
                await asyncio.sleep(2.0)

        # ---------- 7. Click CREATE GAMBLING TABLE ----------
        log_bot.info("casino: [7] clicking CREATE GAMBLING TABLE")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        _log_all_buttons(panel_msg, "step7")
        btn = _find_button_by_pattern(panel_msg, [
            r"create\s*gambling\s*table", r"ᴄʀᴇᴀᴛᴇ\s*ɢᴀᴍʙʟɪɴɢ",
        ])
        if btn is not None:
            await _click_button(client, chat_id, panel_msg.id, btn)
            await asyncio.sleep(2.5)

        # ---------- 8. Reply with 🎰 emoji ----------
        log_bot.info("casino: [8] sending 🎰")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        await client.send_message(chat_id, EMOJI_TRIGGER, reply_to=panel_msg.id)

        # ---------- 9. Poll for result ----------
        log_bot.info("casino: [9] polling for result")
        return await _poll_result(client, chat_id, panel_msg.id)

    except Exception as e:
        log_bot.error(
            f"casino: play_round FAILED: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        raise


async def _find_panel(client, chat_id: int, sent_id: int):
    try:
        msgs = await client.get_messages(chat_id, limit=50)
        # reply to trigger
        for m in msgs:
            if getattr(m, "reply_to_msg_id", None) == sent_id and _msg_has_clickable(m):
                return m
        # newest clickable newer than trigger
        best = None
        for m in msgs:
            if m.id <= sent_id:
                continue
            if _msg_has_clickable(m):
                if best is None or m.id > best.id:
                    best = m
        if best is not None:
            return best
        # broad search
        msgs = await client.get_messages(chat_id, limit=200)
        best = None
        for m in msgs:
            if _msg_has_clickable(m):
                if best is None or m.id > best.id:
                    best = m
        return best
    except Exception:
        return None


async def _get_message(client, chat_id: int, msg_id: int):
    try:
        return await client.get_messages(chat_id, ids=msg_id)
    except Exception:
        return None


async def _poll_result(client, chat_id: int, msg_id: int) -> str:
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
    win_symbols = ["🍇", "🍒", "bar", "💎", "🎰", "7"]
    lose_symbols = ["0", "❌", "💀", "lose"]
    if any(s in tail for s in win_symbols):
        return {"result": "win", "score": score}
    if any(s in tail for s in lose_symbols):
        return {"result": "lose", "score": score}
    return {"result": "push", "score": score}


async def _finalize_run(user_id: int, chat_id: int, bet: int, result: str) -> None:
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
    db.update_casino_run(st.run_id, multiplier=float(mult), result=result,
                          profit=int(profit), status="finished",
                          finished_at=_now_iso())
    save_config(
        user_id,
        current_bet=int(next_bet),
        total_plays=cfg["total_plays"] + 1,
        total_wins=cfg["total_wins"] + (1 if result == "win" else 0),
        total_losses=cfg["total_losses"] + (1 if result == "lose" else 0),
        total_profit=cfg["total_profit"] + int(profit),
        peak_bet=max(cfg["peak_bet"], bet),
    )
    log_bot.info(f"casino: done uid={user_id} result={result} profit={profit} next={next_bet}")


# ===========================================================================
# Manager
# ===========================================================================
async def start_all_loops(runtime) -> None:
    for cfg_row in db.list_casino_enabled():
        uid = int(cfg_row["user_id"])
        asyncio.create_task(casino_loop(runtime, uid))
        log_bot.info(f"casino: spawned loop uid={uid}")


async def ensure_loop(runtime, user_id: int) -> None:
    st = _STATES.get(int(user_id))
    if st is None:
        asyncio.create_task(casino_loop(runtime, user_id))


async def trigger_now(runtime, user_id: int) -> dict:
    cfg = get_config(user_id)
    if not cfg["chat_id"]:
        return {"ok": False, "error": "no chat_id"}
    client = runtime.user_clients.get(int(user_id))
    if client is None:
        return {"ok": False, "error": "user client not connected"}
    save_config(user_id, next_run="")
    try:
        await _run_cycle(runtime, user_id)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}