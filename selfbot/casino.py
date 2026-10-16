"""Self-bot casino automator — plays Meowie Casino's Wheel of Fortune.

Strategy (Martingale with 5M cap):
  - Start: 100,000
  - On loss: bet *= 3
  - On win: reset to 100,000
  - Cap: 5,000,000 (bot rejects anything above)
  - Any computed bet above cap → rounded down to 5,000,000

Ledger (per user):
  - total_invested: sum of all bets placed
  - total_won: sum of winning payouts (bet * multiplier)
  - total_lost: sum of losing bets
  - total_profit: won - lost
  - peak_balance: max(total_won - total_lost)
  - current_streak: consecutive wins
  - best_streak: longest consecutive wins
  - last_multiplier, last_score, last_result
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
    from telethon.tl.types import (
        ReplyInlineMarkup,
        KeyboardButtonCallback,
        KeyboardButtonUrl,
        MessageEntityCustomEmoji,
        MessageEntityTextUrl,
    )
    from telethon.extensions import html as tl_html
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False
    MessageEntityCustomEmoji = None
    MessageEntityTextUrl = None
    tl_html = None


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
# Custom HTML Parser — supports <tg-emoji> and <a href="emoji/ID">
# ===========================================================================
if HAS_TELETHON:
    class CustomHtml:
        """HTML parse mode that understands custom emoji tags.

        Supports two syntaxes:
          - <tg-emoji emoji-id="123456">🎰</tg-emoji>
          - <a href="emoji/123456">🎰</a>
        """
        @staticmethod
        def parse(text):
            text, entities = tl_html.parse(text)
            for i, e in enumerate(entities):
                if isinstance(e, MessageEntityTextUrl):
                    url = e.url or ""
                    if url.startswith("emoji/"):
                        try:
                            doc_id = int(url.split("/", 1)[1])
                        except (ValueError, IndexError):
                            continue
                        entities[i] = MessageEntityCustomEmoji(
                            offset=e.offset,
                            length=e.length,
                            document_id=doc_id,
                        )
            return text, entities

        @staticmethod
        def unparse(text, entities):
            return tl_html.unparse(text, entities)
else:
    CustomHtml = None


# ===========================================================================
# Custom Emoji ID Cache
# ===========================================================================
_CUSTOM_EMOJI_CACHE: dict = {}      # user_id -> int document_id
_PREMIUM_LOGGED: set = set()        # user_ids we already logged premium status for


async def _log_account_status(client, user_id: int) -> None:
    """Log whether the account is Premium (only once per user)."""
    if int(user_id) in _PREMIUM_LOGGED:
        return
    try:
        me = await client.get_me()
        is_premium = bool(getattr(me, "premium", False))
        log_bot.info(
            f"casino: account uid={user_id} "
            f"username=@{getattr(me, 'username', None)} "
            f"premium={is_premium}"
        )
        if not is_premium:
            log_bot.warning(
                f"casino: ⚠️ uid={user_id} is NOT Telegram Premium. "
                f"Animated custom emoji will be downgraded to plain unicode "
                f"by the server. Upgrade is required to send animated 🎰."
            )
        _PREMIUM_LOGGED.add(int(user_id))
    except Exception as e:
        log_bot.warning(f"casino: could not read account status: {e}")


async def find_wheel_custom_emoji_id(client, user_id: int) -> Optional[int]:
    """Resolve the animated 🎰 custom emoji document_id via the Telegram API.

    messages.SearchCustomEmoji returns an EmojiList whose `document_id` field
    is a Vector<long> (a list). We must take the first element.
    Result is cached per user_id.
    """
    cached = _CUSTOM_EMOJI_CACHE.get(int(user_id))
    if cached:
        return int(cached)

    if not HAS_TELETHON:
        return None

    try:
        me = await client.get_me()
        access_hash = getattr(me, "access_hash", 0) or 0

        # Try the modern signature first
        try:
            result = await client(functions.messages.SearchCustomEmojiRequest(
                emoticon=EMOJI_TRIGGER,
                hash=access_hash,
            ))
        except TypeError:
            result = await client(functions.messages.SearchCustomEmojiRequest(
                emoticon=EMOJI_TRIGGER,
            ))

        doc_id = None
        if result is not None:
            raw = getattr(result, "document_id", None)
            # Vector<long> case — most common
            if isinstance(raw, (list, tuple)):
                if raw:
                    doc_id = int(raw[0])
            elif raw:
                try:
                    doc_id = int(raw)
                except (TypeError, ValueError):
                    doc_id = None

            # Fallback: look in .documents (older response shapes)
            if not doc_id:
                docs = getattr(result, "documents", None) or []
                for d in docs:
                    did = getattr(d, "id", None)
                    if did:
                        doc_id = int(did)
                        break

        # Debug: dump what the API returned
        try:
            log_bot.info(
                f"casino: searchCustomEmoji 🎰 raw result="
                f"{result.stringify()[:300] if result else 'None'}"
            )
        except Exception:
            pass

        if doc_id:
            _CUSTOM_EMOJI_CACHE[int(user_id)] = int(doc_id)
            log_bot.info(
                f"casino: resolved animated 🎰 doc_id={doc_id} "
                f"for uid={user_id}"
            )
            return int(doc_id)

        log_bot.warning(
            f"casino: searchCustomEmoji returned no doc_id for 🎰 "
            f"(account may lack access)"
        )
        return None
    except Exception as e:
        log_bot.warning(f"casino: searchCustomEmoji 🎰 failed: {e}")
        return None


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


def _debug_entities(msg, label: str = "") -> None:
    try:
        if not msg:
            return
        text = msg.message or ""
        log_bot.info(f"casino: [ent{label}] msg_id={msg.id} text={text[:200]!r}")
        if not msg.entities:
            log_bot.info(f"casino: [ent{label}] no entities")
            return
        for i, ent in enumerate(msg.entities):
            etype = type(ent).__name__
            offset = getattr(ent, "offset", None)
            length = getattr(ent, "length", None)
            doc_id = getattr(ent, "document_id", None)
            emoji_char = ""
            try:
                if text and offset is not None and length is not None:
                    emoji_char = text[offset:offset + length]
            except Exception:
                pass
            log_bot.info(
                f"casino: [ent{label}] ent[{i}] type={etype} "
                f"offset={offset} length={length} "
                f"document_id={doc_id} emoji={emoji_char!r}"
            )
    except Exception as e:
        log_bot.warning(f"casino: debug entities failed: {e}")


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


def _extract_custom_emoji_from_message(msg, prefer_emoji: str = EMOJI_TRIGGER) -> Optional[dict]:
    """Extract a custom emoji entity from a message.

    Prefers the entity whose visible slice equals `prefer_emoji`,
    otherwise the one whose slice contains it, otherwise the first one.

    Returns dict with: document_id, offset, length, raw.
    """
    try:
        if not msg or not msg.entities:
            return None
        text = msg.message or ""
        candidates = []
        for ent in msg.entities:
            if type(ent).__name__ != "MessageEntityCustomEmoji":
                continue
            doc_id = getattr(ent, "document_id", None)
            if not doc_id:
                continue
            offset = getattr(ent, "offset", 0) or 0
            length = getattr(ent, "length", 0) or 0
            try:
                slice_text = text[offset:offset + length] if length else ""
            except Exception:
                slice_text = ""
            candidates.append({
                "document_id": int(doc_id),
                "offset": int(offset),
                "length": int(length),
                "raw": slice_text,
            })
        if not candidates:
            return None

        for c in candidates:
            if c["raw"] == prefer_emoji:
                return c
        for c in candidates:
            if prefer_emoji and prefer_emoji in (c["raw"] or ""):
                return c
        return candidates[0]
    except Exception as e:
        log_bot.warning(f"casino: extract custom emoji failed: {e}")
        return None


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


async def _send_animated_emoji(client, chat_id: int, emoji_char: str,
                                doc_id: int, reply_to: int) -> bool:
    """Send an animated custom emoji as a reply.

    Uses the CustomHtml parser first (most reliable across Telethon versions),
    then falls back to explicitly constructing MessageEntityCustomEmoji.
    """
    if not emoji_char or not doc_id:
        return False

    # --- Attempt 1: HTML with <tg-emoji> tag ---
    if HAS_TELETHON and CustomHtml is not None:
        try:
            prev_parse = getattr(client, "parse_mode", None)
            client.parse_mode = CustomHtml()
            html_text = (
                f'<tg-emoji emoji-id="{int(doc_id)}">{emoji_char}</tg-emoji>'
            )
            await client.send_message(
                chat_id, html_text, reply_to=reply_to,
            )
            log_bot.info(
                f"casino: [emoji] OK via HTML tg-emoji doc_id={doc_id}"
            )
            client.parse_mode = prev_parse
            return True
        except Exception as e:
            log_bot.warning(f"casino: [emoji] HTML tg-emoji failed: {e}")
            try:
                client.parse_mode = prev_parse  # restore
            except Exception:
                pass

    # --- Attempt 2: HTML with <a href="emoji/ID"> ---
    if HAS_TELETHON and CustomHtml is not None:
        try:
            prev_parse = getattr(client, "parse_mode", None)
            client.parse_mode = CustomHtml()
            html_text = (
                f'<a href="emoji/{int(doc_id)}">{emoji_char}</a>'
            )
            await client.send_message(
                chat_id, html_text, reply_to=reply_to,
            )
            log_bot.info(
                f"casino: [emoji] OK via HTML a-emoji doc_id={doc_id}"
            )
            client.parse_mode = prev_parse
            return True
        except Exception as e:
            log_bot.warning(f"casino: [emoji] HTML a-emoji failed: {e}")
            try:
                client.parse_mode = prev_parse
            except Exception:
                pass

    # --- Attempt 3: explicit MessageEntityCustomEmoji ---
    if MessageEntityCustomEmoji is None:
        return False
    try:
        length = len(emoji_char.encode("utf-16-le")) // 2
        if length <= 0:
            return False
        entity = MessageEntityCustomEmoji(
            offset=0,
            length=length,
            document_id=int(doc_id),
        )
        await client.send_message(
            chat_id,
            emoji_char,
            formatting_entities=[entity],
            reply_to=reply_to,
        )
        log_bot.info(
            f"casino: [emoji] OK via entity doc_id={doc_id}"
        )
        return True
    except Exception as e:
        log_bot.warning(f"casino: [emoji] entity failed: {e}")
        return False


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

    # Log account status once per user
    try:
        await _log_account_status(client, user_id)
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
        # 8. Send animated 🎰 custom emoji
        # -------------------------------------------------------------------
        log_bot.info("casino: [8] sending animated 🎰 custom emoji")
        panel_msg = await _get_message(client, chat_id, panel_msg.id)
        _debug_entities(panel_msg, "before-emoji-send")

        sent = False

        # Priority 1: official animated 🎰 via SearchCustomEmoji
        doc_id = await find_wheel_custom_emoji_id(client, user_id)
        if doc_id:
            sent = await _send_animated_emoji(
                client, chat_id, EMOJI_TRIGGER, doc_id, panel_msg.id,
            )
            if not sent:
                # Clear cache to retry lookup next cycle
                _CUSTOM_EMOJI_CACHE.pop(int(user_id), None)

        # Priority 2: custom emoji entity present in the panel message
        if not sent:
            custom = _extract_custom_emoji_from_message(
                panel_msg, prefer_emoji=EMOJI_TRIGGER,
            )
            if custom and custom.get("document_id"):
                log_bot.info(
                    f"casino: [8] trying panel-derived doc_id="
                    f"{custom['document_id']} raw={custom.get('raw')!r}"
                )
                sent = await _send_animated_emoji(
                    client, chat_id, EMOJI_TRIGGER,
                    custom["document_id"], panel_msg.id,
                )
                if not sent and custom.get("raw") and custom["raw"] != EMOJI_TRIGGER:
                    sent = await _send_animated_emoji(
                        client, chat_id, custom["raw"],
                        custom["document_id"], panel_msg.id,
                    )

        # Priority 3: scan recent messages for any custom 🎰 entity
        if not sent:
            log_bot.info("casino: [8] scanning recent msgs for custom 🎰")
            try:
                recent = await client.get_messages(chat_id, limit=15)
                for m in recent:
                    if not m or not m.entities:
                        continue
                    c2 = _extract_custom_emoji_from_message(
                        m, prefer_emoji=EMOJI_TRIGGER,
                    )
                    if not c2 or not c2.get("document_id"):
                        continue
                    log_bot.info(
                        f"casino: [8] found in msg_id={m.id} "
                        f"doc_id={c2['document_id']} raw={c2.get('raw')!r}"
                    )
                    if await _send_animated_emoji(
                        client, chat_id, EMOJI_TRIGGER,
                        c2["document_id"], panel_msg.id,
                    ):
                        sent = True
                        break
                    if c2.get("raw") and c2["raw"] != EMOJI_TRIGGER:
                        if await _send_animated_emoji(
                            client, chat_id, c2["raw"],
                            c2["document_id"], panel_msg.id,
                        ):
                            sent = True
                            break
            except Exception as e:
                log_bot.warning(f"casino: [8] scan failed: {e}")

        # Final fallback: plain unicode emoji
        if not sent:
            log_bot.warning(
                "casino: [8] all custom emoji attempts failed, "
                "falling back to plain unicode 🎰"
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