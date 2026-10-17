"""HTML + PDF export for casino run logs."""
import io
from datetime import datetime, timezone, timedelta
from typing import Optional

from .logging_setup import log_flask
from . import db


TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def _to_tehran(iso_str, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    try:
        if not iso_str:
            return "—"
        s = str(iso_str).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TEHRAN_TZ).strftime(fmt)
    except Exception:
        return str(iso_str or "—")[:19]


def filter_runs(runs, filter_mode: str):
    """Filter a list of run dicts by outcome.

    filter_mode:
        all         -> all runs
        win         -> only wins
        lose        -> only losses
        push        -> only pushes (1x)
        win_lose    -> wins + losses
        win_push    -> wins + pushes
        lose_push   -> losses + pushes
    """
    mode = (filter_mode or "all").lower().strip()
    if mode == "all":
        return list(runs)
    if mode == "win":
        return [r for r in runs if (r.get("result") or "") == "win"]
    if mode == "lose":
        return [r for r in runs if (r.get("result") or "") == "lose"]
    if mode == "push":
        return [r for r in runs if (r.get("result") or "") == "push"]
    if mode == "win_lose":
        return [r for r in runs if (r.get("result") or "") in ("win", "lose")]
    if mode == "win_push":
        return [r for r in runs if (r.get("result") or "") in ("win", "push")]
    if mode == "lose_push":
        return [r for r in runs if (r.get("result") or "") in ("lose", "push")]
    return list(runs)


def compute_stats(runs):
    """Compute stats for a list of runs."""
    total = len(runs)
    wins = sum(1 for r in runs if r.get("result") == "win")
    losses = sum(1 for r in runs if r.get("result") == "lose")
    pushes = sum(1 for r in runs if r.get("result") == "push")
    timeouts = sum(1 for r in runs if r.get("status") == "timeout")
    net = sum(int(r.get("profit") or 0) for r in runs)
    invested = sum(int(r.get("bet_amount") or 0) for r in runs)
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "timeouts": timeouts,
        "net": net,
        "invested": invested,
        "win_rate": (wins / total * 100) if total else 0,
    }


def _fmt_num(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n or "0")


def _fmt_signed(n) -> str:
    try:
        v = int(n)
        return f"+{v:,}" if v >= 0 else f"{v:,}"
    except Exception:
        return str(n or "0")


# ===========================================================================
# HTML render
# ===========================================================================
HTML_TEMPLATE = """<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<title>Casino Log — {filter_label}</title>
<style>
:root {{
  --bg: #0a0a0c;
  --surface: #131318;
  --surface-2: #17171d;
  --text: #f5f5f7;
  --text-2: #a8a8b0;
  --text-3: #78787f;
  --border: #23232a;
  --green: #22c55e;
  --green-bg: #0f2418;
  --red: #ef4444;
  --red-bg: #2a1215;
  --amber: #f59e0b;
  --amber-bg: #2a1e0a;
  --mono: 'JetBrains Mono', ui-monospace, Menlo, monospace;
}}

* {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 13px;
  line-height: 1.5;
  padding: 32px;
  letter-spacing: -0.008em;
}}

.header {{
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 24px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}}

.title {{
  font-size: 26px;
  font-weight: 800;
  letter-spacing: -0.04em;
  background: linear-gradient(180deg, var(--text) 0%, var(--text-2) 120%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}}

.subtitle {{
  color: var(--text-2);
  font-size: 13px;
  margin-top: 4px;
}}

.meta {{
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: right;
  font-size: 11px;
  color: var(--text-3);
}}

.meta strong {{
  color: var(--text);
  font-weight: 600;
  font-family: var(--mono);
}}

.stats {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 10px;
  margin-bottom: 24px;
}}

.stat {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
}}

.stat .k {{
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--text-3);
}}

.stat .v {{
  font-size: 20px;
  font-weight: 800;
  letter-spacing: -0.03em;
  margin-top: 4px;
  font-variant-numeric: tabular-nums;
}}

.stat .v.green {{ color: var(--green); }}
.stat .v.red {{ color: var(--red); }}
.stat .v.amber {{ color: var(--amber); }}

table {{
  width: 100%;
  border-collapse: collapse;
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  background: var(--surface);
}}

thead th {{
  background: var(--surface-2);
  color: var(--text-2);
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  text-align: left;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}}

tbody td {{
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}}

tbody tr:last-child td {{ border-bottom: none; }}

td.mono, .mono {{ font-family: var(--mono); font-variant-numeric: tabular-nums; }}
td.right {{ text-align: right; }}
td.muted {{ color: var(--text-2); }}

.badge {{
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 600;
  line-height: 1.5;
  white-space: nowrap;
}}
.badge.win {{ background: var(--green-bg); color: var(--green); }}
.badge.lose {{ background: var(--red-bg); color: var(--red); }}
.badge.push {{ background: var(--amber-bg); color: var(--amber); }}
.badge.timeout {{ background: var(--amber-bg); color: var(--amber); }}
.badge.other {{ background: var(--surface-2); color: var(--text-2); }}

.profit-pos {{ color: var(--green); font-weight: 600; }}
.profit-neg {{ color: var(--red); font-weight: 600; }}
.profit-zero {{ color: var(--text-2); }}

.footer {{
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
  color: var(--text-3);
  font-size: 11px;
  text-align: center;
}}

.empty {{
  padding: 40px;
  text-align: center;
  color: var(--text-3);
}}
</style>
</head>
<body>

<div class="header">
  <div>
    <div class="title">Casino Log</div>
    <div class="subtitle">{filter_label}</div>
  </div>
  <div class="meta">
    <span>Generated: <strong>{generated_at}</strong></span>
    <span>Timezone: <strong>Asia/Tehran (UTC+3:30)</strong></span>
    <span>Runs: <strong>{total}</strong></span>
  </div>
</div>

<div class="stats">
  <div class="stat">
    <div class="k">Total runs</div>
    <div class="v">{total}</div>
  </div>
  <div class="stat">
    <div class="k">Wins</div>
    <div class="v green">{wins}</div>
  </div>
  <div class="stat">
    <div class="k">Losses</div>
    <div class="v red">{losses}</div>
  </div>
  <div class="stat">
    <div class="k">Pushes (1x)</div>
    <div class="v amber">{pushes}</div>
  </div>
  <div class="stat">
    <div class="k">Win rate</div>
    <div class="v">{win_rate:.1f}%</div>
  </div>
  <div class="stat">
    <div class="k">Net profit</div>
    <div class="v {net_class}">{net}</div>
  </div>
</div>

<table>
  <thead>
    <tr>
      <th>#</th>
      <th>When (Tehran)</th>
      <th>Bet</th>
      <th>Result</th>
      <th>Mult</th>
      <th>Score</th>
      <th class="right">Profit</th>
    </tr>
  </thead>
  <tbody>
    {rows}
  </tbody>
</table>

<div class="footer">
  SELF BOT · Casino automator · {total} runs · exported {generated_at}
</div>

</body>
</html>
"""


def _row_html(idx: int, r: dict) -> str:
    result = (r.get("result") or "").strip() or r.get("status", "")
    result_cls = "win" if result == "win" else \
                 "lose" if result == "lose" else \
                 "push" if result == "push" else \
                 "timeout" if result == "timeout" else "other"

    profit = int(r.get("profit") or 0)
    profit_cls = "profit-pos" if profit > 0 else \
                 "profit-neg" if profit < 0 else "profit-zero"

    mult = r.get("multiplier") or 0
    try:
        mult_str = f"{float(mult):g}x"
    except Exception:
        mult_str = "—"

    started = _to_tehran(r.get("started_at"))
    bet = _fmt_num(r.get("bet_amount"))
    score = r.get("score", 0)
    profit_str = _fmt_signed(profit)
    result_display = result or "—"

    return f"""<tr>
      <td class="mono muted">{idx}</td>
      <td class="mono">{started}</td>
      <td class="mono">{bet}</td>
      <td><span class="badge {result_cls}">{result_display}</span></td>
      <td class="mono">{mult_str}</td>
      <td class="mono">{score}</td>
      <td class="mono right {profit_cls}">{profit_str}</td>
    </tr>"""


def render_html(user_id: int, filter_mode: str = "all",
                limit: int = 500) -> str:
    runs = db.list_casino_runs(user_id, limit=limit)
    filtered = filter_runs(runs, filter_mode)
    stats = compute_stats(filtered)

    label_map = {
        "all": "All runs",
        "win": "Wins only",
        "lose": "Losses only",
        "push": "Pushes only (1x)",
        "win_lose": "Wins + Losses",
        "win_push": "Wins + Pushes",
        "lose_push": "Losses + Pushes",
    }
    filter_label = label_map.get(filter_mode, "All runs")

    rows_html = "\n".join(
        _row_html(i + 1, r) for i, r in enumerate(filtered)
    ) or '<tr><td colspan="7" class="empty">No runs in this filter.</td></tr>'

    net_class = "green" if stats["net"] > 0 else "red" if stats["net"] < 0 else ""

    return HTML_TEMPLATE.format(
        filter_label=filter_label,
        generated_at=_to_tehran(
            datetime.now(timezone.utc).isoformat()
        ),
        total=stats["total"],
        wins=stats["wins"],
        losses=stats["losses"],
        pushes=stats["pushes"],
        win_rate=stats["win_rate"],
        net=_fmt_signed(stats["net"]),
        net_class=net_class,
        rows=rows_html,
    )


# ===========================================================================
# PDF render
# ===========================================================================
def render_pdf(user_id: int, filter_mode: str = "all",
               limit: int = 500) -> bytes:
    """Render as PDF using xhtml2pdf (pure Python, works on Railway)."""
    html = render_html(user_id, filter_mode, limit)

    try:
        from xhtml2pdf import pisa
    except ImportError:
        log_flask.error("xhtml2pdf not installed - cannot export PDF")
        raise RuntimeError(
            "PDF export unavailable. Add 'xhtml2pdf' to requirements.txt."
        )

    buf = io.BytesIO()
    try:
        pisa.CreatePDF(
            src=html,
            dest=buf,
            encoding="utf-8",
        )
        data = buf.getvalue()
        buf.close()
        return data
    except Exception as e:
        log_flask.error(f"PDF render failed: {e}")
        raise