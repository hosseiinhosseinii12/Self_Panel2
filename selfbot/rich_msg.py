"""Rich message renderer — always uses Markdown fallback (Telethon has no
native rich-message endpoint)."""
from typing import List, Optional

from .logging_setup import log_bot


def _escape_html(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_rich_html(title: str, subtitle: str = "",
                    rows: Optional[List[List[str]]] = None,
                    footer: str = "") -> str:
    parts = [f"<h1>{_escape_html(title)}</h1>"]
    if subtitle:
        parts.append(f"<p>{_escape_html(subtitle)}</p>")
    if rows:
        parts.append("<table bordered striped>")
        for row in rows:
            parts.append("<tr>" + "".join(
                f"<td>{_escape_html(c)}</td>" for c in row) + "</tr>")
        parts.append("</table>")
    if footer:
        parts.append(f"<footer>{_escape_html(footer)}</footer>")
    return "\n".join(parts)


def build_markdown_fallback(title: str, subtitle: str = "",
                            rows: Optional[List[List[str]]] = None,
                            footer: str = "") -> str:
    lines = [f"# {title}"]
    if subtitle:
        lines.append("")
        lines.append(f"_{subtitle}_")
    if rows:
        lines.append("")
        lines.append(_unicode_table(rows))
    if footer:
        lines.append("")
        lines.append(f"— {footer}")
    return "\n".join(lines)


def _unicode_table(rows: List[List[str]]) -> str:
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    cols: List[List[str]] = []
    for c in range(ncols):
        cell_vals = [(r[c] if c < len(r) else "") for r in rows]
        w = max(len(str(v)) for v in cell_vals)
        cols.append([str(v).ljust(w) for v in cell_vals])

    top = "┌" + "┬".join("─" * (len(c[0]) + 2) for c in cols) + "┐"
    sep = "├" + "┼".join("─" * (len(c[0]) + 2) for c in cols) + "┤"
    bot = "└" + "┴".join("─" * (len(c[0]) + 2) for c in cols) + "┘"

    out = [top]
    for i in range(len(rows)):
        row = "│" + "│".join(f" {c[i]} " for c in cols) + "│"
        out.append(row)
        if i == 0 and len(rows) > 1:
            out.append(sep)
    out.append(bot)
    return "```\n" + "\n".join(out) + "\n```"


async def send_rich(
    client,
    chat_id: int,
    title: str,
    subtitle: str = "",
    rows: Optional[List[List[str]]] = None,
    footer: str = "",
    fallback_markdown: Optional[str] = None,
):
    md = fallback_markdown or build_markdown_fallback(title, subtitle, rows, footer)
    try:
        return await client.send_message(chat_id, md, parse_mode="md")
    except Exception as e:
        log_bot.warning(f"markdown send failed, plain text fallback: {e}")
        return await client.send_message(chat_id, md)