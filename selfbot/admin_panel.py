"""Flask admin panel — modern dark-first design system."""
import functools
import os
import secrets
import string
from datetime import datetime

from flask import (Flask, abort, flash, redirect, render_template_string,
                   request, send_file, session, url_for)

from .audit import all_entries as audit_all, record as audit_record
from .config import CONFIG, DB_PATH, save_config
from .economy import (get_balance, grant, hourly_consumption, set_balance,
                      spend, total_in_circulation)
from .logging_setup import log_flask
from .rate_limit import ip_limiter
from .requests_mod import (all_requests, approval_rate_series, approve,
                           pending, reject)
from .shop import all_items as shop_items, delete_item, set_item
from .subscriptions import PLANS, cancel_sub, days_left, distribution, \
    effective_plan, get_sub, set_sub
from .transactions import for_user as tx_for_user, recent as tx_recent, \
    top_spenders, totals as tx_totals
from .users import (active_last_24h, all_users, ban, count as user_count,
                    delete_user, get_user, new_last_7d, unban)

try:
    import pyotp
    HAS_PYOTP = True
except ImportError:
    HAS_PYOTP = False

try:
    import qrcode  # type: ignore
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


# ===========================================================================
# Admin password
# ===========================================================================
def _load_password() -> str:
    env_pw = os.environ.get("ADMIN_PASSWORD")
    if env_pw:
        return env_pw
    pw = CONFIG.get("admin_password")
    if pw:
        return pw
    pw = "".join(secrets.choice(string.ascii_letters + string.digits)
                 for _ in range(16))
    CONFIG["admin_password"] = pw
    try:
        save_config(CONFIG)
    except Exception:
        pass
    log_flask.warning(
        f"ADMIN_PASSWORD not set — generated one: {pw}  "
        "(set ADMIN_PASSWORD env var in production!)"
    )
    return pw


ADMIN_PASSWORD = _load_password()


# ===========================================================================
# Auth helpers
# ===========================================================================
def _login_required(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper


def _rate_limit(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        ip = request.remote_addr or "0.0.0.0"
        if not ip_limiter.allow(ip):
            return ("Too Many Requests", 429)
        return fn(*a, **kw)
    return wrapper


def _csrf_token() -> str:
    tok = session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        session["csrf"] = tok
    return tok


def _csrf_check() -> None:
    tok = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
    if not tok or tok != session.get("csrf"):
        abort(400, "CSRF check failed")


def _current_admin_id() -> int:
    return int(session.get("admin_id", 0) or 0)


def _current_admin_name() -> str:
    return session.get("admin_name", "") or ""


# ===========================================================================
# BASE TEMPLATE
# ===========================================================================
BASE_TEMPLATE = r"""
<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<title>{{ title }} · SELF BOT</title>

<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>

<style>
:root {
  --bg: #0a0a0c;
  --bg-2: #101014;
  --surface: #131318;
  --surface-2: #17171d;
  --surface-3: #1c1c23;
  --surface-elev: #1a1a20;

  --text: #f5f5f7;
  --text-2: #a8a8b0;
  --text-3: #78787f;
  --text-4: #5a5a60;

  --border: #23232a;
  --border-2: #2d2d35;
  --border-3: #3a3a43;

  --hover: #1c1c22;
  --active: #22222a;
  --focus-ring: rgba(245, 245, 247, 0.08);

  --accent: #f5f5f7;
  --accent-fg: #0a0a0c;

  --green: #22c55e;
  --green-bg: #0f2418;
  --green-border: #1a4a2e;
  --red: #ef4444;
  --red-bg: #2a1215;
  --red-border: #5c1f26;
  --amber: #f59e0b;
  --amber-bg: #2a1e0a;
  --amber-border: #5c421a;
  --blue: #3b82f6;
  --blue-bg: #0f1a2a;
  --blue-border: #1e3a5c;
  --purple: #a855f7;
  --purple-bg: #1e1530;
  --purple-border: #3d2d5c;

  --shadow-xs: 0 1px 2px rgba(0, 0, 0, 0.2);
  --shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.25), 0 1px 2px rgba(0, 0, 0, 0.15);
  --shadow-md: 0 2px 4px rgba(0, 0, 0, 0.25), 0 6px 16px rgba(0, 0, 0, 0.3);
  --shadow-lg: 0 4px 12px rgba(0, 0, 0, 0.3), 0 16px 40px rgba(0, 0, 0, 0.4);
  --shadow-xl: 0 8px 24px rgba(0, 0, 0, 0.35), 0 24px 64px rgba(0, 0, 0, 0.5);
  --shadow-ring: 0 0 0 4px rgba(245, 245, 247, 0.08);

  --r-xs: 6px;
  --r-sm: 8px;
  --r-md: 12px;
  --r-lg: 16px;
  --r-xl: 20px;
  --r-2xl: 24px;
  --r-pill: 999px;

  --sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --mono: 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, monospace;

  --ease: cubic-bezier(0.2, 0.8, 0.2, 1);
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --t-fast: 130ms;
  --t-base: 200ms;
  --t-slow: 320ms;
}

[data-theme="light"] {
  --bg: #fbfbfc;
  --bg-2: #f5f5f7;
  --surface: #ffffff;
  --surface-2: #fafafa;
  --surface-3: #f2f2f4;
  --surface-elev: #ffffff;

  --text: #0a0a0a;
  --text-2: #525252;
  --text-3: #8a8a8a;
  --text-4: #b8b8b8;

  --border: #ececec;
  --border-2: #dedede;
  --border-3: #d0d0d0;

  --hover: #f6f6f7;
  --active: #efeff1;
  --focus-ring: rgba(10, 10, 10, 0.06);

  --accent: #0a0a0a;
  --accent-fg: #ffffff;

  --green: #16a34a;
  --green-bg: #f0fdf4;
  --green-border: #bbf7d0;
  --red: #dc2626;
  --red-bg: #fef2f2;
  --red-border: #fecaca;
  --amber: #d97706;
  --amber-bg: #fffbeb;
  --amber-border: #fde68a;
  --blue: #2563eb;
  --blue-bg: #eff6ff;
  --blue-border: #bfdbfe;
  --purple: #7c3aed;
  --purple-bg: #f5f3ff;
  --purple-border: #ddd6fe;

  --shadow-xs: 0 1px 2px rgba(15, 15, 20, 0.03);
  --shadow-sm: 0 1px 2px rgba(15, 15, 20, 0.04), 0 1px 3px rgba(15, 15, 20, 0.04);
  --shadow-md: 0 2px 4px rgba(15, 15, 20, 0.04), 0 6px 16px rgba(15, 15, 20, 0.06);
  --shadow-lg: 0 4px 12px rgba(15, 15, 20, 0.06), 0 16px 40px rgba(15, 15, 20, 0.08);
  --shadow-xl: 0 8px 24px rgba(15, 15, 20, 0.08), 0 24px 64px rgba(15, 15, 20, 0.12);
  --shadow-ring: 0 0 0 4px rgba(10, 10, 10, 0.06);
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html { -webkit-text-size-adjust: 100%; text-rendering: optimizeLegibility; }

body {
  font-family: var(--sans);
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  line-height: 1.55;
  letter-spacing: -0.008em;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  font-feature-settings: 'cv11', 'ss01', 'tnum';
  min-height: 100vh;
  transition: background var(--t-base) var(--ease), color var(--t-base) var(--ease);
}

a { color: var(--text); text-decoration: none; transition: color var(--t-fast) var(--ease); }
a:hover { text-decoration: underline; text-underline-offset: 3px; text-decoration-thickness: 1px; }
button { font-family: inherit; }
input, select, textarea { font-family: inherit; }

::selection { background: var(--accent); color: var(--accent-fg); }

::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border-2); border-radius: var(--r-pill); border: 2px solid var(--bg); }
::-webkit-scrollbar-thumb:hover { background: var(--border-3); }

.layout {
  display: grid;
  grid-template-columns: 264px 1fr;
  min-height: 100vh;
}

@media (max-width: 960px) {
  .layout { grid-template-columns: 1fr; }
  .sidebar { display: none !important; }
}

.sidebar {
  background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 24px 16px 16px;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
  transition: background var(--t-base) var(--ease), border-color var(--t-base) var(--ease);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 8px 24px;
  font-weight: 800;
  font-size: 15px;
  letter-spacing: -0.02em;
}

.brand-mark {
  width: 32px;
  height: 32px;
  background: var(--accent);
  color: var(--accent-fg);
  border-radius: 10px;
  display: grid;
  place-items: center;
  font-size: 15px;
  font-weight: 900;
  transition: transform var(--t-base) var(--ease);
  box-shadow: 0 4px 12px rgba(255, 255, 255, 0.08);
}
.brand:hover .brand-mark { transform: rotate(-8deg) scale(1.05); }

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
}
.brand-text small {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-3);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-top: 2px;
}

.nav-group {
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--text-3);
  padding: 20px 10px 8px;
}

.nav-link {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: var(--r-sm);
  color: var(--text-2);
  font-size: 13px;
  font-weight: 500;
  transition: all var(--t-fast) var(--ease);
  position: relative;
  margin-bottom: 1px;
}

.nav-link:hover {
  background: var(--hover);
  color: var(--text);
  text-decoration: none;
}

.nav-link.active {
  background: var(--active);
  color: var(--text);
  font-weight: 600;
}

.nav-link.active::before {
  content: "";
  position: absolute;
  left: -16px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 18px;
  background: var(--accent);
  border-radius: 0 3px 3px 0;
}

.nav-link svg {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  opacity: 0.7;
  transition: opacity var(--t-fast) var(--ease);
}

.nav-link:hover svg, .nav-link.active svg { opacity: 1; }

.sidebar-footer {
  margin-top: auto;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}

.theme-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 12px;
  border-radius: var(--r-sm);
  font-size: 12px;
  color: var(--text-2);
  cursor: pointer;
  border: none;
  background: transparent;
  width: 100%;
  margin-bottom: 8px;
  transition: background var(--t-fast) var(--ease);
}
.theme-toggle:hover { background: var(--hover); color: var(--text); }

.theme-toggle .toggle-switch {
  width: 32px;
  height: 18px;
  border-radius: var(--r-pill);
  background: var(--border-2);
  position: relative;
  transition: background var(--t-base) var(--ease);
}
.theme-toggle .toggle-switch::after {
  content: "";
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0,0,0,0.2);
  transition: transform var(--t-base) var(--ease);
}
[data-theme="light"] .theme-toggle .toggle-switch { background: var(--accent); }
[data-theme="light"] .theme-toggle .toggle-switch::after { transform: translateX(14px); }

.main {
  padding: 40px 40px 80px;
  max-width: 1520px;
  width: 100%;
  margin: 0 auto;
}

@media (max-width: 640px) {
  .main { padding: 24px 16px 60px; }
}

.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 32px;
  flex-wrap: wrap;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--border);
}

.page-title {
  font-size: 32px;
  font-weight: 800;
  letter-spacing: -0.045em;
  line-height: 1.1;
  background: linear-gradient(180deg, var(--text) 0%, var(--text-2) 120%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.page-sub {
  color: var(--text-2);
  font-size: 14px;
  margin-top: 6px;
  letter-spacing: -0.005em;
}

.page-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.page-meta {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: var(--r-pill);
  background: var(--surface);
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--text-2);
  box-shadow: var(--shadow-xs);
}

.page-meta-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.15);
  animation: pulse 2.4s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.15); }
  50% { box-shadow: 0 0 0 6px rgba(34, 197, 94, 0.02); }
}

.crumb {
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--text-3);
  margin-bottom: 10px;
}

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 9px 16px;
  border-radius: var(--r-sm);
  border: 1px solid var(--border-2);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
  letter-spacing: -0.005em;
  cursor: pointer;
  transition: all var(--t-fast) var(--ease);
  text-decoration: none;
  line-height: 1.2;
  white-space: nowrap;
  user-select: none;
}

.btn:hover {
  background: var(--hover);
  border-color: var(--border-3);
  text-decoration: none;
  transform: translateY(-1px);
  box-shadow: var(--shadow-xs);
}
.btn:active { transform: translateY(0); box-shadow: none; }
.btn:focus-visible { outline: none; box-shadow: var(--shadow-ring); }

.btn-primary, .btn-dark {
  background: var(--accent);
  color: var(--accent-fg);
  border-color: var(--accent);
  box-shadow: 0 1px 0 rgba(255,255,255,.08) inset, var(--shadow-xs);
}
.btn-primary:hover, .btn-dark:hover {
  background: var(--accent);
  border-color: var(--accent);
  filter: brightness(1.1);
}

.btn-danger {
  background: var(--red);
  color: #fff;
  border-color: var(--red);
}
.btn-danger:hover { background: #b91c1c; border-color: #b91c1c; filter: none; }

.btn-success {
  background: var(--green);
  color: #fff;
  border-color: var(--green);
}
.btn-success:hover { background: #15803d; border-color: #15803d; filter: none; }

.btn-ghost {
  background: transparent;
  border-color: transparent;
  color: var(--text-2);
}
.btn-ghost:hover { background: var(--hover); color: var(--text); }

.btn-sm {
  padding: 5px 11px;
  font-size: 12px;
  border-radius: 7px;
  letter-spacing: 0;
}
.btn-sm.btn-icon { padding: 5px 8px; }

.btn-lg {
  padding: 12px 22px;
  font-size: 14px;
  border-radius: var(--r-md);
}

.btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
  letter-spacing: 0.005em;
  margin: 16px 0 6px;
}

label:first-child { margin-top: 0; }

input, select, textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border-2);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--text);
  font-size: 13.5px;
  letter-spacing: -0.005em;
  transition: all var(--t-fast) var(--ease);
  outline: none;
}

input:hover, select:hover, textarea:hover { border-color: var(--border-3); }

input:focus, select:focus, textarea:focus {
  border-color: var(--accent);
  box-shadow: var(--shadow-ring);
}

input::placeholder, textarea::placeholder { color: var(--text-4); }

input[type="checkbox"], input[type="radio"] {
  width: auto;
  accent-color: var(--accent);
  cursor: pointer;
}

textarea { resize: vertical; min-height: 80px; }

select {
  appearance: none;
  background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2378787f' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 12px center;
  padding-right: 36px;
}

.field-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  align-items: flex-end;
}

.field-row > * {
  flex: 1;
  min-width: 140px;
}

.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: 24px;
  margin-bottom: 20px;
  box-shadow: var(--shadow-xs);
  transition: box-shadow var(--t-base) var(--ease),
              border-color var(--t-base) var(--ease);
}

.card:hover {
  box-shadow: var(--shadow-sm);
  border-color: var(--border-2);
}

.card h2 {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.015em;
  margin-bottom: 16px;
  color: var(--text);
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.card h2 .sub {
  font-size: 12px;
  color: var(--text-3);
  font-weight: 500;
  letter-spacing: -0.005em;
  margin-left: auto;
}

.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 28px;
}

.stat {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: 20px;
  box-shadow: var(--shadow-xs);
  transition: transform var(--t-base) var(--ease-out),
              box-shadow var(--t-base) var(--ease),
              border-color var(--t-fast) var(--ease);
  position: relative;
  overflow: hidden;
}

.stat::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  opacity: 0;
  transition: opacity var(--t-base) var(--ease);
}

.stat:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
  border-color: var(--border-2);
}
.stat:hover::before { opacity: 0.6; }

.stat .k {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--text-3);
  display: flex;
  align-items: center;
  gap: 6px;
}

.stat .v {
  font-size: 32px;
  font-weight: 800;
  letter-spacing: -0.04em;
  line-height: 1.05;
  margin-top: 10px;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.stat .v small {
  font-size: 14px;
  color: var(--text-3);
  font-weight: 500;
  margin-left: 4px;
  letter-spacing: 0;
}

.table-wrap {
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  overflow: hidden;
  background: var(--surface);
  box-shadow: var(--shadow-xs);
}

table {
  width: 100%;
  border-collapse: collapse;
}

thead th {
  background: var(--surface-2);
  color: var(--text-2);
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  text-align: left;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

tbody td {
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  font-size: 13.5px;
  color: var(--text);
  letter-spacing: -0.005em;
  transition: background var(--t-fast) var(--ease);
  vertical-align: middle;
}

tbody tr:last-child td { border-bottom: none; }
tbody tr { transition: background var(--t-fast) var(--ease); }
tbody tr:hover td { background: var(--hover); }

.mono {
  font-family: var(--mono);
  font-size: 12.5px;
  letter-spacing: -0.01em;
  font-feature-settings: 'tnum';
}

.muted { color: var(--text-2); }
.dim { color: var(--text-3); }

.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: var(--r-pill);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.01em;
  border: 1px solid var(--border-2);
  background: var(--surface);
  color: var(--text-2);
  line-height: 1.5;
  white-space: nowrap;
}

.badge.solid {
  background: var(--accent);
  color: var(--accent-fg);
  border-color: var(--accent);
}

.badge.green { background: var(--green-bg); color: var(--green); border-color: var(--green-border); }
.badge.red { background: var(--red-bg); color: var(--red); border-color: var(--red-border); }
.badge.amber { background: var(--amber-bg); color: var(--amber); border-color: var(--amber-border); }
.badge.blue { background: var(--blue-bg); color: var(--blue); border-color: var(--blue-border); }
.badge.purple { background: var(--purple-bg); color: var(--purple); border-color: var(--purple-border); }

.badge-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  display: inline-block;
}

.flash {
  padding: 12px 16px;
  border-radius: var(--r-md);
  font-size: 13.5px;
  font-weight: 500;
  letter-spacing: -0.005em;
  margin-bottom: 16px;
  background: var(--green-bg);
  color: var(--green);
  border: 1px solid var(--green-border);
  display: flex;
  align-items: center;
  gap: 10px;
  box-shadow: var(--shadow-xs);
  animation: flash-in var(--t-slow) var(--ease-out);
}

.flash.err {
  background: var(--red-bg);
  color: var(--red);
  border-color: var(--red-border);
}

.flash .flash-close {
  margin-left: auto;
  cursor: pointer;
  opacity: 0.5;
  background: none;
  border: none;
  color: inherit;
  padding: 4px;
  font-size: 16px;
  line-height: 1;
}
.flash .flash-close:hover { opacity: 1; }

@keyframes flash-in {
  from { opacity: 0; transform: translateY(-6px); }
  to { opacity: 1; transform: translateY(0); }
}

.card-chart { padding-bottom: 20px; }
.card-chart h2 { margin-bottom: 20px; }

.chart-box {
  height: 260px;
  position: relative;
  margin: 0 -12px -12px;
}

.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-bottom: 20px;
}

.grid-3 {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
  margin-bottom: 20px;
}

@media (max-width: 1100px) { .grid-3 { grid-template-columns: 1fr; } }
@media (max-width: 1000px) { .grid-2 { grid-template-columns: 1fr; } }

.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 40px 20px;
  background: var(--bg);
  position: relative;
  overflow: hidden;
}

.login-page::before,
.login-page::after {
  content: "";
  position: absolute;
  border-radius: 50%;
  filter: blur(100px);
  pointer-events: none;
  opacity: 0.5;
}
.login-page::before {
  width: 500px;
  height: 500px;
  background: rgba(245, 245, 247, 0.05);
  top: -200px;
  left: -200px;
}
.login-page::after {
  width: 400px;
  height: 400px;
  background: rgba(245, 245, 247, 0.04);
  bottom: -150px;
  right: -150px;
}

[data-theme="light"] .login-page::before { background: rgba(10, 10, 10, 0.05); }
[data-theme="light"] .login-page::after { background: rgba(10, 10, 10, 0.04); }

.login-card {
  width: 100%;
  max-width: 420px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-2xl);
  padding: 48px 40px 40px;
  box-shadow: var(--shadow-xl);
  animation: card-in 500ms var(--ease-out);
  position: relative;
  z-index: 1;
}

@keyframes card-in {
  from { opacity: 0; transform: translateY(12px) scale(0.97); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

.login-mark {
  width: 56px;
  height: 56px;
  background: var(--accent);
  color: var(--accent-fg);
  border-radius: var(--r-lg);
  display: grid;
  place-items: center;
  font-size: 26px;
  font-weight: 900;
  margin: 0 auto 24px;
  letter-spacing: -0.02em;
  box-shadow: 0 8px 24px rgba(255, 255, 255, 0.08);
  transition: transform var(--t-base) var(--ease);
}

[data-theme="light"] .login-mark { box-shadow: 0 8px 24px rgba(10, 10, 10, 0.15); }

.login-card:hover .login-mark {
  transform: rotate(-6deg) scale(1.04);
}

.login-card h1 {
  font-size: 22px;
  font-weight: 800;
  text-align: center;
  letter-spacing: -0.035em;
  color: var(--text);
  line-height: 1.2;
}

.login-card .sub {
  text-align: center;
  color: var(--text-2);
  font-size: 13.5px;
  margin: 8px 0 32px;
  letter-spacing: -0.005em;
}

.login-label {
  margin-top: 0;
  margin-bottom: 8px;
  text-transform: uppercase;
  font-size: 11px;
  letter-spacing: 0.14em;
  color: var(--text-3);
  font-weight: 700;
}

.login-input {
  padding: 13px 14px;
  font-size: 14px;
  border-radius: var(--r-md);
  background: var(--surface-2);
  letter-spacing: 0.02em;
  transition: all var(--t-fast) var(--ease);
}

.login-input:hover { background: var(--surface); }
.login-input:focus {
  background: var(--surface);
  box-shadow: var(--shadow-ring), var(--shadow-sm);
}

.login-input::placeholder {
  letter-spacing: 0.15em;
  color: var(--text-4);
}

.login-submit {
  width: 100%;
  margin-top: 24px;
  padding: 13px 20px;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.005em;
  border-radius: var(--r-md);
}

.login-foot {
  text-align: center;
  margin-top: 24px;
  font-size: 12px;
  color: var(--text-3);
}

.empty {
  padding: 48px 24px;
  text-align: center;
  color: var(--text-3);
  font-size: 13.5px;
}

.empty-icon {
  font-size: 32px;
  margin-bottom: 8px;
  opacity: 0.4;
}

.mt-2 { margin-top: 8px; }
.mt-3 { margin-top: 16px; }
.mt-4 { margin-top: 24px; }
.mb-2 { margin-bottom: 8px; }
.mb-3 { margin-bottom: 16px; }
.mb-4 { margin-bottom: 24px; }
.text-right { text-align: right; }
.text-center { text-align: center; }

code {
  font-family: var(--mono);
  font-size: 12.5px;
  background: var(--surface-3);
  padding: 2px 7px;
  border-radius: 5px;
  color: var(--text);
  letter-spacing: -0.01em;
}

.main > * {
  animation: page-in var(--t-slow) var(--ease-out) both;
}
.main > *:nth-child(1) { animation-delay: 0ms; }
.main > *:nth-child(2) { animation-delay: 30ms; }
.main > *:nth-child(3) { animation-delay: 60ms; }
.main > *:nth-child(4) { animation-delay: 90ms; }
.main > *:nth-child(5) { animation-delay: 120ms; }

@keyframes page-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
</head>
<body>

{% if session.get('admin') %}
<div class="layout">
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-mark">S</div>
      <div class="brand-text">
        <span>SELF BOT</span>
        <small>Admin Panel</small>
      </div>
    </div>

    <div class="nav-group">Overview</div>
    <a href="{{ url_for('dashboard') }}" class="nav-link {{ 'active' if page=='dashboard' else '' }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>
      Dashboard
    </a>
    <a href="{{ url_for('users_page') }}" class="nav-link {{ 'active' if page=='users' else '' }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
      Users
    </a>
    <a href="{{ url_for('requests_page') }}" class="nav-link {{ 'active' if page=='requests' else '' }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
      Requests
    </a>
    <a href="{{ url_for('transactions_page') }}" class="nav-link {{ 'active' if page=='tx' else '' }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
      Transactions
    </a>

    <div class="nav-group">Automation</div>
    <a href="{{ url_for('casino_page') }}" class="nav-link {{ 'active' if page=='casino' else '' }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/><line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/><line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/></svg>
      Casino
    </a>
    <a href="{{ url_for('casino_access_page') }}" class="nav-link {{ 'active' if page=='casino_access' else '' }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
      Casino Access
    </a>

    <div class="nav-group">System</div>
    <a href="{{ url_for('shop_page') }}" class="nav-link {{ 'active' if page=='shop' else '' }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/></svg>
      Shop
    </a>
    <a href="{{ url_for('audit_page') }}" class="nav-link {{ 'active' if page=='audit' else '' }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><line x1="9" y1="15" x2="15" y2="15"/><line x1="9" y1="11" x2="15" y2="11"/></svg>
      Audit
    </a>
    <a href="{{ url_for('backups_page') }}" class="nav-link {{ 'active' if page=='backups' else '' }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><polyline points="21 3 21 8 16 8"/></svg>
      Backups
    </a>
    <a href="{{ url_for('broadcast_page') }}" class="nav-link {{ 'active' if page=='broadcast' else '' }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/></svg>
      Broadcast
    </a>
    <a href="{{ url_for('settings_page') }}" class="nav-link {{ 'active' if page=='settings' else '' }}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
      Settings
    </a>

    <div class="sidebar-footer">
      <button class="theme-toggle" onclick="toggleTheme()">
        <span>Theme</span>
        <span class="toggle-switch"></span>
      </button>
      <a href="{{ url_for('logout') }}" class="nav-link">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
        Sign out
      </a>
    </div>
  </aside>

  <main class="main">
    {% with msgs = get_flashed_messages(with_categories=true) %}
      {% for cat, msg in msgs %}
        <div class="flash {{ 'err' if cat=='error' else '' }}">
          <span>{{ msg }}</span>
          <button class="flash-close" onclick="this.parentElement.remove()">×</button>
        </div>
      {% endfor %}
    {% endwith %}
    {{ body|safe }}
  </main>
</div>

<script>
(function() {
  const stored = localStorage.getItem('admin-theme');
  if (stored === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
  }
})();
function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('admin-theme', next);
}
</script>

{% else %}
{{ body|safe }}
{% endif %}

</body>
</html>
"""


# ===========================================================================
# LOGIN
# ===========================================================================
LOGIN_TEMPLATE = r"""
<div class="login-page">
  <form class="login-card" method="post" action="{{ url_for('login_post') }}">
    <div class="login-mark">S</div>
    <h1>Admin Sign In</h1>
    <p class="sub">SELF BOT control panel</p>

    <input type="hidden" name="csrf" value="{{ csrf }}">

    <label class="login-label">Password</label>
    <input class="login-input" type="password" name="password"
           placeholder="••••••••••••" autofocus required autocomplete="current-password">

    {% if totp_enabled %}
    <label class="login-label" style="margin-top:20px">2FA code</label>
    <input class="login-input mono" type="text" name="totp"
           inputmode="numeric" pattern="[0-9]*" maxlength="6"
           placeholder="000000" autocomplete="one-time-code">
    {% endif %}

    <button type="submit" class="btn btn-primary login-submit">Sign in</button>

    <div class="login-foot">
      Protected by rate limiting &middot; CSRF
    </div>
  </form>
</div>
"""


# ===========================================================================
# DASHBOARD
# ===========================================================================
DASHBOARD_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Overview</div>
    <div class="page-title">Dashboard</div>
    <div class="page-sub">Live snapshot of your bot economy and activity</div>
  </div>
  <div class="page-actions">
    <div class="page-meta">
      <span class="page-meta-dot"></span>
      <span>Live</span>
    </div>
  </div>
</div>

<div class="stats">
  <div class="stat">
    <div class="k">Total users</div>
    <div class="v">{{ users_total }}</div>
  </div>
  <div class="stat">
    <div class="k">Active 24h</div>
    <div class="v">{{ users_active }}</div>
  </div>
  <div class="stat">
    <div class="k">New 7 days</div>
    <div class="v">{{ users_new }}</div>
  </div>
  <div class="stat">
    <div class="k">Diamonds in circulation</div>
    <div class="v">{{ diamonds }}</div>
  </div>
  <div class="stat">
    <div class="k">Granted all-time</div>
    <div class="v">{{ granted }}</div>
  </div>
  <div class="stat">
    <div class="k">Spent all-time</div>
    <div class="v">{{ spent }}</div>
  </div>
</div>

<div class="grid-2">
  <div class="card card-chart">
    <h2>Diamond consumption <span class="sub">last 24 hours</span></h2>
    <div class="chart-box"><canvas id="diamondChart"></canvas></div>
  </div>
  <div class="card card-chart">
    <h2>Subscription distribution</h2>
    <div class="chart-box"><canvas id="subChart"></canvas></div>
  </div>
</div>

<div class="grid-2">
  <div class="card card-chart">
    <h2>Request approval rate <span class="sub">last 14 days</span></h2>
    <div class="chart-box"><canvas id="approvalChart"></canvas></div>
  </div>
  <div class="card">
    <h2>Top spenders</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>User</th><th class="text-right">Spent</th></tr></thead>
        <tbody>
        {% for s in top_spenders %}
        <tr>
          <td class="mono dim">{{ loop.index }}</td>
          <td><a href="{{ url_for('user_detail', uid=s.user_id) }}">#{{ s.user_id }}</a></td>
          <td class="text-right mono">{{ s.spent }} ◆</td>
        </tr>
        {% else %}
        <tr><td colspan="3" class="empty"><div class="empty-icon">💎</div>No spenders yet</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>

<script>
Chart.defaults.color = getComputedStyle(document.documentElement).getPropertyValue('--text-3').trim() || '#a3a3a3';
Chart.defaults.font.family = 'Inter, sans-serif';
Chart.defaults.font.size = 11;
Chart.defaults.font.weight = 500;
Chart.defaults.borderColor = getComputedStyle(document.documentElement).getPropertyValue('--border').trim() || '#23232a';

const accentColor = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#f5f5f7';
const gridColor = 'rgba(255,255,255,0.05)';

const diamondData = {{ diamond_series|safe }};
new Chart(document.getElementById('diamondChart'), {
  type: 'line',
  data: {
    labels: diamondData.map(d => d.hour.slice(11, 16)),
    datasets: [{
      label: 'Spent', data: diamondData.map(d => d.spent),
      borderColor: accentColor,
      backgroundColor: accentColor + '14',
      fill: true, tension: 0.4, pointRadius: 0, pointHoverRadius: 5,
      borderWidth: 2, pointHoverBackgroundColor: accentColor, pointHoverBorderColor: '#fff',
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    interaction: { intersect: false, mode: 'index' },
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { display: false }, border: { display: false }, ticks: { padding: 6 } },
      y: { grid: { color: gridColor }, border: { display: false }, beginAtZero: true, ticks: { padding: 6 } }
    }
  }
});

const subData = {{ sub_dist|safe }};
new Chart(document.getElementById('subChart'), {
  type: 'doughnut',
  data: {
    labels: Object.keys(subData),
    datasets: [{
      data: Object.values(subData),
      backgroundColor: ['#f5f5f7', '#a8a8b0', '#78787f', '#3a3a43'],
      borderWidth: 0, hoverOffset: 6,
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false, cutout: '70%',
    plugins: { legend: { position: 'bottom', labels: { padding: 16, boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: 'circle' } } }
  }
});

const approvalData = {{ approval_series|safe }};
new Chart(document.getElementById('approvalChart'), {
  type: 'bar',
  data: {
    labels: approvalData.map(d => d.date.slice(5)),
    datasets: [
      { label: 'Approved', data: approvalData.map(d => d.approved),
        backgroundColor: accentColor, borderRadius: 4, barThickness: 10 },
      { label: 'Rejected', data: approvalData.map(d => d.rejected),
        backgroundColor: 'rgba(255,255,255,0.15)', borderRadius: 4, barThickness: 10 },
    ]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { position: 'bottom', labels: { padding: 16, boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: 'circle' } } },
    scales: {
      x: { stacked: true, grid: { display: false }, border: { display: false } },
      y: { stacked: true, grid: { color: gridColor }, border: { display: false } }
    }
  }
});
</script>
"""


# ===========================================================================
# USERS
# ===========================================================================
USERS_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Manage</div>
    <div class="page-title">Users</div>
    <div class="page-sub">{{ users|length }} registered accounts</div>
  </div>
</div>

<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>ID</th><th>Name</th><th>Username</th><th>Plan</th>
        <th>Days</th><th>Diamonds</th><th>Casino</th><th>Status</th><th class="text-right"></th>
      </tr>
    </thead>
    <tbody>
    {% for u in users %}
    <tr>
      <td class="mono">#{{ u.id }}</td>
      <td>{{ u.first_name or '—' }}</td>
      <td class="muted">{{ ('@' + u.username) if u.username else '—' }}</td>
      <td>
        {% if u.plan != 'member' %}
          <span class="badge solid">{{ u.plan }}</span>
        {% else %}
          <span class="badge">{{ u.plan }}</span>
        {% endif %}
      </td>
      <td class="mono">{{ u.days }}</td>
      <td class="mono">{{ u.diamonds }}</td>
      <td>
        {% if u.casino_unlocked %}
          <span class="badge green">unlocked</span>
        {% else %}
          <span class="badge amber">locked</span>
        {% endif %}
      </td>
      <td>
        {% if u.banned %}<span class="badge red">banned</span>
        {% else %}<span class="badge green">active</span>{% endif %}
      </td>
      <td class="text-right">
        <a class="btn btn-sm" href="{{ url_for('user_detail', uid=u.id) }}">Open →</a>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="9" class="empty"><div class="empty-icon">👥</div>No users yet.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
"""


# ===========================================================================
# USER DETAIL
# ===========================================================================
USER_DETAIL_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">User</div>
    <div class="page-title">#{{ u.id }}</div>
    <div class="page-sub">
      {{ u.first_name or 'Unnamed' }} &middot; joined {{ u.created_at[:10] if u.created_at else '—' }}
    </div>
  </div>
  <div class="page-actions">
    <a class="btn" href="{{ url_for('users_page') }}">← Back</a>
  </div>
</div>

<div class="stats">
  <div class="stat"><div class="k">Diamonds</div><div class="v">{{ u.diamonds }}</div></div>
  <div class="stat"><div class="k">Plan</div><div class="v" style="font-size:22px">{{ u.plan }}</div></div>
  <div class="stat"><div class="k">Days left</div><div class="v">{{ u.days }}</div></div>
  <div class="stat"><div class="k">Referral code</div><div class="v mono" style="font-size:15px">{{ u.referral_code }}</div></div>
</div>

<div class="card">
  <h2>Casino access</h2>
  <p class="muted" style="font-size:13px;margin-bottom:12px">
    {% if u.casino_unlocked %}
      This user currently has <strong style="color:var(--green)">casino access</strong>.
    {% else %}
      This user is currently <strong style="color:var(--amber)">locked</strong> out of casino.
    {% endif %}
  </p>
  {% if u.casino_unlocked %}
  <form method="post" action="{{ url_for('casino_access_lock', uid=u.id) }}" style="display:inline"
        onsubmit="return confirm('Lock casino access for this user?');">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn btn-danger">🔒 Lock casino</button>
  </form>
  {% else %}
  <form method="post" action="{{ url_for('casino_access_unlock', uid=u.id) }}" style="display:inline">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn btn-success">🔓 Unlock casino</button>
  </form>
  {% endif %}
</div>

<div class="grid-2">
  <div class="card">
    <h2>Grant diamonds</h2>
    <form method="post" action="{{ url_for('grant_diamonds', uid=u.id) }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <div class="field-row">
        <div><label>Amount</label><input type="number" name="amount" value="10"></div>
        <div><label>Reason</label><input type="text" name="reason" value="admin_grant"></div>
        <button class="btn btn-primary">Apply</button>
      </div>
    </form>
  </div>

  <div class="card">
    <h2>Set subscription</h2>
    <form method="post" action="{{ url_for('set_subscription', uid=u.id) }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <div class="field-row">
        <div><label>Plan</label>
          <select name="plan">
            {% for p in plans %}<option value="{{ p }}" {{ 'selected' if p==u.plan else '' }}>{{ p }}</option>{% endfor %}
          </select>
        </div>
        <div style="flex:0 0 110px"><label>Days</label><input type="number" name="days" value="30"></div>
        <button class="btn btn-primary">Set</button>
      </div>
    </form>
    <div class="mt-3">
      <form method="post" action="{{ url_for('cancel_subscription', uid=u.id) }}" style="display:inline">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn btn-sm">Cancel subscription</button>
      </form>
    </div>
  </div>
</div>

<div class="card">
  <h2>Moderation</h2>
  <form method="post" action="{{ url_for('toggle_ban', uid=u.id) }}" style="display:inline">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn {{ 'btn-success' if u.banned else 'btn-danger' }}">
      {{ 'Unban user' if u.banned else 'Ban user' }}
    </button>
  </form>
  <form method="post" action="{{ url_for('delete_user_route', uid=u.id) }}" style="display:inline"
        onsubmit="return confirm('Delete user? This cannot be undone.');">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn btn-danger">Delete permanently</button>
  </form>
</div>

<div class="card">
  <h2>Recent transactions</h2>
  <div class="table-wrap">
    <table>
      <thead><tr><th>When</th><th>Kind</th><th>Amount</th><th>Reason</th><th class="text-right">Balance</th></tr></thead>
      <tbody>
      {% for tx in txs %}
      <tr>
        <td class="mono muted">{{ tx.created_at[:19] }}</td>
        <td>
          {% if tx.kind == 'spend' %}<span class="badge red">{{ tx.kind }}</span>
          {% elif tx.kind == 'refund' %}<span class="badge amber">{{ tx.kind }}</span>
          {% else %}<span class="badge green">{{ tx.kind }}</span>{% endif %}
        </td>
        <td class="mono">{{ tx.amount }}</td>
        <td class="muted">{{ tx.reason }}</td>
        <td class="mono text-right">{{ tx.balance_after }}</td>
      </tr>
      {% else %}
      <tr><td colspan="5" class="empty">No transactions.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
"""


# ===========================================================================
# REQUESTS
# ===========================================================================
REQUESTS_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Manage</div>
    <div class="page-title">Requests</div>
    <div class="page-sub">Approve or reject user submissions</div>
  </div>
</div>

<div class="table-wrap">
  <table>
    <thead>
      <tr><th>ID</th><th>User</th><th>Type</th><th>Details</th><th>Status</th><th>Created</th><th class="text-right">Actions</th></tr>
    </thead>
    <tbody>
    {% for r in requests %}
    <tr>
      <td class="mono muted">{{ r.id }}</td>
      <td><a href="{{ url_for('user_detail', uid=r.user_id) }}">#{{ r.user_id }}</a></td>
      <td>{{ r.type }}</td>
      <td><strong>{{ r.amount or r.plan or '—' }}</strong></td>
      <td>
        {% if r.status == 'pending' %}<span class="badge amber">pending</span>
        {% elif r.status == 'approved' %}<span class="badge green">approved</span>
        {% else %}<span class="badge red">rejected</span>{% endif %}
      </td>
      <td class="mono muted">{{ r.created_at[:19] }}</td>
      <td class="text-right">
        {% if r.status == 'pending' %}
        <form method="post" action="{{ url_for('approve_request', rid=r.id) }}" style="display:inline">
          <input type="hidden" name="csrf" value="{{ csrf }}">
          <button class="btn btn-success btn-sm">Approve</button>
        </form>
        <form method="post" action="{{ url_for('reject_request', rid=r.id) }}" style="display:inline">
          <input type="hidden" name="csrf" value="{{ csrf }}">
          <button class="btn btn-danger btn-sm">Reject</button>
        </form>
        {% endif %}
      </td>
    </tr>
    {% else %}
    <tr><td colspan="7" class="empty"><div class="empty-icon">📥</div>No requests.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
"""


# ===========================================================================
# TRANSACTIONS
# ===========================================================================
TX_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Manage</div>
    <div class="page-title">Transactions</div>
    <div class="page-sub">Complete ledger of diamond flow</div>
  </div>
</div>

<div class="stats">
  <div class="stat"><div class="k">Granted</div><div class="v">{{ totals.granted }}</div></div>
  <div class="stat"><div class="k">Spent</div><div class="v">{{ totals.spent }}</div></div>
  <div class="stat"><div class="k">Refunded</div><div class="v">{{ totals.refunded }}</div></div>
</div>

<div class="table-wrap">
  <table>
    <thead><tr><th>When</th><th>User</th><th>Kind</th><th>Amount</th><th>Reason</th><th class="text-right">Balance</th></tr></thead>
    <tbody>
    {% for tx in txs %}
    <tr>
      <td class="mono muted">{{ tx.created_at[:19] }}</td>
      <td><a href="{{ url_for('user_detail', uid=tx.user_id) }}">#{{ tx.user_id }}</a></td>
      <td>
        {% if tx.kind == 'spend' %}<span class="badge red">{{ tx.kind }}</span>
        {% elif tx.kind == 'refund' %}<span class="badge amber">{{ tx.kind }}</span>
        {% else %}<span class="badge green">{{ tx.kind }}</span>{% endif %}
      </td>
      <td class="mono">{{ tx.amount }}</td>
      <td class="muted">{{ tx.reason }}</td>
      <td class="mono text-right">{{ tx.balance_after }}</td>
    </tr>
    {% else %}
    <tr><td colspan="6" class="empty">No transactions.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
"""


# ===========================================================================
# SHOP
# ===========================================================================
SHOP_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">System</div>
    <div class="page-title">Shop</div>
    <div class="page-sub">Items users can buy with diamonds</div>
  </div>
</div>

<div class="card">
  <h2>Add or update item</h2>
  <form method="post" action="{{ url_for('shop_save') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="field-row">
      <div><label>ID</label><input name="item_id" placeholder="extra_job_slot"></div>
      <div><label>Name</label><input name="name" placeholder="Extra job slot"></div>
      <div style="flex:0 0 120px"><label>Price</label><input type="number" name="price" value="20"></div>
      <div style="flex:0 0 140px"><label>Max per user</label><input type="number" name="max_per_user" value="1"></div>
    </div>
    <label>Description</label>
    <input name="description" placeholder="What does this item do?">
    <div class="mt-3"><button class="btn btn-primary">Save item</button></div>
  </form>
</div>

<div class="table-wrap">
  <table>
    <thead><tr><th>ID</th><th>Name</th><th>Price</th><th>Max</th><th>Description</th><th></th></tr></thead>
    <tbody>
    {% for iid, it in items.items() %}
    <tr>
      <td><code>{{ iid }}</code></td>
      <td><strong>{{ it.name }}</strong></td>
      <td class="mono">{{ it.price }} ◆</td>
      <td class="mono">{{ it.max_per_user }}</td>
      <td class="muted">{{ it.description }}</td>
      <td class="text-right">
        <form method="post" action="{{ url_for('shop_delete', item_id=iid) }}" style="display:inline">
          <input type="hidden" name="csrf" value="{{ csrf }}">
          <button class="btn btn-danger btn-sm">Delete</button>
        </form>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="6" class="empty">No items.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
"""


# ===========================================================================
# AUDIT
# ===========================================================================
AUDIT_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">System</div>
    <div class="page-title">Audit log</div>
    <div class="page-sub">Every admin action, timestamped</div>
  </div>
</div>

<div class="table-wrap">
  <table>
    <thead><tr><th>When</th><th>Admin</th><th>Action</th><th>Target</th><th>IP</th></tr></thead>
    <tbody>
    {% for e in entries %}
    <tr>
      <td class="mono muted">{{ e.created_at[:19] }}</td>
      <td class="mono">#{{ e.admin_id }}{% if e.admin_name %} <span class="muted">({{ e.admin_name }})</span>{% endif %}</td>
      <td><span class="badge">{{ e.action }}</span></td>
      <td class="muted">{{ e.target or '—' }}</td>
      <td class="mono muted">{{ e.ip or '—' }}</td>
    </tr>
    {% else %}
    <tr><td colspan="5" class="empty">No audit entries.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
"""


# ===========================================================================
# BACKUPS
# ===========================================================================
BACKUPS_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">System</div>
    <div class="page-title">Backups</div>
    <div class="page-sub">Snapshot and restore all bot data</div>
  </div>
</div>

<div class="grid-2">
  <div class="card">
    <h2>Create backup</h2>
    <p class="muted mt-2 mb-3">
      Zips all JSON data and sends it to the owner's Telegram DM.
    </p>
    <form method="post" action="{{ url_for('backup_now') }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="btn btn-primary">Create backup now</button>
    </form>
  </div>

  <div class="card">
    <h2>Restore from ZIP</h2>
    <p class="muted mt-2 mb-3">
      Upload a previously created backup archive.
    </p>
    <form method="post" action="{{ url_for('restore_backup') }}" enctype="multipart/form-data">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <input type="file" name="file" accept=".zip">
      <div class="mt-3"><button class="btn btn-danger">Restore data</button></div>
    </form>
  </div>
</div>

<div class="table-wrap">
  <table>
    <thead><tr><th>File</th><th>Size</th><th>Modified</th><th></th></tr></thead>
    <tbody>
    {% for b in backups %}
    <tr>
      <td class="mono">{{ b.name }}</td>
      <td class="mono">{{ b.size_kb }} KB</td>
      <td class="muted mono">{{ b.modified }}</td>
      <td class="text-right">
        <a class="btn btn-sm" href="{{ url_for('download_backup', name=b.name) }}">Download</a>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="4" class="empty">No backups yet.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
"""


# ===========================================================================
# BROADCAST
# ===========================================================================
BROADCAST_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">System</div>
    <div class="page-title">Broadcast</div>
    <div class="page-sub">Send a message to all non-banned users</div>
  </div>
</div>

<div class="card">
  <form method="post" action="{{ url_for('broadcast_send') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <label>Message</label>
    <textarea name="message" rows="8" placeholder="Hello everyone..."></textarea>
    <div class="mt-3"><button class="btn btn-primary">Send to all users</button></div>
  </form>
</div>
"""


# ===========================================================================
# SETTINGS
# ===========================================================================
SETTINGS_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">System</div>
    <div class="page-title">Settings</div>
    <div class="page-sub">Runtime economy and bot configuration</div>
  </div>
</div>

<div class="card">
  <h2>Economy</h2>
  <form method="post" action="{{ url_for('settings_save') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="field-row">
      <div><label>Signup bonus</label><input type="number" name="signup_bonus" value="{{ cfg.signup_bonus }}"></div>
      <div><label>Cost per clock update</label><input type="number" name="diamond_cost_clock" value="{{ cfg.diamond_cost_clock }}"></div>
      <div><label>Cost per job</label><input type="number" name="diamond_cost_job" value="{{ cfg.diamond_cost_job }}"></div>
      <div><label>Referral bonus</label><input type="number" name="referral_bonus" value="{{ cfg.referral_bonus }}"></div>
    </div>
    <div class="field-row mt-3">
      <div><label>Timezone</label><input name="timezone" value="{{ cfg.timezone }}"></div>
      <div><label>Base name</label><input name="base_name" value="{{ cfg.base_name }}"></div>
      <div style="flex:0 0 140px"><label>Interval (min)</label><input type="number" name="interval" value="{{ cfg.interval }}"></div>
    </div>
    <div class="mt-4"><button class="btn btn-primary">Save settings</button></div>
  </form>
</div>

<div class="card">
  <h2>Security</h2>
  <p class="muted mt-2 mb-3">
    Two-factor authentication protects your admin login with a TOTP code.
  </p>
  <a class="btn btn-primary" href="{{ url_for('totp_page') }}">Configure 2FA</a>
</div>

<div class="card">
  <h2>API access</h2>
  <p class="muted mt-2">
    Set the <code>API_TOKEN</code> environment variable to protect <code>/api/*</code> endpoints.
    Pass it as <code>Authorization: Bearer &lt;token&gt;</code>.
  </p>
</div>
"""


# ===========================================================================
# TOTP
# ===========================================================================
TOTP_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Security</div>
    <div class="page-title">Two-Factor Authentication</div>
    <div class="page-sub">Protect admin login with a TOTP code</div>
  </div>
  <div class="page-actions">
    <a class="btn" href="{{ url_for('settings_page') }}">← Back</a>
  </div>
</div>

<div class="card">
  {% if enabled %}
    <span class="badge green mb-3">Enabled</span>
    <p class="muted mb-3">
      Your account is protected with TOTP. You will be prompted for a code on every login.
    </p>
    <form method="post" action="{{ url_for('totp_disable') }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="btn btn-danger">Disable 2FA</button>
    </form>
  {% else %}
    <span class="badge amber mb-3">Disabled</span>
    {% if qr_b64 %}
      <p class="muted">Scan this QR code with your authenticator app:</p>
      <img src="data:image/png;base64,{{ qr_b64 }}" alt="QR"
           style="background:#fff;padding:12px;border-radius:12px;margin:16px 0;border:1px solid var(--border)">
      <p class="muted">Or enter this secret manually:</p>
      <p class="mb-3"><code>{{ pending_secret }}</code></p>
      <form method="post" action="{{ url_for('totp_enable') }}">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn btn-success">Enable 2FA</button>
      </form>
    {% else %}
      <p class="muted">pyotp or qrcode not installed. Add them to requirements.</p>
    {% endif %}
  {% endif %}
</div>
"""


# ===========================================================================
# App factory
# ===========================================================================
def _is_https_hint() -> bool:
    return bool(os.environ.get("RAILWAY_PUBLIC_DOMAIN")) or \
        os.environ.get("HTTPS", "") == "on"


def build_admin_app() -> Flask:
    app = Flask(__name__, static_folder=None)

    secret = os.environ.get("SECRET_KEY") or os.environ.get("ADMIN_SECRET_KEY")
    if not secret:
        secret_file = DB_PATH / "flask_secret.txt"
        if secret_file.exists():
            secret = secret_file.read_text().strip()
        else:
            secret = secrets.token_urlsafe(32)
            secret_file.write_text(secret)
    app.secret_key = secret

    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    if _is_https_hint():
        app.config["SESSION_COOKIE_SECURE"] = True

    @app.before_request
    def _attach_csrf():
        _csrf_token()

    def _render_page(title: str, body: str, page: str):
        return render_template_string(
            BASE_TEMPLATE, title=title, body=body, page=page)

    def _get_runtime_safe():
        try:
            from app import _get_runtime
            return _get_runtime()
        except Exception:
            return None

    # ---------- Login ----------
    @app.route("/", methods=["GET"])
    def index():
        if session.get("admin"):
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/admin/login", methods=["GET"])
    @_rate_limit
    def login():
        from .admin_totp import is_enabled as _totp_enabled
        body = render_template_string(
            LOGIN_TEMPLATE, csrf=_csrf_token(), totp_enabled=_totp_enabled(),
        )
        return _render_page("Login", body, "login")

    @app.route("/admin/login", methods=["POST"])
    @_rate_limit
    def login_post():
        _csrf_check()
        pw = request.form.get("password", "")
        if pw != ADMIN_PASSWORD:
            flash("Invalid password", "error")
            return redirect(url_for("login"))

        from .admin_totp import is_enabled as _totp_enabled, verify as _totp_verify
        if _totp_enabled():
            code = request.form.get("totp", "").strip()
            if not _totp_verify(code):
                flash("Invalid 2FA code", "error")
                return redirect(url_for("login"))

        session["admin"] = True
        session["admin_id"] = int(CONFIG.get("owner_id", 0) or 0)
        session["admin_name"] = "owner"
        session.permanent = True
        return redirect(url_for("dashboard"))

    @app.route("/admin/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------- Dashboard ----------
    @app.route("/admin/dashboard")
    @_login_required
    def dashboard():
        tx_total = tx_totals()
        body = render_template_string(
            DASHBOARD_TEMPLATE,
            users_total=user_count(),
            users_active=active_last_24h(),
            users_new=new_last_7d(),
            diamonds=total_in_circulation(),
            granted=tx_total["granted"],
            spent=tx_total["spent"],
            top_spenders=top_spenders(10),
            diamond_series=hourly_consumption(24),
            sub_dist=distribution(),
            approval_series=approval_rate_series(14),
        )
        return _render_page("Dashboard", body, "dashboard")

    # ---------- Users ----------
    @app.route("/admin/users")
    @_login_required
    def users_page():
        raw = all_users()
        enriched = []
        for uid_str, u in raw.items():
            try:
                uid = int(uid_str)
            except ValueError:
                continue
            enriched.append({
                "id": uid,
                "first_name": u.get("first_name", ""),
                "username": u.get("username", ""),
                "plan": effective_plan(uid),
                "days": days_left(uid),
                "diamonds": int(u.get("diamonds", 0) or 0),
                "banned": bool(u.get("banned", False)),
                "casino_unlocked": bool(u.get("casino_unlocked", 0)),
            })
        enriched.sort(key=lambda x: x["id"])
        body = render_template_string(USERS_TEMPLATE, users=enriched)
        return _render_page("Users", body, "users")

    @app.route("/admin/users/<int:uid>")
    @_login_required
    def user_detail(uid: int):
        u = get_user(uid) or {"id": uid}
        body = render_template_string(
            USER_DETAIL_TEMPLATE,
            u={
                "id": uid,
                "first_name": u.get("first_name", ""),
                "diamonds": int(u.get("diamonds", 0) or 0),
                "plan": effective_plan(uid),
                "days": days_left(uid),
                "referral_code": u.get("referral_code", "—"),
                "banned": bool(u.get("banned", False)),
                "casino_unlocked": bool(u.get("casino_unlocked", 0)),
                "created_at": u.get("created_at", ""),
            },
            plans=list(PLANS.keys()),
            txs=tx_for_user(uid, 50),
            csrf=_csrf_token(),
        )
        return _render_page(f"User {uid}", body, "users")

    @app.route("/admin/users/<int:uid>/grant", methods=["POST"])
    @_login_required
    def grant_diamonds(uid: int):
        _csrf_check()
        try:
            amount = int(request.form.get("amount", "0"))
        except ValueError:
            amount = 0
        reason = request.form.get("reason", "admin_grant")
        if amount >= 0:
            grant(uid, amount, reason, meta={"admin": True})
        else:
            spend(uid, -amount, reason, meta={"admin": True})
        audit_record(_current_admin_id(), "grant_diamonds", target=str(uid),
                     ip=request.remote_addr, admin_name=_current_admin_name(),
                     meta={"amount": amount, "reason": reason})
        flash(f"Applied {amount} diamonds to user {uid}.")
        return redirect(url_for("user_detail", uid=uid))

    @app.route("/admin/users/<int:uid>/subscription", methods=["POST"])
    @_login_required
    def set_subscription(uid: int):
        _csrf_check()
        plan = request.form.get("plan", "member")
        try:
            days = int(request.form.get("days", "30"))
        except ValueError:
            days = 30
        auto = request.form.get("auto_renew", "0") == "1"
        set_sub(uid, plan, days=days, auto_renew=auto)
        audit_record(_current_admin_id(), "set_subscription", target=str(uid),
                     ip=request.remote_addr, admin_name=_current_admin_name(),
                     meta={"plan": plan, "days": days})
        flash(f"Subscription set to {plan} for {days} days.")
        return redirect(url_for("user_detail", uid=uid))

    @app.route("/admin/users/<int:uid>/subscription/cancel", methods=["POST"])
    @_login_required
    def cancel_subscription(uid: int):
        _csrf_check()
        cancel_sub(uid)
        audit_record(_current_admin_id(), "cancel_subscription",
                     target=str(uid), ip=request.remote_addr,
                     admin_name=_current_admin_name())
        flash("Subscription cancelled.")
        return redirect(url_for("user_detail", uid=uid))

    @app.route("/admin/users/<int:uid>/ban", methods=["POST"])
    @_login_required
    def toggle_ban(uid: int):
        _csrf_check()
        u = get_user(uid) or {}
        if u.get("banned"):
            unban(uid)
            action = "unban"
        else:
            ban(uid)
            action = "ban"
        audit_record(_current_admin_id(), action, target=str(uid),
                     ip=request.remote_addr, admin_name=_current_admin_name())
        return redirect(url_for("user_detail", uid=uid))

    @app.route("/admin/users/<int:uid>/delete", methods=["POST"])
    @_login_required
    def delete_user_route(uid: int):
        _csrf_check()
        delete_user(uid)
        audit_record(_current_admin_id(), "delete_user", target=str(uid),
                     ip=request.remote_addr, admin_name=_current_admin_name())
        flash(f"User {uid} deleted.")
        return redirect(url_for("users_page"))

    # ---------- Requests ----------
    @app.route("/admin/requests")
    @_login_required
    def requests_page():
        body = render_template_string(
            REQUESTS_TEMPLATE,
            requests=all_requests()[:200],
            csrf=_csrf_token(),
        )
        return _render_page("Requests", body, "requests")

    @app.route("/admin/requests/<rid>/approve", methods=["POST"])
    @_login_required
    def approve_request(rid: str):
        _csrf_check()
        approve(rid, _current_admin_id())
        audit_record(_current_admin_id(), "approve_request", target=rid,
                     ip=request.remote_addr, admin_name=_current_admin_name())
        flash(f"Request {rid} approved.")
        return redirect(url_for("requests_page"))

    @app.route("/admin/requests/<rid>/reject", methods=["POST"])
    @_login_required
    def reject_request(rid: str):
        _csrf_check()
        reject(rid, _current_admin_id())
        audit_record(_current_admin_id(), "reject_request", target=rid,
                     ip=request.remote_addr, admin_name=_current_admin_name())
        flash(f"Request {rid} rejected.")
        return redirect(url_for("requests_page"))

    # ---------- Transactions ----------
    @app.route("/admin/transactions")
    @_login_required
    def transactions_page():
        body = render_template_string(
            TX_TEMPLATE, txs=tx_recent(200), totals=tx_totals(),
        )
        return _render_page("Transactions", body, "tx")

    # ---------- Shop ----------
    @app.route("/admin/shop")
    @_login_required
    def shop_page():
        body = render_template_string(
            SHOP_TEMPLATE, items=shop_items(), csrf=_csrf_token(),
        )
        return _render_page("Shop", body, "shop")

    @app.route("/admin/shop/save", methods=["POST"])
    @_login_required
    def shop_save():
        _csrf_check()
        item_id = request.form.get("item_id", "").strip()
        if not item_id:
            flash("Item ID required.", "error")
            return redirect(url_for("shop_page"))
        existing = shop_items().get(item_id, {})
        try:
            price = int(request.form.get("price", "0") or 0)
        except ValueError:
            price = 0
        try:
            max_per_user = int(request.form.get("max_per_user", "1") or 1)
        except ValueError:
            max_per_user = 1
        set_item(item_id, {
            "name": request.form.get("name", item_id),
            "description": request.form.get("description", ""),
            "price": price,
            "max_per_user": max_per_user,
            "effect": existing.get("effect", {}),
        })
        audit_record(_current_admin_id(), "shop_save", target=item_id,
                     ip=request.remote_addr, admin_name=_current_admin_name())
        flash(f"Item {item_id} saved.")
        return redirect(url_for("shop_page"))

    @app.route("/admin/shop/delete/<item_id>", methods=["POST"])
    @_login_required
    def shop_delete(item_id: str):
        _csrf_check()
        delete_item(item_id)
        audit_record(_current_admin_id(), "shop_delete", target=item_id,
                     ip=request.remote_addr, admin_name=_current_admin_name())
        flash(f"Item {item_id} deleted.")
        return redirect(url_for("shop_page"))

    # ---------- Audit ----------
    @app.route("/admin/audit")
    @_login_required
    def audit_page():
        body = render_template_string(AUDIT_TEMPLATE, entries=audit_all(500))
        return _render_page("Audit", body, "audit")

    # ---------- Backups ----------
    @app.route("/admin/backups")
    @_login_required
    def backups_page():
        backups_dir = DB_PATH / "backups"
        items = []
        if backups_dir.exists():
            files = sorted(backups_dir.glob("*.zip"),
                           key=lambda x: x.stat().st_mtime, reverse=True)
            for f in files:
                stat = f.stat()
                items.append({
                    "name": f.name,
                    "size_kb": round(stat.st_size / 1024, 1),
                    "modified": datetime.fromtimestamp(
                        stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                })
        body = render_template_string(BACKUPS_TEMPLATE, backups=items,
                                      csrf=_csrf_token())
        return _render_page("Backups", body, "backups")

    @app.route("/admin/backups/now", methods=["POST"])
    @_login_required
    def backup_now():
        _csrf_check()
        from .backup import create_backup_zip, send_backup_to_owner
        try:
            path = create_backup_zip()
            send_backup_to_owner(path)
            audit_record(_current_admin_id(), "backup_now",
                         ip=request.remote_addr,
                         admin_name=_current_admin_name())
            flash(f"Backup created: {path.name}")
        except Exception as e:
            log_flask.error(f"manual backup failed: {e}")
            flash(f"Backup failed: {e}", "error")
        return redirect(url_for("backups_page"))

    @app.route("/admin/backups/download/<name>")
    @_login_required
    def download_backup(name: str):
        backups_dir = (DB_PATH / "backups").resolve()
        target = (backups_dir / name).resolve()
        if not str(target).startswith(str(backups_dir)) or not target.exists():
            abort(404)
        return send_file(target, as_attachment=True, download_name=name)

    @app.route("/admin/backups/restore", methods=["POST"])
    @_login_required
    def restore_backup():
        _csrf_check()
        from .backup import restore_from_zip
        f = request.files.get("file")
        if not f or not f.filename.endswith(".zip"):
            flash("Upload a .zip file.", "error")
            return redirect(url_for("backups_page"))
        try:
            restore_from_zip(f.read())
            audit_record(_current_admin_id(), "restore_backup",
                         ip=request.remote_addr,
                         admin_name=_current_admin_name())
            flash("Backup restored successfully.")
        except Exception as e:
            log_flask.error(f"restore failed: {e}")
            flash(f"Restore failed: {e}", "error")
        return redirect(url_for("backups_page"))

    # ---------- Broadcast ----------
    @app.route("/admin/broadcast")
    @_login_required
    def broadcast_page():
        body = render_template_string(BROADCAST_TEMPLATE, csrf=_csrf_token())
        return _render_page("Broadcast", body, "broadcast")

    @app.route("/admin/broadcast/send", methods=["POST"])
    @_login_required
    def broadcast_send():
        _csrf_check()
        msg = request.form.get("message", "").strip()
        if not msg:
            flash("Empty message.", "error")
            return redirect(url_for("broadcast_page"))

        rt = _get_runtime_safe()
        if not rt or not rt.bot_client:
            flash("Bot not connected.", "error")
            return redirect(url_for("broadcast_page"))

        try:
            from .bootstrap import _async_loop
        except Exception:
            _async_loop = None

        if _async_loop is None or not _async_loop.is_running():
            flash("Bot event loop is not running.", "error")
            return redirect(url_for("broadcast_page"))

        import asyncio

        async def _do():
            sent = 0
            for uid_str, u in all_users().items():
                if u.get("banned"):
                    continue
                try:
                    await rt.bot_client.send_message(int(uid_str), msg)
                    sent += 1
                except Exception:
                    pass
            return sent

        try:
            fut = asyncio.run_coroutine_threadsafe(_do(), _async_loop)
            sent = fut.result(timeout=180)
            audit_record(_current_admin_id(), "broadcast",
                         ip=request.remote_addr,
                         admin_name=_current_admin_name(),
                         meta={"sent": sent})
            flash(f"Broadcast sent to {sent} users.")
        except Exception as e:
            flash(f"Broadcast failed: {e}", "error")
        return redirect(url_for("broadcast_page"))

    # ---------- Settings ----------
    @app.route("/admin/settings")
    @_login_required
    def settings_page():
        body = render_template_string(SETTINGS_TEMPLATE, cfg=CONFIG,
                                      csrf=_csrf_token())
        return _render_page("Settings", body, "settings")

    @app.route("/admin/settings/save", methods=["POST"])
    @_login_required
    def settings_save():
        _csrf_check()
        int_fields = ("signup_bonus", "diamond_cost_clock", "diamond_cost_job",
                      "referral_bonus", "interval")
        str_fields = ("timezone", "base_name")
        for key in int_fields:
            val = request.form.get(key)
            if val is None:
                continue
            try:
                n = int(val)
                if key == "interval":
                    n = max(1, min(60, n))
                CONFIG[key] = n
            except ValueError:
                pass
        for key in str_fields:
            val = request.form.get(key)
            if val is not None:
                CONFIG[key] = val
        save_config(CONFIG)
        audit_record(_current_admin_id(), "settings_save",
                     ip=request.remote_addr,
                     admin_name=_current_admin_name())
        flash("Settings saved.")
        return redirect(url_for("settings_page"))

    # ---------- TOTP ----------
    @app.route("/admin/settings/2fa", methods=["GET"])
    @_login_required
    def totp_page():
        from .admin_totp import HAS_PYOTP, get_secret, is_enabled, provision
        import base64 as _b64

        enabled = is_enabled()
        secret = get_secret() if enabled else None
        qr_b64 = ""
        if not enabled and HAS_PYOTP:
            s, uri, png = provision()
            session["pending_totp_secret"] = s
            if png:
                qr_b64 = _b64.b64encode(png).decode()

        body = render_template_string(
            TOTP_TEMPLATE,
            enabled=enabled, secret=secret, qr_b64=qr_b64,
            pending_secret=session.get("pending_totp_secret", ""),
            csrf=_csrf_token(),
        )
        return _render_page("2FA", body, "settings")

    @app.route("/admin/settings/2fa/enable", methods=["POST"])
    @_login_required
    def totp_enable():
        _csrf_check()
        from .admin_totp import enable as _enable
        secret = session.get("pending_totp_secret", "")
        if not secret:
            flash("No pending secret. Reload the page.", "error")
            return redirect(url_for("totp_page"))
        _enable(secret)
        session.pop("pending_totp_secret", None)
        audit_record(_current_admin_id(), "totp_enable",
                     ip=request.remote_addr,
                     admin_name=_current_admin_name())
        flash("2FA enabled.")
        return redirect(url_for("totp_page"))

    @app.route("/admin/settings/2fa/disable", methods=["POST"])
    @_login_required
    def totp_disable():
        _csrf_check()
        from .admin_totp import disable as _disable
        _disable()
        audit_record(_current_admin_id(), "totp_disable",
                     ip=request.remote_addr,
                     admin_name=_current_admin_name())
        flash("2FA disabled.")
        return redirect(url_for("totp_page"))

    return app