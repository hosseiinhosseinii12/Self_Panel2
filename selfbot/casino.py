"""Self-bot casino automator — plays Meowie Casino's Wheel of Fortune.

Strategy (Martingale with 5M cap):
  - Start: 100,000
  - On loss: bet *= 3
  - On win: reset to 100,000
  - Cap: 5,000,000 (bot rejects anything above)
  - Any computed bet above cap -> rounded down to 5,000,000

Ledger (per user):
  - total_invested: sum of all bets placed
  - total_won: sum of winning payouts (bet * multiplier)
  - total_lost: sum of losing bets
  - total_profit: won - lost
  - peak_balance: max(total_won - total_lost)
  - current_streak: consecutive wins
  - best_streak: longest consecutive wins
  - last_multiplier, last_score, last_result

FIX: the animated 🎰 is now sent as a real Telegram Dice
(InputMediaDice) instead of a custom-emoji text entity, so the game bot
counts it (message.dice) and it is animated for every account.
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
GAME_TIMEOUT = 120.0
POLL_INTERVAL = 0.4

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
    """Send a real Telegram dice (animated slot machine) as a reply.

    This is the animated 🎰 that game bots read via `message.dice`.
    The value (1..64 for 🎰) is decided by Telegram's servers.

    Returns the dice value on success (0 if sent but value unknown),
    or None on failure.
    """
    if not HAS_TELETHON:
        return None

    sent = None

    # Attempt 1: high-level send_file with InputMediaDice
    try:
        sent = await client.send_file(
            chat_id,
            types.InputMediaDice(emoticon),
            reply_to=reply_to,
        )
    except Exception as e:
        log_bot.warning(f"casino: [dice] send_file failed: {e}")

    # Attempt 2: raw SendMediaRequest (old/new Telethon signatures)
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
            return 0  # sent, value unknown
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
    save_config(user_id, enabled=1 if enabled else 0)


def set_chat(user_id: int, chat_id: int) -> None:
    save_config(user_id, chat_id=int(chat_id), panel_msg_id=0)


def set_initial_bet(user_id: int, amount: int) -> None:
    amount = max(MIN_BET, int(amount))
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
def compute_next_bet(current_bet: int, result: str,
                      initial_bet: int = START_BET,
                      max_allowed: int = MAX_BET) -> int:
    if result == "win":
        nxt = initial_bet
    elif result == "push":
        nxt = current_bet
    else:
        nxt = current_bet * LOSS_MULTIPLIER

    if nxt > max_allowed:
        nxt = max_allowed
    if nxt >= 1000:
        nxt = (nxt // 1000) * 1000
    if nxt < MIN_BET:
        nxt = MIN_BET
    return nxt


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


# ===========================================================================
# Loop
# ===========================================================================
async def casino_loop(runtime, user_id: int) -> None:
    log_bot.info(f"casino: loop started uid={user_id}")
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
        db.add_casino_log(user_id, chat_id, "error", "user client not connected")
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
        # 1. Send "Casino"
        log_bot.info(f"casino: [1] sending 'Casino' uid={user_id}")
        await client.send_message(chat_id, CASINO_TRIGGER_TEXT)
        await asyncio.sleep(3.0)

        # 2. Find panel
        panel_msg = None
        if st.last_panel_id:
            cached = await _get_message(client, chat_id, st.last_panel_id)
            if _msg_has_clickable(cached):
                panel_msg = cached

        if panel_msg is None:
            for attempt in range(1, 11):
                await asyncio.sleep(2.0)
                msgs = await client.get_messages(chat_id, limit=30)
                for m in msgs:
                    if _msg_has_clickable(m):
                        panel_msg = m
                        break
                if panel_msg is not None:
                    break

        if panel_msg is None:
            raise TimeoutError("panel not found")

        st.panel_msg_id = panel_msg.id
        st.last_panel_id = panel_msg.id
        save_config(user_id, panel_msg_id=panel_msg.id)
        log_bot.info(f"casino: [2] panel id={panel_msg.id}")
        _log_all_buttons(panel_msg, "init")

        # 3. Click WHEEL = index 1
        log_bot.info(f"casino: [3] clicking WHEEL index={WHEEL_INDEX}")
        await _click_button(client, chat_id, panel_msg.id, index=WHEEL_INDEX)
        await asyncio.sleep(3.0)

        # 4. SET ENTRANCE FEE
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

        # 5. Reply with bet amount
        log_bot.info(f"casino: [5] reply bet {_format_amount(bet)}")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        await _send_reply(client, chat_id, _format_amount(bet), panel_msg.id)
        await asyncio.sleep(3.0)

        # 6. SET NUMBER OF PLAYERS
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

        # 7. CREATE GAMBLING TABLE
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

        # -------------------------------------------------------------------
        # 8. Send real animated 🎰 (Telegram dice) — this is what the game counts
        # -------------------------------------------------------------------
        log_bot.info("casino: [8] sending 🎰 dice")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)

        dice_value = await _send_dice(client, chat_id, panel_msg.id)

        if dice_value is None:
            # one retry after a short pause
            await asyncio.sleep(1.5)
            dice_value = await _send_dice(client, chat_id, panel_msg.id)

        if dice_value is None:
            log_bot.warning(
                "casino: [8] dice failed twice, falling back to plain 🎰"
            )
            await _send_reply(client, chat_id, EMOJI_TRIGGER, panel_msg.id)

        # 9. Poll for result
        log_bot.info("casino: [9] polling for result")
        return await _poll_result(client, chat_id, panel_msg.id)

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
    if score == 0:
        return {"result": "lose", "score": score}
    if score > 0:
        return {"result": "win", "score": score}
    return {"result": "push", "score": score}


async def _finalize_run(user_id: int, chat_id: int, bet: int, result: str) -> None:
    cfg = get_config(user_id)
    st = _get_state(user_id)
    initial = int(cfg["initial_bet"])
    max_allowed = int(cfg["max_allowed_bet"])

    mults = _parse_multipliers(cfg)

    if result == "win":
        mult = random.choice(mults) if mults else 2.0
    elif result == "push":
        mult = 1.0
    else:
        mult = 0.0

    if result == "win":
        winnings = int(bet * mult)
        profit = winnings - bet
        won_amount = winnings
        lost_amount = 0
    elif result == "push":
        profit = 0
        won_amount = bet
        lost_amount = 0
    else:
        profit = -bet
        won_amount = 0
        lost_amount = bet

    next_bet = compute_next_bet(bet, result, initial, max_allowed)

    if result == "win":
        new_streak = int(cfg["current_streak"]) + 1
    elif result == "lose":
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
        result=result,
        score=0,
        profit=int(profit),
        status="finished",
        finished_at=_now_iso(),
    )

    save_config(
        user_id,
        current_bet=int(next_bet),
        total_plays=int(cfg["total_plays"]) + 1,
        total_wins=int(cfg["total_wins"]) + (1 if result == "win" else 0),
        total_losses=int(cfg["total_losses"]) + (1 if result == "lose" else 0),
        total_pushes=int(cfg["total_pushes"]) + (1 if result == "push" else 0),
        total_profit=total_profit,
        total_invested=total_invested,
        total_won=total_won,
        total_lost=total_lost,
        peak_bet=peak_bet,
        peak_balance=peak_balance,
        last_multiplier=float(mult),
        last_score=0,
        last_result=result,
        current_streak=new_streak,
        best_streak=best_streak,
    )

    db.add_casino_log(
        user_id, chat_id, "info",
        f"result={result} bet={_format_amount(bet)} "
        f"mult={mult} profit={profit} next={_format_amount(next_bet)} "
        f"streak={new_streak}"
    )

    log_bot.info(
        f"casino: done uid={user_id} result={result} "
        f"bet={bet} mult={mult} profit={profit} "
        f"next={next_bet} streak={new_streak} best={best_streak} "
        f"invested={total_invested} won={total_won} lost={total_lost}"
    )


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