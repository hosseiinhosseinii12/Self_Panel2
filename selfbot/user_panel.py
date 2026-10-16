"""User panel — OTP-based login, dark theme synced with admin.

Entry point: /user/login — user enters the 6-digit OTP given by the bot.
After login, session keeps user_id.
"""
import secrets
from datetime import datetime

from flask import (Blueprint, abort, flash, redirect, render_template_string,
                   request, session, url_for)

from .config import CONFIG
from .economy import get_balance
from .logging_setup import log_flask
from .requests_mod import (create_diamond_request, create_subscription_request,
                           for_user as req_for_user)
from .subscriptions import effective_plan
from .transactions import for_user as tx_for_user
from .users import ensure_user, get_user, get_user_settings, update_user_settings
from . import db
from . import user_otp as OTP
from . import text_format as TF
from . import jobs as J
from .fonts import NAME_FONTS, DIGIT_FONTS

bp = Blueprint("user", __name__, url_prefix="/user")


# ===========================================================================
# Session helpers
# ===========================================================================
def _current_uid():
    return session.get("user_id")


def _login_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*a, **kw):
        if not _current_uid():
            return redirect(url_for("user.login_page"))
        return fn(*a, **kw)
    return wrapper


def _csrf_token() -> str:
    tok = session.get("csrf_user")
    if not tok:
        tok = secrets.token_urlsafe(24)
        session["csrf_user"] = tok
    return tok


def _csrf_check() -> None:
    tok = request.form.get("csrf")
    if not tok or tok != session.get("csrf_user"):
        abort(400, "CSRF check failed")


def _redirect_home():
    return redirect(url_for("user.dashboard"))


# ===========================================================================
# BASE TEMPLATE — dark theme, synced with admin
# ===========================================================================
BASE_TEMPLATE = r"""
<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} · SELF BOT</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0a0a0c;
  --bg-2: #101014;
  --surface: #131318;
  --surface-2: #17171d;
  --surface-3: #1c1c23;

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

  --r-sm: 8px;
  --r-md: 12px;
  --r-lg: 16px;
  --r-xl: 20px;
  --r-2xl: 24px;
  --r-pill: 999px;

  --sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --mono: 'JetBrains Mono', ui-monospace, monospace;

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
  --green-bg: #f0fdf4;
  --green-border: #bbf7d0;
  --red-bg: #fef2f2;
  --red-border: #fecaca;
  --amber-bg: #fffbeb;
  --amber-border: #fde68a;
  --blue-bg: #eff6ff;
  --blue-border: #bfdbfe;
  --purple-bg: #f5f3ff;
  --purple-border: #ddd6fe;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html { -webkit-text-size-adjust: 100%; }

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
a:hover { text-decoration: underline; text-underline-offset: 3px; }

::selection { background: var(--accent); color: var(--accent-fg); }

::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border-2); border-radius: var(--r-pill); border: 2px solid var(--bg); }
::-webkit-scrollbar-thumb:hover { background: var(--border-3); }

.layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  min-height: 100vh;
}

@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  .sidebar { display: none !important; }
}

/* ---------- Sidebar ---------- */
.sidebar {
  background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 24px 14px 16px;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 8px 24px;
  font-weight: 800;
  font-size: 14.5px;
  letter-spacing: -0.02em;
}

.brand-mark {
  width: 30px;
  height: 30px;
  background: var(--accent);
  color: var(--accent-fg);
  border-radius: 10px;
  display: grid;
  place-items: center;
  font-size: 14px;
  font-weight: 900;
  transition: transform var(--t-base) var(--ease);
  box-shadow: 0 4px 12px rgba(10, 10, 10, 0.15);
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
  padding: 18px 10px 8px;
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
  left: -14px;
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
  font-family: inherit;
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

/* ---------- Main ---------- */
.main {
  padding: 32px 32px 80px;
  max-width: 1200px;
  width: 100%;
}

@media (max-width: 640px) {
  .main { padding: 20px 16px 60px; }
}

.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 28px;
  flex-wrap: wrap;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border);
}

.page-title {
  font-size: 28px;
  font-weight: 800;
  letter-spacing: -0.04em;
  line-height: 1.1;
  background: linear-gradient(180deg, var(--text) 0%, var(--text-2) 120%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.page-sub {
  color: var(--text-2);
  font-size: 13.5px;
  margin-top: 6px;
}

.page-actions { display: flex; gap: 8px; flex-wrap: wrap; }

.crumb {
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--text-3);
  margin-bottom: 8px;
}

/* ---------- Buttons ---------- */
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
  cursor: pointer;
  font-family: inherit;
  text-decoration: none;
  transition: all var(--t-fast) var(--ease);
  line-height: 1.2;
  white-space: nowrap;
}

.btn:hover {
  background: var(--hover);
  border-color: var(--border-3);
  transform: translateY(-1px);
  box-shadow: var(--shadow-xs);
  text-decoration: none;
}

.btn:active { transform: translateY(0); }

.btn-primary {
  background: var(--accent);
  color: var(--accent-fg);
  border-color: var(--accent);
}
.btn-primary:hover { filter: brightness(1.1); }

.btn-danger {
  background: var(--red);
  color: #fff;
  border-color: var(--red);
}
.btn-danger:hover { background: #dc2626; filter: none; }

.btn-success {
  background: var(--green);
  color: #fff;
  border-color: var(--green);
}
.btn-success:hover { background: #16a34a; filter: none; }

.btn-sm { padding: 6px 12px; font-size: 12.5px; border-radius: 7px; }

/* ---------- Forms ---------- */
label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
  margin: 12px 0 6px;
  letter-spacing: 0.005em;
}

label:first-child { margin-top: 0; }

input, select, textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border-2);
  border-radius: var(--r-sm);
  background: var(--surface-2);
  color: var(--text);
  font-size: 13.5px;
  font-family: inherit;
  outline: none;
  transition: all var(--t-fast) var(--ease);
}

input:hover, select:hover, textarea:hover { border-color: var(--border-3); }
input:focus, select:focus, textarea:focus {
  border-color: var(--accent);
  box-shadow: var(--shadow-ring);
  background: var(--surface);
}

input::placeholder, textarea::placeholder { color: var(--text-4); }

.field-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
.field-row > * { flex: 1; min-width: 140px; }

/* ---------- Cards ---------- */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: 22px;
  margin-bottom: 18px;
  box-shadow: var(--shadow-xs);
  transition: box-shadow var(--t-base) var(--ease), border-color var(--t-base) var(--ease);
}

.card:hover { box-shadow: var(--shadow-sm); border-color: var(--border-2); }

.card h2 {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.015em;
  margin-bottom: 16px;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.card h2 .sub {
  font-size: 12px;
  color: var(--text-3);
  font-weight: 500;
  margin-left: auto;
}

/* ---------- Stats ---------- */
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
  margin-bottom: 24px;
}

.stat {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: 18px;
  box-shadow: var(--shadow-xs);
  transition: transform var(--t-base) var(--ease-out), box-shadow var(--t-base) var(--ease);
  position: relative;
  overflow: hidden;
}

.stat::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  opacity: 0;
  transition: opacity var(--t-base) var(--ease);
}

.stat:hover { transform: translateY(-2px); box-shadow: var(--shadow-md); }
.stat:hover::before { opacity: 0.6; }

.stat .k {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--text-3);
}

.stat .v {
  font-size: 26px;
  font-weight: 800;
  letter-spacing: -0.035em;
  margin-top: 8px;
  font-variant-numeric: tabular-nums;
  color: var(--text);
  line-height: 1.1;
}

.stat .v small { font-size: 13px; color: var(--text-3); font-weight: 500; margin-left: 4px; }

/* ---------- Tables ---------- */
.table-wrap {
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  overflow: hidden;
  background: var(--surface);
  box-shadow: var(--shadow-xs);
}

table { width: 100%; border-collapse: collapse; }

thead th {
  background: var(--surface-2);
  color: var(--text-2);
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  text-align: left;
  padding: 12px 18px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

tbody td {
  padding: 12px 18px;
  border-bottom: 1px solid var(--border);
  font-size: 13.5px;
  letter-spacing: -0.005em;
}

tbody tr:last-child td { border-bottom: none; }
tbody tr { transition: background var(--t-fast) var(--ease); }
tbody tr:hover td { background: var(--hover); }

.mono { font-family: var(--mono); font-size: 12.5px; font-feature-settings: 'tnum'; }
.muted { color: var(--text-2); }
.dim { color: var(--text-3); }
.text-right { text-align: right; }
.text-center { text-align: center; }

/* ---------- Badges ---------- */
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
  white-space: nowrap;
  line-height: 1.5;
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

/* ---------- Flash ---------- */
.flash {
  padding: 12px 16px;
  border-radius: var(--r-md);
  font-size: 13.5px;
  font-weight: 500;
  margin-bottom: 16px;
  background: var(--green-bg);
  color: var(--green);
  border: 1px solid var(--green-border);
  display: flex;
  align-items: center;
  gap: 10px;
  animation: flash-in var(--t-slow) var(--ease-out);
}

.flash.err { background: var(--red-bg); color: var(--red); border-color: var(--red-border); }

@keyframes flash-in {
  from { opacity: 0; transform: translateY(-6px); }
  to { opacity: 1; transform: translateY(0); }
}

/* ---------- Empty ---------- */
.empty {
  padding: 40px 20px;
  text-align: center;
  color: var(--text-3);
  font-size: 13.5px;
}

.empty-icon { font-size: 32px; margin-bottom: 8px; opacity: 0.4; }

/* ---------- Utilities ---------- */
.mt-2 { margin-top: 8px; }
.mt-3 { margin-top: 16px; }
.mt-4 { margin-top: 24px; }
.mb-2 { margin-bottom: 8px; }
.mb-3 { margin-bottom: 16px; }
.mb-4 { margin-bottom: 24px; }

code {
  font-family: var(--mono);
  font-size: 12.5px;
  background: var(--surface-3);
  padding: 2px 7px;
  border-radius: 5px;
  color: var(--text);
}

/* ==========================================================================
   OTP LOGIN PAGE
   ========================================================================== */
.otp-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 40px 20px;
  background: var(--bg);
  position: relative;
  overflow: hidden;
}

.otp-page::before,
.otp-page::after {
  content: "";
  position: absolute;
  border-radius: 50%;
  filter: blur(100px);
  pointer-events: none;
  opacity: 0.5;
}
.otp-page::before {
  width: 500px; height: 500px;
  background: rgba(245, 245, 247, 0.05);
  top: -200px; left: -200px;
}
.otp-page::after {
  width: 400px; height: 400px;
  background: rgba(245, 245, 247, 0.04);
  bottom: -150px; right: -150px;
}

.otp-card {
  width: 100%;
  max-width: 460px;
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

.otp-mark {
  width: 60px;
  height: 60px;
  background: var(--accent);
  color: var(--accent-fg);
  border-radius: var(--r-lg);
  display: grid;
  place-items: center;
  font-size: 28px;
  margin: 0 auto 24px;
  box-shadow: 0 8px 24px rgba(255, 255, 255, 0.08);
  transition: transform var(--t-base) var(--ease);
}

.otp-card:hover .otp-mark { transform: rotate(-6deg) scale(1.04); }

.otp-card h1 {
  font-size: 22px;
  font-weight: 800;
  text-align: center;
  letter-spacing: -0.035em;
  color: var(--text);
  line-height: 1.2;
}

.otp-card .sub {
  text-align: center;
  color: var(--text-2);
  font-size: 13.5px;
  margin: 8px 0 32px;
  line-height: 1.6;
}

.otp-card .sub strong {
  color: var(--text);
  font-weight: 600;
}

/* ---------- OTP INPUT ---------- */
.otp-input-wrap {
  display: flex;
  justify-content: center;
  gap: 10px;
  margin: 8px 0 24px;
  direction: ltr;
}

.otp-cell {
  width: 52px;
  height: 64px;
  border: 2px solid var(--border-2);
  border-radius: var(--r-md);
  background: var(--surface-2);
  color: var(--text);
  font-family: var(--mono);
  font-size: 26px;
  font-weight: 700;
  text-align: center;
  letter-spacing: 0;
  outline: none;
  transition: all var(--t-fast) var(--ease);
  padding: 0;
  caret-color: var(--accent);
}

.otp-cell:hover { border-color: var(--border-3); background: var(--surface-3); }

.otp-cell:focus {
  border-color: var(--accent);
  background: var(--surface);
  box-shadow: 0 0 0 4px rgba(245, 245, 247, 0.08), 0 4px 12px rgba(0, 0, 0, 0.2);
  transform: translateY(-2px) scale(1.03);
}

.otp-cell.filled {
  border-color: var(--accent);
  background: var(--surface);
}

.otp-cell.error {
  border-color: var(--red);
  background: var(--red-bg);
  animation: shake 300ms var(--ease);
}

@keyframes shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-6px); }
  75% { transform: translateX(6px); }
}

.otp-cell.success {
  border-color: var(--green);
  background: var(--green-bg);
}

@media (max-width: 480px) {
  .otp-cell { width: 42px; height: 54px; font-size: 22px; }
  .otp-input-wrap { gap: 6px; }
}

.otp-submit {
  width: 100%;
  padding: 13px 20px;
  font-size: 14px;
  font-weight: 600;
  border-radius: var(--r-md);
  margin-top: 8px;
}

.otp-help {
  text-align: center;
  color: var(--text-3);
  font-size: 12.5px;
  margin-top: 24px;
  line-height: 1.7;
}

.otp-help code {
  font-size: 12px;
  background: var(--surface-3);
  padding: 2px 6px;
}

.otp-timer {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-3);
  font-size: 12px;
  font-weight: 500;
  margin-top: 16px;
  padding: 6px 14px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--r-pill);
}

.otp-timer .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--green);
  animation: pulse 2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
</head>
<body>
{{ body|safe }}

<script>
(function() {
  const stored = localStorage.getItem('user-theme');
  if (stored === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
  }
})();
function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('user-theme', next);
}
</script>
</body>
</html>
"""


# ===========================================================================
# LOGIN TEMPLATE (OTP)
# ===========================================================================
LOGIN_TEMPLATE = r"""
<div class="otp-page">
  <form class="otp-card" method="post" action="{{ url_for('user.login_post') }}"
        id="otp-form" autocomplete="off">
    <div class="otp-mark">🔐</div>
    <h1>Sign in to your panel</h1>
    <p class="sub">
      Send <strong>/web</strong> to the bot in Telegram to receive a
      <strong>6-digit code</strong>, then enter it here.
    </p>

    <input type="hidden" name="csrf" value="{{ csrf }}">
    <input type="hidden" name="otp" id="otp-hidden" value="">

    <div class="otp-input-wrap" id="otp-wrap">
      <input class="otp-cell" type="text" inputmode="numeric" maxlength="1"
             pattern="[0-9]*" data-idx="0" autofocus>
      <input class="otp-cell" type="text" inputmode="numeric" maxlength="1"
             pattern="[0-9]*" data-idx="1">
      <input class="otp-cell" type="text" inputmode="numeric" maxlength="1"
             pattern="[0-9]*" data-idx="2">
      <input class="otp-cell" type="text" inputmode="numeric" maxlength="1"
             pattern="[0-9]*" data-idx="3">
      <input class="otp-cell" type="text" inputmode="numeric" maxlength="1"
             pattern="[0-9]*" data-idx="4">
      <input class="otp-cell" type="text" inputmode="numeric" maxlength="1"
             pattern="[0-9]*" data-idx="5">
    </div>

    <button type="submit" class="btn btn-primary otp-submit" id="otp-submit">
      Verify &amp; Sign in
    </button>

    <div class="otp-timer">
      <span class="dot"></span>
      <span id="otp-timer-text">Code valid for 5 minutes</span>
    </div>

    <div class="otp-help">
      Open Telegram &rarr; your bot &rarr; send <code>/web</code>
    </div>

    {% with msgs = get_flashed_messages(with_categories=true) %}
      {% for cat, msg in msgs %}
        <div class="flash {{ 'err' if cat=='error' else '' }}" style="margin-top:20px">{{ msg }}</div>
      {% endfor %}
    {% endwith %}
  </form>
</div>

<script>
(function() {
  const cells = Array.from(document.querySelectorAll('.otp-cell'));
  const hidden = document.getElementById('otp-hidden');
  const form = document.getElementById('otp-form');

  function updateHidden() {
    hidden.value = cells.map(c => c.value).join('');
  }

  cells.forEach((cell, i) => {
    cell.addEventListener('input', (e) => {
      const v = e.target.value.replace(/\D/g, '');
      e.target.value = v.slice(-1);
      e.target.classList.toggle('filled', !!e.target.value);
      updateHidden();
      if (e.target.value && i < cells.length - 1) {
        cells[i + 1].focus();
      }
      if (hidden.value.length === 6) {
        // auto-submit after slight delay for UX
        setTimeout(() => form.submit(), 150);
      }
    });

    cell.addEventListener('keydown', (e) => {
      if (e.key === 'Backspace' && !e.target.value && i > 0) {
        cells[i - 1].focus();
        cells[i - 1].value = '';
        cells[i - 1].classList.remove('filled');
        updateHidden();
        e.preventDefault();
      }
      if (e.key === 'ArrowLeft' && i > 0) cells[i - 1].focus();
      if (e.key === 'ArrowRight' && i < cells.length - 1) cells[i + 1].focus();
      if (e.key === 'Enter') {
        updateHidden();
        form.submit();
      }
    });

    cell.addEventListener('paste', (e) => {
      e.preventDefault();
      const pasted = (e.clipboardData || window.clipboardData)
        .getData('text').replace(/\D/g, '').slice(0, 6);
      if (!pasted) return;
      for (let j = 0; j < pasted.length && i + j < cells.length; j++) {
        cells[i + j].value = pasted[j];
        cells[i + j].classList.add('filled');
      }
      updateHidden();
      const next = Math.min(i + pasted.length, cells.length - 1);
      cells[next].focus();
      if (hidden.value.length === 6) {
        setTimeout(() => form.submit(), 150);
      }
    });
  });

  form.addEventListener('submit', () => {
    updateHidden();
    cells.forEach(c => c.classList.add('success'));
  });
})();
</script>
"""


# ===========================================================================
# DASHBOARD
# ===========================================================================
DASHBOARD_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Overview</div>
    <div class="page-title">Dashboard</div>
    <div class="page-sub">Welcome back{% if first_name %}, {{ first_name }}{% endif %}</div>
  </div>
  <div class="page-actions">
    <span class="badge">ID {{ user_id }}</span>
  </div>
</div>

<div class="stats">
  <div class="stat">
    <div class="k">Balance</div>
    <div class="v">{{ diamonds }}<small>💎</small></div>
  </div>
  <div class="stat">
    <div class="k">Clock</div>
    <div class="v" style="font-size:22px">{% if clock_on %}🟢 ON{% else %}🔴 OFF{% endif %}</div>
  </div>
  <div class="stat">
    <div class="k">Interval</div>
    <div class="v">{{ interval }}<small>min</small></div>
  </div>
  <div class="stat">
    <div class="k">Text style</div>
    <div class="v" style="font-size:18px">{{ text_style }}</div>
  </div>
</div>

<div class="card">
  <h2>Quick actions</h2>
  <div class="page-actions">
    <a class="btn btn-primary" href="{{ url_for('user.clock_page') }}">Clock settings</a>
    <a class="btn" href="{{ url_for('user.appearance_page') }}">Appearance</a>
    <a class="btn" href="{{ url_for('user.textformat_page') }}">Text style</a>
    <a class="btn" href="{{ url_for('user.jobs_page') }}">Jobs</a>
  </div>
</div>

<div class="card">
  <h2>Recent transactions <span class="sub">{{ txs|length }}</span></h2>
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
      <tr><td colspan="5" class="empty"><div class="empty-icon">💎</div>No transactions yet</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
"""


# ===========================================================================
# CLOCK
# ===========================================================================
CLOCK_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Settings</div>
    <div class="page-title">Clock</div>
    <div class="page-sub">Auto-update your Telegram profile name</div>
  </div>
  <div class="page-actions">
    <a class="btn" href="{{ url_for('user.dashboard') }}">← Back</a>
  </div>
</div>

<div class="stats">
  <div class="stat">
    <div class="k">Status</div>
    <div class="v" style="font-size:22px">{% if clock_on %}🟢 ON{% else %}🔴 OFF{% endif %}</div>
  </div>
  <div class="stat">
    <div class="k">Interval</div>
    <div class="v">{{ interval }}<small>min</small></div>
  </div>
  <div class="stat">
    <div class="k">Timezone</div>
    <div class="v" style="font-size:18px">{{ timezone }}</div>
  </div>
  <div class="stat">
    <div class="k">Last written</div>
    <div class="v mono" style="font-size:15px">{{ last_written or '—' }}</div>
  </div>
</div>

<div class="card">
  <h2>Toggle clock</h2>
  <div class="page-actions">
    <form method="post" action="{{ url_for('user.clock_toggle') }}" style="display:inline">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <input type="hidden" name="action" value="on">
      <button class="btn btn-success" {% if clock_on %}disabled{% endif %}>Turn ON</button>
    </form>
    <form method="post" action="{{ url_for('user.clock_toggle') }}" style="display:inline">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <input type="hidden" name="action" value="off">
      <button class="btn btn-danger" {% if not clock_on %}disabled{% endif %}>Turn OFF</button>
    </form>
  </div>
</div>

<div class="card">
  <h2>Interval &amp; timezone</h2>
  <form method="post" action="{{ url_for('user.clock_settings') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="field-row">
      <div>
        <label>Interval (minutes)</label>
        <input type="number" name="interval" value="{{ interval }}" min="1" max="60" required>
      </div>
      <div>
        <label>Timezone</label>
        <input type="text" name="timezone" value="{{ timezone }}" placeholder="Asia/Tehran" required>
      </div>
      <button class="btn btn-primary">Save</button>
    </div>
  </form>
</div>
"""


# ===========================================================================
# APPEARANCE
# ===========================================================================
APPEARANCE_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Settings</div>
    <div class="page-title">Appearance</div>
    <div class="page-sub">Base name and fonts</div>
  </div>
  <div class="page-actions">
    <a class="btn" href="{{ url_for('user.dashboard') }}">← Back</a>
  </div>
</div>

<div class="card">
  <h2>Preview</h2>
  <div class="stat" style="max-width:400px">
    <div class="k">Preview</div>
    <div class="v" style="font-size:22px">{{ preview_name }} {{ preview_clock }}</div>
  </div>
</div>

<div class="card">
  <h2>Base name</h2>
  <form method="post" action="{{ url_for('user.appearance_save') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <label>Base name</label>
    <input type="text" name="base_name" value="{{ base_name }}" maxlength="32" required>

    <label>Name font</label>
    <select name="name_font">
      {% for f in name_fonts %}
      <option value="{{ f }}" {% if f == name_font %}selected{% endif %}>{{ f }}</option>
      {% endfor %}
    </select>

    <label>Clock font</label>
    <select name="clock_font">
      {% for f in clock_fonts %}
      <option value="{{ f }}" {% if f == clock_font %}selected{% endif %}>{{ f }}</option>
      {% endfor %}
    </select>

    <div class="mt-3">
      <button class="btn btn-primary">Save appearance</button>
    </div>
  </form>
</div>
"""


# ===========================================================================
# TEXT FORMAT
# ===========================================================================
TEXTFORMAT_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Settings</div>
    <div class="page-title">Text style</div>
    <div class="page-sub">Auto-format your outgoing messages</div>
  </div>
  <div class="page-actions">
    <a class="btn" href="{{ url_for('user.dashboard') }}">← Back</a>
  </div>
</div>

<div class="card">
  <h2>Active styles</h2>
  <form method="post" action="{{ url_for('user.textformat_save') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px">
      {% for key, label in formats %}
      <label style="display:flex;align-items:center;gap:8px;padding:14px 16px;border:1px solid var(--border-2);border-radius:var(--r-md);cursor:pointer;background:var(--surface-2);margin:0">
        <input type="checkbox" name="{{ key }}" {% if settings[key] %}checked{% endif %}
               style="width:auto;margin:0;accent-color:var(--accent)">
        <span style="font-weight:600;font-size:13.5px">{{ label }}</span>
      </label>
      {% endfor %}
    </div>

    <label class="mt-3">Chat filter (comma-separated IDs, or empty for all)</label>
    <input type="text" name="chats_filter" value="{{ chats_filter }}"
           placeholder="e.g. -1001234567,-1007654321">

    <div class="mt-3">
      <button class="btn btn-primary">Save styles</button>
    </div>
  </form>
</div>

<div class="card">
  <h2>How it works</h2>
  <p class="muted" style="font-size:13.5px;line-height:1.7">
    When any of the styles above are enabled, the bot <strong>edits</strong>
    each of your outgoing messages right after you send it, wrapping it with
    the selected Telegram markdown formatting.
    <br><br>
    If a chat filter is set, only messages in those chats are affected.
    Leave it empty to apply everywhere.
  </p>
</div>
"""


# ===========================================================================
# JOBS
# ===========================================================================
JOBS_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Manage</div>
    <div class="page-title">Jobs</div>
    <div class="page-sub">Repeat jobs you have running</div>
  </div>
  <div class="page-actions">
    <a class="btn" href="{{ url_for('user.dashboard') }}">← Back</a>
  </div>
</div>

<div class="table-wrap">
  <table>
    <thead>
      <tr><th>ID</th><th>Target</th><th>Interval</th><th>Duration</th>
      <th class="text-right">Sent</th><th></th></tr>
    </thead>
    <tbody>
    {% for j in jobs %}
    <tr>
      <td class="mono">{{ j.id }}</td>
      <td class="mono">{{ j.chat_id }}</td>
      <td class="mono">{{ j.interval }}s</td>
      <td class="mono">{% if j.duration == 0 %}∞{% else %}{{ j.duration }}m{% endif %}</td>
      <td class="mono text-right">{{ j.sent }}</td>
      <td class="text-right">
        <form method="post" action="{{ url_for('user.job_stop', job_id=j.id) }}" style="display:inline">
          <input type="hidden" name="csrf" value="{{ csrf }}">
          <button class="btn btn-danger btn-sm">Stop</button>
        </form>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="6" class="empty"><div class="empty-icon">🔁</div>No active jobs. Create one from the bot.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
"""


# ===========================================================================
# ACCOUNT
# ===========================================================================
ACCOUNT_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Account</div>
    <div class="page-title">Account</div>
    <div class="page-sub">Diamonds and requests</div>
  </div>
  <div class="page-actions">
    <a class="btn" href="{{ url_for('user.dashboard') }}">← Back</a>
  </div>
</div>

<div class="stats">
  <div class="stat">
    <div class="k">Balance</div>
    <div class="v">{{ diamonds }}<small>💎</small></div>
  </div>
  <div class="stat">
    <div class="k">Referral code</div>
    <div class="v mono" style="font-size:16px">{{ referral_code }}</div>
  </div>
</div>

<div class="card">
  <h2>Request diamonds</h2>
  <form method="post" action="{{ url_for('user.request_diamonds') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="field-row">
      <div style="flex:0 0 200px">
        <label>Amount (1–10000)</label>
        <input type="number" name="amount" value="50" min="1" max="10000" required>
      </div>
      <button class="btn btn-primary">Send request</button>
    </div>
  </form>
</div>

<div class="card">
  <h2>Request subscription</h2>
  <form method="post" action="{{ url_for('user.request_subscription') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="field-row">
      <div style="flex:0 0 260px">
        <label>Plan</label>
        <select name="plan">
          <option value="basic">Basic — 30 days</option>
          <option value="pro">Pro — 30 days</option>
          <option value="vip">VIP — 30 days</option>
        </select>
      </div>
      <button class="btn btn-primary">Send request</button>
    </div>
  </form>
</div>

<div class="card">
  <h2>My requests <span class="sub">{{ requests|length }}</span></h2>
  <div class="table-wrap">
    <table>
      <thead><tr><th>ID</th><th>Type</th><th>Value</th><th>Status</th><th>Created</th></tr></thead>
      <tbody>
      {% for r in requests %}
      <tr>
        <td class="mono muted">{{ r.id }}</td>
        <td>{{ r.type }}</td>
        <td><strong>{{ r.amount or r.plan or '—' }}</strong></td>
        <td>
          {% if r.status == 'pending' %}<span class="badge amber">pending</span>
          {% elif r.status == 'approved' %}<span class="badge green">approved</span>
          {% else %}<span class="badge red">rejected</span>{% endif %}
        </td>
        <td class="mono muted">{{ r.created_at[:19] }}</td>
      </tr>
      {% else %}
      <tr><td colspan="5" class="empty">No requests yet.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<div class="card">
  <h2>Transaction history <span class="sub">{{ txs|length }}</span></h2>
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
      <tr><td colspan="5" class="empty">No transactions yet.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
"""


# ===========================================================================
# SIDEBAR (shared)
# ===========================================================================
SIDEBAR = r"""
<aside class="sidebar">
  <div class="brand">
    <div class="brand-mark">S</div>
    <div class="brand-text">
      <span>SELF BOT</span>
      <small>My Panel</small>
    </div>
  </div>

  <div class="nav-group">Overview</div>
  <a href="{{ url_for('user.dashboard') }}" class="nav-link {{ 'active' if active=='dashboard' else '' }}">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>
    Dashboard
  </a>
  <a href="{{ url_for('user.account_page') }}" class="nav-link {{ 'active' if active=='account' else '' }}">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21v-2a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v2"/></svg>
    Account
  </a>

  <div class="nav-group">Settings</div>
  <a href="{{ url_for('user.clock_page') }}" class="nav-link {{ 'active' if active=='clock' else '' }}">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>
    Clock
  </a>
  <a href="{{ url_for('user.appearance_page') }}" class="nav-link {{ 'active' if active=='appearance' else '' }}">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
    Appearance
  </a>
  <a href="{{ url_for('user.textformat_page') }}" class="nav-link {{ 'active' if active=='textformat' else '' }}">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7V4h16v3M9 20h6M12 4v16"/></svg>
    Text style
  </a>
  <a href="{{ url_for('user.jobs_page') }}" class="nav-link {{ 'active' if active=='jobs' else '' }}">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>
    Jobs
  </a>

  <div class="sidebar-footer">
    <button class="theme-toggle" onclick="toggleTheme()">
      <span>Theme</span>
      <span class="toggle-switch"></span>
    </button>
    <a href="{{ url_for('user.logout') }}" class="nav-link">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
      Sign out
    </a>
  </div>
</aside>
"""


def _render_layout(title: str, body: str, active: str = "dashboard"):
    full = (
        '<div class="layout">'
        + SIDEBAR
        + '<main class="main">'
        + '{% with msgs = get_flashed_messages(with_categories=true) %}'
        + '{% for cat, msg in msgs %}<div class="flash {{ \'err\' if cat==\'error\' else \'\' }}">{{ msg }}</div>{% endfor %}'
        + '{% endwith %}'
        + body
        + '</main></div>'
    )
    # flash messages need to be rendered, so we call render_template_string once
    return render_template_string(BASE_TEMPLATE, title=title, body=render_template_string(
        full, active=active
    ))


# ===========================================================================
# Auth helpers
# ===========================================================================
def _get_uid_or_redirect():
    uid = _current_uid()
    if not uid:
        return None
    return int(uid)


# ===========================================================================
# Routes
# ===========================================================================
@bp.route("/", methods=["GET"])
def index():
    if _current_uid():
        return redirect(url_for("user.dashboard"))
    return redirect(url_for("user.login_page"))


@bp.route("/login", methods=["GET"])
def login_page():
    if _current_uid():
        return redirect(url_for("user.dashboard"))
    body = render_template_string(LOGIN_TEMPLATE, csrf=_csrf_token())
    return render_template_string(BASE_TEMPLATE, title="Sign in", body=body)


@bp.route("/login", methods=["POST"])
def login_post():
    _csrf_check()
    code = request.form.get("otp", "").strip()
    uid = OTP.verify_code(code)
    if uid is None:
        flash("Invalid or expired code. Send /web again to get a new one.", "error")
        return redirect(url_for("user.login_page"))
    session["user_id"] = uid
    session.permanent = True
    try:
        ensure_user(uid)
    except Exception:
        pass
    log_flask.info(f"user panel login uid={uid}")
    return redirect(url_for("user.dashboard"))


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("user.login_page"))


@bp.route("/dashboard")
@_login_required
def dashboard():
    uid = _get_uid_or_redirect()
    u = get_user(uid) or {}
    settings = get_user_settings(uid)
    tf = TF.get_settings(uid)
    tf_active = [k.capitalize() for k, v in tf.items() if k in TF.FORMAT_PAIRS and v]
    body = render_template_string(
        DASHBOARD_TEMPLATE,
        user_id=uid,
        first_name=u.get("first_name") or "",
        diamonds=get_balance(uid),
        clock_on=bool(settings.get("clock_on", False)),
        interval=int(settings.get("interval", 5)),
        text_style=" · ".join(tf_active) if tf_active else "None",
        txs=tx_for_user(uid, 15),
    )
    return _render_layout("Dashboard", body, "dashboard")


# ---------- Clock ----------
@bp.route("/clock")
@_login_required
def clock_page():
    from .clock import last_written
    uid = _get_uid_or_redirect()
    settings = get_user_settings(uid)
    body = render_template_string(
        CLOCK_TEMPLATE,
        clock_on=bool(settings.get("clock_on", False)),
        interval=int(settings.get("interval", 5)),
        timezone=settings.get("timezone", "Asia/Tehran"),
        last_written=last_written(uid),
        csrf=_csrf_token(),
    )
    return _render_layout("Clock", body, "clock")


@bp.route("/clock/toggle", methods=["POST"])
@_login_required
def clock_toggle():
    _csrf_check()
    uid = _get_uid_or_redirect()
    action = request.form.get("action", "")
    try:
        from app import _get_runtime
        rt = _get_runtime()
        client = rt.user_clients.get(uid) if rt else None
    except Exception:
        client = None

    if action == "on":
        update_user_settings(uid, clock_on=True)
        if client:
            try:
                from .clock import start_clock
                start_clock(client, uid)
            except Exception as e:
                flash(f"Clock started but encountered: {e}", "error")
        flash("Clock turned ON.")
    elif action == "off":
        if client:
            try:
                from .clock import stop_clock_async
                import asyncio
                from .bootstrap import _async_loop
                if _async_loop and _async_loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        stop_clock_async(client, uid), _async_loop
                    ).result(timeout=10)
                else:
                    from .clock import stop_clock
                    stop_clock(uid)
            except Exception:
                from .clock import stop_clock
                stop_clock(uid)
        else:
            from .clock import stop_clock
            stop_clock(uid)
        flash("Clock turned OFF.")
    return redirect(url_for("user.clock_page"))


@bp.route("/clock/settings", methods=["POST"])
@_login_required
def clock_settings():
    _csrf_check()
    uid = _get_uid_or_redirect()
    try:
        interval = int(request.form.get("interval", "5"))
        interval = max(1, min(60, interval))
    except ValueError:
        interval = 5
    tz = request.form.get("timezone", "Asia/Tehran").strip() or "Asia/Tehran"
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
    except Exception:
        flash("Invalid timezone.", "error")
        return redirect(url_for("user.clock_page"))
    update_user_settings(uid, interval=interval, timezone=tz)
    flash("Clock settings saved.")
    return redirect(url_for("user.clock_page"))


# ---------- Appearance ----------
@bp.route("/appearance")
@_login_required
def appearance_page():
    from .fonts import apply_clock_font, apply_name_font
    uid = _get_uid_or_redirect()
    s = get_user_settings(uid)
    preview_name = apply_name_font(
        s.get("base_name", "User"), s.get("name_font", "normal"),
        custom=s.get("custom_name_font", "") or "",
    )
    preview_clock = apply_clock_font(
        "12:34", s.get("clock_font", "double"),
        custom=s.get("custom_clock_font", "") or "",
    )
    body = render_template_string(
        APPEARANCE_TEMPLATE,
        base_name=s.get("base_name", "User"),
        name_font=s.get("name_font", "normal"),
        clock_font=s.get("clock_font", "double"),
        preview_name=preview_name,
        preview_clock=preview_clock,
        name_fonts=list(NAME_FONTS.keys()),
        clock_fonts=list(DIGIT_FONTS.keys()),
        csrf=_csrf_token(),
    )
    return _render_layout("Appearance", body, "appearance")


@bp.route("/appearance/save", methods=["POST"])
@_login_required
def appearance_save():
    _csrf_check()
    uid = _get_uid_or_redirect()
    base_name = (request.form.get("base_name", "User") or "User").strip()[:32]
    name_font = request.form.get("name_font", "normal")
    clock_font = request.form.get("clock_font", "double")
    if name_font not in NAME_FONTS:
        name_font = "normal"
    if clock_font not in DIGIT_FONTS:
        clock_font = "double"
    update_user_settings(
        uid,
        base_name=base_name or "User",
        name_font=name_font,
        clock_font=clock_font,
    )
    flash("Appearance saved.")
    return redirect(url_for("user.appearance_page"))


# ---------- Text format ----------
@bp.route("/textformat")
@_login_required
def textformat_page():
    uid = _get_uid_or_redirect()
    s = TF.get_settings(uid)
    formats = [(k, TF.FORMAT_LABELS[k]) for k in TF.FORMAT_PAIRS.keys()]
    body = render_template_string(
        TEXTFORMAT_TEMPLATE,
        settings=s,
        formats=formats,
        chats_filter=s.get("chats_filter", ""),
        csrf=_csrf_token(),
    )
    return _render_layout("Text style", body, "textformat")


@bp.route("/textformat/save", methods=["POST"])
@_login_required
def textformat_save():
    _csrf_check()
    uid = _get_uid_or_redirect()
    fields = {}
    for key in TF.FORMAT_PAIRS.keys():
        fields[key] = 1 if request.form.get(key) else 0
    fields["chats_filter"] = (request.form.get("chats_filter", "") or "").strip()[:500]
    db.upsert_text_format(uid, **fields)
    flash("Text styles saved.")
    return redirect(url_for("user.textformat_page"))


# ---------- Jobs ----------
@bp.route("/jobs")
@_login_required
def jobs_page():
    uid = _get_uid_or_redirect()
    jobs = J.active_jobs(owner_id=uid)
    body = render_template_string(JOBS_TEMPLATE, jobs=jobs, csrf=_csrf_token())
    return _render_layout("Jobs", body, "jobs")


@bp.route("/jobs/stop/<job_id>", methods=["POST"])
@_login_required
def job_stop(job_id: str):
    _csrf_check()
    uid = _get_uid_or_redirect()
    j = db.get_job(job_id)
    if not j or int(j.get("owner_id", 0)) != uid:
        flash("Job not found.", "error")
        return redirect(url_for("user.jobs_page"))
    J.stop_job(job_id)
    flash(f"Job {job_id} stopped.")
    return redirect(url_for("user.jobs_page"))


# ---------- Account ----------
@bp.route("/account")
@_login_required
def account_page():
    uid = _get_uid_or_redirect()
    u = get_user(uid) or {}
    body = render_template_string(
        ACCOUNT_TEMPLATE,
        diamonds=get_balance(uid),
        referral_code=u.get("referral_code", "—"),
        requests=req_for_user(uid)[:50],
        txs=tx_for_user(uid, 50),
        csrf=_csrf_token(),
    )
    return _render_layout("Account", body, "account")


@bp.route("/request/diamonds", methods=["POST"])
@_login_required
def request_diamonds():
    _csrf_check()
    uid = _get_uid_or_redirect()
    try:
        amount = int(request.form.get("amount", "0"))
    except ValueError:
        flash("Invalid amount.", "error")
        return redirect(url_for("user.account_page"))
    if not (1 <= amount <= 10000):
        flash("Amount must be between 1 and 10000.", "error")
        return redirect(url_for("user.account_page"))
    create_diamond_request(uid, amount)
    flash(f"💎 Diamond request sent to admin ({amount}).")
    return redirect(url_for("user.account_page"))


@bp.route("/request/subscription", methods=["POST"])
@_login_required
def request_subscription():
    _csrf_check()
    uid = _get_uid_or_redirect()
    plan = request.form.get("plan", "basic")
    if plan not in ("basic", "pro", "vip"):
        plan = "basic"
    create_subscription_request(uid, plan)
    flash(f"⭐ Subscription request ({plan}) sent to admin.")
    return redirect(url_for("user.account_page"))


def build_user_app():
    return bp