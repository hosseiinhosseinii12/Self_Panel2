"""Text formatting engine — bold/italic/underline/strike/spoiler auto-edit.

When enabled, every outgoing message from the user gets re-edited with the
selected Telegram markdown/HTML formatting.
"""
import asyncio
from typing import Optional

from .logging_setup import log_bot
from . import db


FORMAT_PAIRS = {
    "bold":      ("**", "**"),
    "italic":    ("__", "__"),
    "underline": ("<u>", "</u>"),
    "strike":    ("~~", "~~"),
    "spoiler":   ("||", "||"),
}

FORMAT_LABELS = {
    "bold":      "Bold",
    "italic":    "Italic",
    "underline": "Underline",
    "strike":    "Strike",
    "spoiler":   "Spoiler",
}


def get_settings(user_id: int) -> dict:
    s = db.get_text_format(user_id) or {}
    return {
        "bold": bool(s.get("bold", 0)),
        "italic": bool(s.get("italic", 0)),
        "underline": bool(s.get("underline", 0)),
        "strike": bool(s.get("strike", 0)),
        "spoiler": bool(s.get("spoiler", 0)),
        "chats_filter": s.get("chats_filter", ""),
    }


def any_enabled(user_id: int) -> bool:
    s = get_settings(user_id)
    return any(s[k] for k in FORMAT_PAIRS.keys())


def active_list(user_id: int) -> list:
    s = get_settings(user_id)
    return [FORMAT_LABELS[k] for k in FORMAT_PAIRS.keys() if s.get(k)]


def toggle(user_id: int, key: str) -> bool:
    if key not in FORMAT_PAIRS:
        return False
    current = get_settings(user_id)
    new_val = not current.get(key, False)
    db.upsert_text_format(user_id, **{key: 1 if new_val else 0})
    log_bot.info(f"text_format uid={user_id} {key}={new_val}")
    return new_val


def set_filter(user_id: int, chats_filter: str) -> None:
    db.upsert_text_format(user_id, chats_filter=(chats_filter or "")[:500])


def apply_format(text: str, user_id: int) -> Optional[str]:
    """Return text wrapped with the enabled formats, or None if no change needed."""
    s = get_settings(user_id)
    if not any(s[k] for k in FORMAT_PAIRS.keys()):
        return None
    if not text or not text.strip():
        return None
    result = text
    for key in ("bold", "italic", "underline", "strike", "spoiler"):
        if s.get(key):
            op, cl = FORMAT_PAIRS[key]
            result = f"{op}{result}{cl}"
    return result


def _chat_allowed(settings: dict, chat_id: int) -> bool:
    """Empty filter = all chats. Comma-separated IDs = whitelist."""
    flt = (settings.get("chats_filter") or "").strip()
    if not flt:
        return True
    allowed = set()
    for part in flt.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            allowed.add(int(part))
        except ValueError:
            continue
    return int(chat_id) in allowed


async def handle_own_message(runtime, user_id: int, chat_id: int,
                              message_id: int, text: str) -> bool:
    """Called for every outgoing message from the user.
    Edits the message with the enabled formatting if any.
    Returns True if the message was edited.
    """
    if not any_enabled(user_id):
        return False

    settings = get_settings(user_id)
    if not _chat_allowed(settings, chat_id):
        return False

    formatted = apply_format(text, user_id)
    if not formatted or formatted == text:
        return False

    try:
        client = runtime.sender_for(user_id)
        if client is None:
            return False
        await client.edit_message(
            chat_id, message_id, formatted,
            parse_mode="html",
        )
        log_bot.info(
            f"text_format applied uid={user_id} chat={chat_id} msg={message_id}"
        )
        try:
            db.incr_counter(user_id, "text_format_edits", 1)
        except Exception:
            pass
        return True
    except Exception as e:
        log_bot.warning(f"text_format edit failed: {e}")
        return False