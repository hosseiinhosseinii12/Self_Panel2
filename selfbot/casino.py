"""Self-bot casino automator — DEBUG VERSION.

Dumps every detail of the panel's reply_markup so we can identify the
correct Wheel of Fortune button. Only clicks once, then stops.
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
        KeyboardButton,
        KeyboardButtonRow,
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


def _dump_raw_markup(panel_msg, label: str = "") -> None:
    """Log every detail of the panel's reply_markup."""
    try:
        if not panel_msg:
            log_bot.info(f"casino: [raw{label}] panel_msg is None")
            return
        if not panel_msg.reply_markup:
            log_bot.info(f"casino: [raw{label}] no reply_markup")
            return
        rm = panel_msg.reply_markup
        log_bot.info(
            f"casino: [raw{label}] msg_id={panel_msg.id} "
            f"markup_class={type(rm).__name__}"
        )
        try:
            rep = repr(rm)
            log_bot.info(f"casino: [raw{label}] repr={rep[:600]}")
        except Exception:
            pass

        for i, row in enumerate(rm.rows):
            for j, b in enumerate(row.buttons):
                btype = type(b).__name__
                btext = getattr(b, "text", None)
                bdata = getattr(b, "data", None)
                burl = getattr(b, "url", None)
                bquery = getattr(b, "query", None)
                log_bot.info(
                    f"casino: [raw{label}] btn[{i}][{j}] "
                    f"type={btype} text={btext!r} "
                    f"data={bdata!r} url={burl!r} query={bquery!r}"
                )
                try:
                    attrs = {}
                    for k in dir(b):
                        if k.startswith("_"):
                            continue
                        try:
                            v = getattr(b, k)
                        except Exception:
                            continue
                        if callable(v):
                            continue
                        attrs[k] = v
                    log_bot.info(
                        f"casino: [raw{label}] btn[{i}][{j}] attrs={attrs}"
                    )
                except Exception:
                    pass
    except Exception as e:
        log_bot.warning(f"casino: dump raw failed: {e}")


async def _click_button(client, chat_id: int, message_id: int, btn) -> bool:
    if btn is None:
        log_bot.warning("casino: click called with None button")
        return False
    try:
        btype = type(btn).__name__
        btext = getattr(btn, "text", "")
        data = getattr(btn, "data", None)
        log_bot.info(
            f"casino: [click] type={btype} text={btext!r} "
            f"data={data!r} data_len={len(data) if data else 0}"
        )

        msg = await client.get_messages(chat_id, ids=message_id)
        if msg is None:
            log_bot.warning("casino: [click] message not found")
            return False

        if data:
            try:
                await msg.click(data=data)
                log_bot.info("casino: [click] OK via msg.click(data=...)")
                return True
            except Exception as e:
                log_bot.warning(
                    f"casino: [click] data click failed: {type(e).__name__}: {e}"
                )

        if hasattr(btn, "click") and callable(getattr(btn, "click")):
            try:
                await btn.click()
                log_bot.info("casino: [click] OK via btn.click()")
                return True
            except Exception as e:
                log_bot.warning(
                    f"casino: [click] btn.click() failed: {type(e).__name__}: {e}"
                )

        if btext:
            try:
                await msg.click(text=btext)
                log_bot.info("casino: [click] OK via msg.click(text=...)")
                return True
            except Exception as e:
                log_bot.warning(
                    f"casino: [click] text click failed: {type(e).__name__}: {e}"
                )

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
                log_bot.warning(
                    f"casino: [click] raw answer failed: {type(e).__name__}: {e}"
                )

        log_bot.error("casino: [click] all methods failed")
        return False
    except Exception as e:
        log_bot.warning(
            f"casino: [click] FAILED {type(e).__name__}: {e}"
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
    except Exception as e:
        log_bot.warning(f"casino: late listener install failed: {e}")

    st = _get_state(user_id)
    async with st.lock:
        st.chat_id = chat_id
        st.bet_amount = bet
        st.run_id = _rand_id()
        st.stage = "starting"
        st.started_at = time.time()
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

    try:
        result = await _play_round(runtime, client, user_id, chat_id, bet, st)
        await _finalize_run(user_id, chat_id, bet, result)
    except asyncio.TimeoutError as e:
        db.update_casino_run(st.run_id, status="timeout",
                              error=str(e), finished_at=_now_iso())
    except Exception as e:
        db.update_casino_run(st.run_id, status="error",
                              error=f"{type(e).__name__}: {e}",
                              finished_at=_now_iso())
        log_bot.error(f"casino: cycle exception: {traceback.format_exc()}")


async def _play_round(runtime, client, user_id: int, chat_id: int,
                       bet: int, st: CasinoState) -> str:
    try:
        # ---------- 1. Send "Casino" ----------
        log_bot.info(f"casino: [1] sending 'Casino' uid={user_id}")
        sent_msg = await client.send_message(chat_id, "Casino")
        sent_msg_id = sent_msg.id
        log_bot.info(f"casino: [1] sent (msg_id={sent_msg_id})")

        await asyncio.sleep(3.0)

        # ---------- 2. Find panel ----------
        panel_msg = None
        if st.last_panel_id:
            cached = await _get_message(client, chat_id, st.last_panel_id)
            if _msg_has_clickable(cached):
                panel_msg = cached
                log_bot.info(f"casino: [2] using cached panel id={cached.id}")

        if panel_msg is None:
            for attempt in range(1, 11):
                await asyncio.sleep(2.0)
                panel_msg = await _find_panel_after_message(
                    client, chat_id, sent_msg_id
                )
                if panel_msg is not None:
                    log_bot.info(f"casino: [2] found panel on attempt {attempt}")
                    break

        if panel_msg is None:
            raise TimeoutError("panel not found")

        st.panel_msg_id = panel_msg.id
        st.last_panel_id = panel_msg.id
        save_config(user_id, panel_msg_id=panel_msg.id)
        log_bot.info(f"casino: [2] panel msg id={panel_msg.id}")

        # ---------- DUMP raw markup ----------
        _dump_raw_markup(panel_msg, label="initial")

        # ---------- 3. Click WHEEL OF FORTUNE ----------
        log_bot.info("casino: [3] looking for wheel button")

        btn_by_text = None
        btn_by_pos_00 = None
        btn_by_pos_01 = None
        btn_by_pos_02 = None
        btn_by_pos_10 = None

        try:
            rm = panel_msg.reply_markup
            for i, row in enumerate(rm.rows):
                for j, b in enumerate(row.buttons):
                    btext = (getattr(b, "text", "") or "").lower()
                    # match by text patterns
                    if (re.search(r"wheel\s*of\s*fortune", btext)
                            or "ᴡʜᴇᴇʟ" in btext
                            or "ꜰᴏʀᴛᴜɴᴇ" in btext
                            or "fortune" in btext):
                        btn_by_text = b
                        log_bot.info(
                            f"casino: [3] matched WHEEL by text at [{i}][{j}]"
                        )
                    # capture positions
                    if i == 0 and j == 0:
                        btn_by_pos_00 = b
                    if i == 0 and j == 1:
                        btn_by_pos_01 = b
                    if i == 0 and j == 2:
                        btn_by_pos_02 = b
                    if i == 1 and j == 0:
                        btn_by_pos_10 = b
        except Exception as e:
            log_bot.warning(f"casino: [3] enumerate failed: {e}")

        # Decide which button to click
        target_btn = None
        target_label = ""
        if btn_by_text is not None:
            target_btn = btn_by_text
            target_label = "by-text"
        elif btn_by_pos_01 is not None:
            target_btn = btn_by_pos_01
            target_label = "[0][1]"
        elif btn_by_pos_00 is not None:
            target_btn = btn_by_pos_00
            target_label = "[0][0]"

        if target_btn is None:
            log_bot.error("casino: [3] no button found to click")
            raise TimeoutError("no button")

        log_bot.info(
            f"casino: [3] clicking {target_label} "
            f"text={getattr(target_btn, 'text', '?')!r} "
            f"data={getattr(target_btn, 'data', None)!r}"
        )
        ok = await _click_button(client, chat_id, panel_msg.id, target_btn)
        log_bot.info(f"casino: [3] click returned {ok}")
        await asyncio.sleep(3.0)

        # ---------- Log post-click markup ----------
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        _dump_raw_markup(panel_msg, label="after-click")

        log_bot.info("casino: [debug] stopping after click for inspection")
        return "debug"

    except Exception as e:
        log_bot.error(
            f"casino: play_round FAILED: {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        raise


async def _find_panel_after_message(client, chat_id: int, sent_msg_id: int):
    try:
        msgs = await client.get_messages(chat_id, limit=50)
        for m in msgs:
            reply_to = getattr(m, "reply_to_msg_id", None)
            if reply_to == sent_msg_id and _msg_has_clickable(m):
                return m
        best = None
        for m in msgs:
            if m.id <= sent_msg_id:
                continue
            if _msg_has_clickable(m):
                if best is None or m.id > best.id:
                    best = m
        if best is not None:
            return best
        return await _find_latest_panel(client, chat_id)
    except Exception as e:
        log_bot.warning(f"casino: find panel failed: {e}")
        return None


async def _find_latest_panel(client, chat_id: int):
    try:
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


async def _finalize_run(user_id: int, chat_id: int, bet: int,
                         result: str) -> None:
    cfg = get_config(user_id)
    st = _get_state(user_id)
    save_config(user_id, total_plays=cfg["total_plays"] + 1)
    db.update_casino_run(
        st.run_id,
        result=result,
        status="finished",
        finished_at=_now_iso(),
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