"""HTML templates for the Casino automator panel.

Contains:
- CASINO_ADMIN_TEMPLATE (admin main page, with links to access management)
- CASINO_USER_TEMPLATE (unlocked user panel)
- CASINO_LOCKED_TEMPLATE (locked user view with request button)
- CASINO_ACCESS_TEMPLATE (admin access management page)
- CASINO_EXPORT_TEMPLATE (export filter + format chooser)
"""


# ===========================================================================
# ADMIN — main casino page
# ===========================================================================
CASINO_ADMIN_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Automation</div>
    <div class="page-title">Casino</div>
    <div class="page-sub">Self-bot Wheel of Fortune automation</div>
  </div>
  <div class="page-actions">
    <a class="btn btn-sm" href="{{ url_for('casino_access_page') }}">
      🔐 Access
      {% if pending_access_count > 0 %}
      <span class="badge red" style="margin-left:4px">{{ pending_access_count }}</span>
      {% endif %}
    </a>
    {% if cfg.enabled %}
      <span class="badge green"><span class="badge-dot"></span>Running</span>
    {% else %}
      <span class="badge"><span class="badge-dot"></span>Stopped</span>
    {% endif %}
  </div>
</div>

{% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat, msg in msgs %}
    <div class="flash {{ 'err' if cat=='error' else '' }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<!-- ======================= Access summary ======================= -->
<div class="card" style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;justify-content:space-between">
  <div>
    <div class="k" style="font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--text-3)">
      Casino Access
    </div>
    <div style="margin-top:6px;font-size:14px">
      <strong>{{ unlocked_count }}</strong> user(s) unlocked
      {% if pending_access_count > 0 %}
        &middot;
        <strong style="color:var(--amber)">{{ pending_access_count }}</strong> pending request(s)
      {% endif %}
    </div>
  </div>
  <a class="btn btn-primary" href="{{ url_for('casino_access_page') }}">
    Manage access
  </a>
</div>

<!-- ======================= Stats ======================= -->
<div class="stats">
  <div class="stat">
    <div class="k">Plays</div>
    <div class="v">{{ stats.total }}</div>
  </div>
  <div class="stat">
    <div class="k">Wins</div>
    <div class="v" style="color:var(--green)">{{ stats.wins }}</div>
  </div>
  <div class="stat">
    <div class="k">Losses</div>
    <div class="v" style="color:var(--red)">{{ stats.losses }}</div>
  </div>
  <div class="stat">
    <div class="k">Pushes (1x)</div>
    <div class="v" style="color:var(--amber)">{{ stats.pushes }}</div>
  </div>
  <div class="stat">
    <div class="k">Net profit</div>
    <div class="v"
         style="color:{% if cfg.total_profit >= 0 %}var(--green){% else %}var(--red){% endif %}">
      {{ '{:+,}'.format(cfg.total_profit) }}
    </div>
  </div>
  <div class="stat">
    <div class="k">Peak profit</div>
    <div class="v">{{ '{:+,}'.format(cfg.peak_balance) }}</div>
  </div>
</div>

<!-- ======================= Config + Controls ======================= -->
<div class="grid-2">
  <div class="card">
    <h2>Configuration</h2>
    <form method="post" action="{{ url_for('casino_save') }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">

      <label>Target chat ID</label>
      <input type="text" name="chat_id" value="{{ cfg.chat_id or '' }}"
             placeholder="e.g. -1001234567890">

      <div class="field-row mt-3">
        <div>
          <label>Initial bet (1000 – 5,000,000)</label>
          <input type="number" name="initial_bet"
                 value="{{ cfg.initial_bet }}" min="1000" max="5000000">
        </div>
        <div>
          <label>Max bet (cap 5,000,000)</label>
          <input type="number" name="max_bet"
                 value="{{ cfg.max_bet }}" min="1000" max="5000000">
        </div>
      </div>

      <div class="field-row mt-3">
        <div>
          <label>Bet interval (seconds, &ge; 60)</label>
          <input type="number" name="bet_interval"
                 value="{{ cfg.bet_interval }}" min="60">
        </div>
        <div>
          <label>Winning multipliers (comma-separated)</label>
          <input type="text" name="multipliers"
                 value="{{ cfg.multipliers }}"
                 placeholder="1.5,2,2.5,3">
        </div>
      </div>

      <label>Strategy mode</label>
      <select name="strategy_mode">
        <option value="martingale"
                {% if cfg.strategy_mode == 'martingale' %}selected{% endif %}>
          Martingale v2 (x3 on 0x / 0.5x, repeat on 1x, reset on win)
        </option>
        <option value="flat"
                {% if cfg.strategy_mode == 'flat' %}selected{% endif %}>
          Flat (always initial bet)
        </option>
      </select>

      <div class="muted mt-3" style="font-size:12px;line-height:1.6">
        <strong>Strategy:</strong>
        0x / 0.5x &rarr; x3 &middot; 1x &rarr; repeat &middot; win (&ge;1.5x) &rarr; reset to initial.<br>
        Bets above 5,000,000 are rounded DOWN to 5,000,000.
      </div>

      <div class="mt-4">
        <button class="btn btn-primary">Save configuration</button>
      </div>
    </form>
  </div>

  <div class="card">
    <h2>Controls</h2>

    <div class="field-row">
      <form method="post" action="{{ url_for('casino_toggle') }}" style="flex:1">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        {% if cfg.enabled %}
          <button class="btn btn-danger" style="width:100%">Stop</button>
        {% else %}
          <button class="btn btn-success" style="width:100%">Start</button>
        {% endif %}
      </form>

      <form method="post" action="{{ url_for('casino_trigger') }}" style="flex:1">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn btn-primary" style="width:100%">Run now</button>
      </form>
    </div>

    <div class="field-row mt-3">
      <form method="post" action="{{ url_for('casino_reset') }}" style="flex:1"
            onsubmit="return confirm('Reset all casino progress?');">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn" style="width:100%">Reset progress</button>
      </form>

      <form method="post" action="{{ url_for('casino_clear_logs') }}" style="flex:1"
            onsubmit="return confirm('Clear all casino logs?');">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn" style="width:100%">Clear logs</button>
      </form>
    </div>

    <div class="mt-4">
      <div class="k" style="font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--text-3);margin-bottom:12px">
        Current state
      </div>
      <div class="table-wrap" style="border:none;box-shadow:none">
        <table>
          <tbody>
            <tr><td class="muted">Next bet (current)</td>
                <td class="mono text-right">{{ '{:,}'.format(cfg.current_bet) }}</td></tr>
            <tr><td class="muted">Peak bet</td>
                <td class="mono text-right">{{ '{:,}'.format(cfg.peak_bet) }}</td></tr>
            <tr><td class="muted">Streak (best)</td>
                <td class="mono text-right">
                  {{ cfg.current_streak }} ({{ cfg.best_streak }})
                </td></tr>
            <tr><td class="muted">Invested</td>
                <td class="mono text-right">{{ '{:,}'.format(cfg.total_invested) }}</td></tr>
            <tr><td class="muted">Total won</td>
                <td class="mono text-right" style="color:var(--green)">
                  {{ '{:+,}'.format(cfg.total_won) }}
                </td></tr>
            <tr><td class="muted">Total lost</td>
                <td class="mono text-right" style="color:var(--red)">
                  {{ '{:+,}'.format(cfg.total_lost) }}
                </td></tr>
            <tr><td class="muted">Last run (Tehran)</td>
                <td class="mono text-right">{{ cfg.last_run_tehran }}</td></tr>
            <tr><td class="muted">Next run (Tehran)</td>
                <td class="mono text-right">{{ cfg.next_run_tehran }}</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- ======================= Recent runs ======================= -->
<div class="card">
  <h2>
    Recent runs <span class="sub">{{ runs|length }}</span>
  </h2>
  <div style="margin-bottom:12px;display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap">
    <a class="btn btn-sm" href="{{ url_for('casino_export_page') }}"
       style="background:var(--accent);color:var(--accent-fg);border-color:var(--accent)">
      📥 Export
    </a>
    <form method="post" action="{{ url_for('casino_clear_runs') }}"
          onsubmit="return confirm('Delete all recent runs? This cannot be undone.');"
          style="display:inline">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="btn btn-sm btn-danger">Clear all runs</button>
    </form>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>When (Tehran)</th><th>Bet</th><th>Result</th><th>Mult</th>
          <th>Score</th><th class="text-right">Profit</th>
        </tr>
      </thead>
      <tbody>
      {% for r in runs %}
      <tr>
        <td class="mono muted">{{ r.started_at_tehran }}</td>
        <td class="mono">{{ '{:,}'.format(r.bet_amount) }}</td>
        <td>
          {% if r.result == 'win' %}<span class="badge green">win</span>
          {% elif r.result == 'lose' %}<span class="badge red">lose</span>
          {% elif r.result == 'push' %}<span class="badge amber">push</span>
          {% elif r.status == 'timeout' %}<span class="badge amber">timeout</span>
          {% elif r.status == 'error' %}<span class="badge red">error</span>
          {% else %}<span class="badge">{{ r.status }}</span>{% endif %}
        </td>
        <td class="mono">{{ r.multiplier }}x</td>
        <td class="mono">{{ r.score }}</td>
        <td class="mono text-right"
            style="color:{% if r.profit >= 0 %}var(--green){% else %}var(--red){% endif %}">
          {{ '{:+,}'.format(r.profit) }}
        </td>
      </tr>
      {% else %}
      <tr><td colspan="6" class="empty"><div class="empty-icon">🎰</div>No runs yet</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<!-- ======================= Logs ======================= -->
<div class="card">
  <h2>Logs <span class="sub">{{ logs|length }}</span></h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>When (Tehran)</th><th>Level</th><th>Message</th></tr>
      </thead>
      <tbody>
      {% for l in logs %}
      <tr>
        <td class="mono muted">{{ l.created_at_tehran }}</td>
        <td>
          {% if l.level == 'error' %}<span class="badge red">error</span>
          {% elif l.level == 'warn' %}<span class="badge amber">warn</span>
          {% else %}<span class="badge green">info</span>{% endif %}
        </td>
        <td class="muted">{{ l.message }}{% if l.extra %} <code>{{ l.extra }}</code>{% endif %}</td>
      </tr>
      {% else %}
      <tr><td colspan="3" class="empty">No logs yet</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
"""


# ===========================================================================
# USER — unlocked casino page
# ===========================================================================
CASINO_USER_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Automation</div>
    <div class="page-title">Casino</div>
    <div class="page-sub">Automate Wheel of Fortune in your chat</div>
  </div>
  <div class="page-actions">
    {% if cfg.enabled %}
      <span class="badge green"><span class="badge-dot"></span>Running</span>
    {% else %}
      <span class="badge"><span class="badge-dot"></span>Stopped</span>
    {% endif %}
  </div>
</div>

{% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat, msg in msgs %}
    <div class="flash {{ 'err' if cat=='error' else '' }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<!-- ======================= Stats ======================= -->
<div class="stats">
  <div class="stat">
    <div class="k">Plays</div>
    <div class="v">{{ stats.total }}</div>
  </div>
  <div class="stat">
    <div class="k">Wins</div>
    <div class="v" style="color:var(--green)">{{ stats.wins }}</div>
  </div>
  <div class="stat">
    <div class="k">Losses</div>
    <div class="v" style="color:var(--red)">{{ stats.losses }}</div>
  </div>
  <div class="stat">
    <div class="k">Pushes (1x)</div>
    <div class="v" style="color:var(--amber)">{{ stats.pushes }}</div>
  </div>
  <div class="stat">
    <div class="k">Net profit</div>
    <div class="v"
         style="color:{% if cfg.total_profit >= 0 %}var(--green){% else %}var(--red){% endif %}">
      {{ '{:+,}'.format(cfg.total_profit) }}
    </div>
  </div>
</div>

<!-- ======================= Config + Controls ======================= -->
<div class="grid-2">
  <div class="card">
    <h2>My configuration</h2>
    <form method="post" action="{{ url_for('user.casino_save') }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">

      <label>Target chat ID</label>
      <input type="text" name="chat_id" value="{{ cfg.chat_id or '' }}"
             placeholder="e.g. -1001234567890"
             {% if not can_edit_chat %}disabled{% endif %}>
      {% if not can_edit_chat %}
        <div class="muted mt-2" style="font-size:12px">Only admin can change this.</div>
      {% endif %}

      <div class="field-row mt-3">
        <div>
          <label>Initial bet (1000 – 5,000,000)</label>
          <input type="number" name="initial_bet"
                 value="{{ cfg.initial_bet }}" min="1000" max="5000000"
                 {% if not can_edit_bet %}disabled{% endif %}>
        </div>
        <div>
          <label>Bet interval (seconds, &ge; 60)</label>
          <input type="number" name="bet_interval"
                 value="{{ cfg.bet_interval }}" min="60"
                 {% if not can_edit_interval %}disabled{% endif %}>
        </div>
      </div>

      <div class="field-row mt-3">
        <div>
          <label>Winning multipliers (comma-separated)</label>
          <input type="text" name="multipliers"
                 value="{{ cfg.multipliers }}"
                 placeholder="1.5,2,2.5,3">
        </div>
      </div>

      <div class="muted mt-3" style="font-size:12px;line-height:1.6">
        <strong>Strategy:</strong>
        0x / 0.5x &rarr; x3 &middot; 1x &rarr; repeat &middot; win (&ge;1.5x) &rarr; reset to initial.<br>
        Bets above 5,000,000 are rounded DOWN to 5,000,000.
      </div>

      <div class="mt-4">
        <button class="btn btn-primary">Save</button>
      </div>
    </form>
  </div>

  <div class="card">
    <h2>Controls</h2>

    <form method="post" action="{{ url_for('user.casino_toggle') }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      {% if cfg.enabled %}
        <button class="btn btn-danger" style="width:100%">Stop automation</button>
      {% else %}
        <button class="btn btn-success" style="width:100%">Start automation</button>
      {% endif %}
    </form>

    <form method="post" action="{{ url_for('user.casino_trigger') }}" class="mt-3">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="btn btn-primary" style="width:100%">Run one game now</button>
    </form>

    <div class="mt-4">
      <div class="k" style="font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--text-3);margin-bottom:12px">
        Current state
      </div>
      <div class="table-wrap" style="border:none;box-shadow:none">
        <table>
          <tbody>
            <tr><td class="muted">Next bet</td>
                <td class="mono text-right">{{ '{:,}'.format(cfg.current_bet) }}</td></tr>
            <tr><td class="muted">Peak bet</td>
                <td class="mono text-right">{{ '{:,}'.format(cfg.peak_bet) }}</td></tr>
            <tr><td class="muted">Streak (best)</td>
                <td class="mono text-right">
                  {{ cfg.current_streak }} ({{ cfg.best_streak }})
                </td></tr>
            <tr><td class="muted">Next run (Tehran)</td>
                <td class="mono text-right">{{ cfg.next_run_tehran }}</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- ======================= Recent runs ======================= -->
<div class="card">
  <h2>
    Recent runs <span class="sub">{{ runs|length }}</span>
  </h2>
  <div style="margin-bottom:12px;display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap">
    <a class="btn btn-sm" href="{{ url_for('user.casino_export_page') }}"
       style="background:var(--accent);color:var(--accent-fg);border-color:var(--accent)">
      📥 Export
    </a>
    <form method="post" action="{{ url_for('user.casino_clear_runs') }}"
          onsubmit="return confirm('Delete all recent runs? This cannot be undone.');"
          style="display:inline">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="btn btn-sm btn-danger">Clear all runs</button>
    </form>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>When (Tehran)</th><th>Bet</th><th>Result</th><th>Mult</th>
          <th class="text-right">Profit</th>
        </tr>
      </thead>
      <tbody>
      {% for r in runs %}
      <tr>
        <td class="mono muted">{{ r.started_at_tehran }}</td>
        <td class="mono">{{ '{:,}'.format(r.bet_amount) }}</td>
        <td>
          {% if r.result == 'win' %}<span class="badge green">win</span>
          {% elif r.result == 'lose' %}<span class="badge red">lose</span>
          {% elif r.result == 'push' %}<span class="badge amber">push</span>
          {% elif r.status == 'timeout' %}<span class="badge amber">timeout</span>
          {% elif r.status == 'error' %}<span class="badge red">error</span>
          {% else %}<span class="badge">{{ r.status }}</span>{% endif %}
        </td>
        <td class="mono">{{ r.multiplier }}x</td>
        <td class="mono text-right"
            style="color:{% if r.profit >= 0 %}var(--green){% else %}var(--red){% endif %}">
          {{ '{:+,}'.format(r.profit) }}
        </td>
      </tr>
      {% else %}
      <tr><td colspan="5" class="empty"><div class="empty-icon">🎰</div>No runs yet</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
"""


# ===========================================================================
# USER — locked casino page
# ===========================================================================
CASINO_LOCKED_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Automation</div>
    <div class="page-title">Casino</div>
    <div class="page-sub">Wheel of Fortune automation</div>
  </div>
  <div class="page-actions">
    <span class="badge amber">
      <span class="badge-dot"></span>Locked
    </span>
  </div>
</div>

{% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat, msg in msgs %}
    <div class="flash {{ 'err' if cat=='error' else '' }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<!-- ======================= Locked card ======================= -->
<div class="card" style="
  text-align:center;
  padding:56px 32px 48px;
  background:linear-gradient(180deg,var(--surface) 0%,var(--surface-2) 100%);
  position:relative;
  overflow:hidden;
">
  <div style="
    position:absolute;
    top:-80px; left:50%;
    transform:translateX(-50%);
    width:300px; height:300px;
    background:radial-gradient(circle, rgba(255,255,255,0.04) 0%, transparent 70%);
    pointer-events:none;
  "></div>

  <div style="
    width:88px; height:88px;
    margin:0 auto 24px;
    border-radius:24px;
    background:linear-gradient(135deg, #1a1a20, #0f0f12);
    border:1px solid var(--border-2);
    display:grid;
    place-items:center;
    font-size:40px;
    box-shadow:0 12px 32px rgba(0,0,0,0.35), 0 0 0 1px rgba(255,255,255,0.02) inset;
    position:relative;
    z-index:1;
  ">
    🔒
  </div>

  <h2 style="
    font-size:24px;
    font-weight:800;
    letter-spacing:-0.03em;
    margin-bottom:10px;
    color:var(--text);
    position:relative;
    z-index:1;
  ">
    Casino is locked
  </h2>

  <p class="muted" style="
    font-size:14px;
    max-width:480px;
    margin:0 auto 28px;
    line-height:1.65;
    position:relative;
    z-index:1;
  ">
    The casino automator is not enabled for your account yet.
    You can request access from the admin and it will be reviewed manually.
  </p>

  {% if pending %}
    <div style="
      display:inline-flex;
      align-items:center;
      gap:10px;
      padding:12px 20px;
      border-radius:12px;
      background:var(--amber-bg);
      border:1px solid var(--amber-border);
      color:var(--amber);
      font-size:13.5px;
      font-weight:600;
      position:relative;
      z-index:1;
    ">
      <span style="
        width:8px; height:8px;
        border-radius:50%;
        background:currentColor;
        animation:pulse 2s ease-in-out infinite;
      "></span>
      Request pending &middot; waiting for admin approval
    </div>

    <div class="muted" style="
      margin-top:16px;
      font-size:11.5px;
      position:relative;
      z-index:1;
    ">
      Request ID: <code>{{ pending.id }}</code>
      &middot;
      Sent {{ pending.created_at[:19] }}
    </div>
  {% else %}
    <form method="post" action="{{ url_for('user.casino_request_access') }}"
          style="position:relative;z-index:1;max-width:480px;margin:0 auto;">
      <input type="hidden" name="csrf" value="{{ csrf }}">

      <label style="text-align:left;margin-bottom:8px">
        Message to admin (optional)
      </label>
      <textarea name="note" rows="3"
                placeholder="Why do you want access? Any context that helps the admin decide."
                style="
                  text-align:left;
                  font-family:inherit;
                  resize:vertical;
                  min-height:80px;
                "></textarea>

      <div style="margin-top:20px;display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
        <button class="btn btn-primary btn-lg">
          🔓 Request access
        </button>
        <a class="btn btn-lg" href="{{ url_for('user.dashboard') }}">
          ← Back to dashboard
        </a>
      </div>
    </form>
  {% endif %}

  <div class="muted" style="
    margin-top:32px;
    padding-top:20px;
    border-top:1px solid var(--border);
    font-size:11.5px;
    line-height:1.7;
    max-width:520px;
    margin-left:auto;
    margin-right:auto;
    position:relative;
    z-index:1;
  ">
    Access is granted manually by the admin. This is a safety measure to
    prevent accidental use of automated betting in your account.
    {% if is_admin %}
    <br><br>
    <span style="color:var(--text-2)">
      You are the owner but your access has been disabled.
      Please re-enable it from the admin panel.
    </span>
    {% endif %}
  </div>
</div>
"""


# ===========================================================================
# ADMIN — access management page
# ===========================================================================
CASINO_ACCESS_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Casino / Access</div>
    <div class="page-title">Casino Access</div>
    <div class="page-sub">Manage who can use the casino automator</div>
  </div>
  <div class="page-actions">
    <a class="btn" href="{{ url_for('casino_page') }}">← Back to Casino</a>
  </div>
</div>

{% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat, msg in msgs %}
    <div class="flash {{ 'err' if cat=='error' else '' }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<!-- ======================= Summary ======================= -->
<div class="stats">
  <div class="stat">
    <div class="k">Unlocked users</div>
    <div class="v">{{ unlocked_count }}</div>
  </div>
  <div class="stat">
    <div class="k">Pending requests</div>
    <div class="v" style="color:var(--amber)">{{ pending|length }}</div>
  </div>
  <div class="stat">
    <div class="k">Owner</div>
    <div class="v mono" style="font-size:16px">#{{ owner_id }}</div>
  </div>
</div>

<!-- ======================= Pending requests ======================= -->
<div class="card">
  <h2>
    Pending requests
    <span class="sub">{{ pending|length }}</span>
  </h2>

  {% if pending %}
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>When (Tehran)</th>
          <th>User</th>
          <th>Note</th>
          <th class="text-right">Actions</th>
        </tr>
      </thead>
      <tbody>
      {% for r in pending %}
      <tr>
        <td class="mono muted">{{ r.created_at_tehran }}</td>
        <td class="mono">#{{ r.user_id }}</td>
        <td class="muted">{{ r.note or '—' }}</td>
        <td class="text-right">
          <form method="post"
                action="{{ url_for('casino_access_approve', rid=r.id) }}"
                style="display:inline">
            <input type="hidden" name="csrf" value="{{ csrf }}">
            <button class="btn btn-sm btn-success">✓ Approve</button>
          </form>
          <form method="post"
                action="{{ url_for('casino_access_reject', rid=r.id) }}"
                style="display:inline"
                onsubmit="return confirm('Reject this request?');">
            <input type="hidden" name="csrf" value="{{ csrf }}">
            <button class="btn btn-sm btn-danger">✕ Reject</button>
          </form>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="empty">
    <div class="empty-icon">✓</div>
    No pending requests
  </div>
  {% endif %}
</div>

<!-- ======================= Unlocked users ======================= -->
<div class="card">
  <h2>
    Unlocked users
    <span class="sub">{{ unlocked_users|length }}</span>
  </h2>

  {% if unlocked_users %}
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Name</th>
          <th>Username</th>
          <th class="text-right">Actions</th>
        </tr>
      </thead>
      <tbody>
      {% for u in unlocked_users %}
      <tr>
        <td class="mono">#{{ u.id }}</td>
        <td>{{ u.first_name or '—' }}</td>
        <td class="muted">{{ u.username_display }}</td>
        <td class="text-right">
          <form method="post"
                action="{{ url_for('casino_access_lock', uid=u.id) }}"
                style="display:inline"
                onsubmit="return confirm('Lock casino for user #{{ u.id }}?');">
            <input type="hidden" name="csrf" value="{{ csrf }}">
            <button class="btn btn-sm btn-danger">🔒 Lock</button>
          </form>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="empty">
    <div class="empty-icon">🔒</div>
    No users unlocked yet
  </div>
  {% endif %}
</div>

<!-- ======================= Manual unlock ======================= -->
<div class="card">
  <h2>Manually unlock a user</h2>
  <p class="muted" style="font-size:13px;margin-bottom:16px">
    Enter the numeric Telegram user ID to grant casino access without
    going through the request flow.
  </p>
  <form method="post"
        action="{{ url_for('casino_access_unlock', uid=0) }}"
        onsubmit="
          var uid = this.querySelector('input[name=uid]').value;
          if (!uid) { alert('Enter user ID'); return false; }
          this.action = '{{ url_for('casino_access_unlock', uid=0) }}'.replace('/0', '/' + uid);
          return confirm('Unlock casino for user #' + uid + '?');
        ">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="field-row">
      <div style="flex:0 0 200px">
        <label>User ID</label>
        <input type="number" name="uid" placeholder="e.g. 123456789">
      </div>
      <button class="btn btn-success">🔓 Unlock</button>
    </div>
  </form>
</div>

<!-- ======================= Recent decisions ======================= -->
<div class="card">
  <h2>
    Recent decisions
    <span class="sub">{{ decided|length }}</span>
  </h2>
  {% if decided %}
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>When (Tehran)</th>
          <th>User</th>
          <th>Status</th>
          <th>Note</th>
        </tr>
      </thead>
      <tbody>
      {% for r in decided %}
      <tr>
        <td class="mono muted">{{ r.decided_at_tehran or r.created_at_tehran }}</td>
        <td class="mono">#{{ r.user_id }}</td>
        <td>
          {% if r.status == 'approved' %}<span class="badge green">approved</span>
          {% elif r.status == 'rejected' %}<span class="badge red">rejected</span>
          {% else %}<span class="badge">{{ r.status }}</span>{% endif %}
        </td>
        <td class="muted">{{ r.note or '—' }}</td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="empty">No decisions yet</div>
  {% endif %}
</div>
"""


# ===========================================================================
# EXPORT PAGE
# ===========================================================================
CASINO_EXPORT_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Casino / Export</div>
    <div class="page-title">Export Logs</div>
    <div class="page-sub">Choose what to include and the output format</div>
  </div>
  <div class="page-actions">
    <a class="btn" href="{{ back_url }}">← Back</a>
  </div>
</div>

<form id="export-form" onsubmit="return false;">
  <input type="hidden" name="csrf" value="{{ csrf }}">

  <div class="card">
    <h2>1. Choose filter</h2>
    <div class="export-grid">
      <label class="export-option">
        <input type="radio" name="filter" value="all"
               {% if filter_mode == 'all' %}checked{% endif %}>
        <div class="export-option-body">
          <div class="export-option-title">All runs</div>
          <div class="export-option-count">{{ counts.all }} runs</div>
        </div>
      </label>

      <label class="export-option">
        <input type="radio" name="filter" value="win"
               {% if filter_mode == 'win' %}checked{% endif %}>
        <div class="export-option-body">
          <div class="export-option-title" style="color:var(--green)">Wins only</div>
          <div class="export-option-count">{{ counts.win }} runs</div>
        </div>
      </label>

      <label class="export-option">
        <input type="radio" name="filter" value="lose"
               {% if filter_mode == 'lose' %}checked{% endif %}>
        <div class="export-option-body">
          <div class="export-option-title" style="color:var(--red)">Losses only</div>
          <div class="export-option-count">{{ counts.lose }} runs</div>
        </div>
      </label>

      <label class="export-option">
        <input type="radio" name="filter" value="push"
               {% if filter_mode == 'push' %}checked{% endif %}>
        <div class="export-option-body">
          <div class="export-option-title" style="color:var(--amber)">Pushes only (1x)</div>
          <div class="export-option-count">{{ counts.push }} runs</div>
        </div>
      </label>

      <label class="export-option">
        <input type="radio" name="filter" value="win_lose"
               {% if filter_mode == 'win_lose' %}checked{% endif %}>
        <div class="export-option-body">
          <div class="export-option-title">Wins + Losses</div>
          <div class="export-option-count">{{ counts.win_lose }} runs</div>
        </div>
      </label>

      <label class="export-option">
        <input type="radio" name="filter" value="win_push"
               {% if filter_mode == 'win_push' %}checked{% endif %}>
        <div class="export-option-body">
          <div class="export-option-title">Wins + Pushes</div>
          <div class="export-option-count">{{ counts.win_push }} runs</div>
        </div>
      </label>

      <label class="export-option">
        <input type="radio" name="filter" value="lose_push"
               {% if filter_mode == 'lose_push' %}checked{% endif %}>
        <div class="export-option-body">
          <div class="export-option-title">Losses + Pushes</div>
          <div class="export-option-count">{{ counts.lose_push }} runs</div>
        </div>
      </label>
    </div>
  </div>

  <div class="card">
    <h2>2. Choose format</h2>
    <p class="muted" style="font-size:13px;margin-bottom:16px">
      The file will download directly. PDF is generated on the server
      (no browser print dialog).
    </p>

    <div class="export-actions">
      <button type="button" class="export-action" onclick="doExport('html')">
        <div class="export-action-icon">📄</div>
        <div class="export-action-title">Download HTML</div>
        <div class="export-action-sub">Styled page, opens in any browser</div>
      </button>

      <button type="button" class="export-action" onclick="doExport('pdf')">
        <div class="export-action-icon">📕</div>
        <div class="export-action-title">Download PDF</div>
        <div class="export-action-sub">Server-generated, print-ready</div>
      </button>
    </div>
  </div>
</form>

<script>
function doExport(fmt) {
  var form = document.getElementById('export-form');
  var radios = form.querySelectorAll('input[name="filter"]');
  var filter = 'all';
  for (var i = 0; i < radios.length; i++) {
    if (radios[i].checked) { filter = radios[i].value; break; }
  }
  var path = window.location.pathname;
  var base = path.replace(/\/export\/?$/, '');
  var url = base + '/export/' + fmt + '?filter=' + encodeURIComponent(filter);
  window.location.href = url;
}
</script>

<style>
.export-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.export-option {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 18px;
  border: 1px solid var(--border-2);
  border-radius: var(--r-md);
  background: var(--surface-2);
  cursor: pointer;
  transition: all 130ms cubic-bezier(0.2, 0.8, 0.2, 1);
  margin: 0;
}

.export-option:hover {
  border-color: var(--border-3);
  background: var(--surface);
  transform: translateY(-1px);
}

.export-option input[type="radio"] {
  width: 18px;
  height: 18px;
  accent-color: var(--accent);
  cursor: pointer;
  flex-shrink: 0;
}

.export-option input[type="radio"]:checked ~ .export-option-body .export-option-title {
  font-weight: 700;
}

.export-option-body {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.export-option-title {
  font-weight: 600;
  font-size: 13.5px;
  color: var(--text);
  line-height: 1.3;
}

.export-option-count {
  font-size: 11.5px;
  color: var(--text-3);
  font-family: var(--mono);
}

.export-actions {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 14px;
}

.export-action {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 22px;
  border: 1px solid var(--border-2);
  border-radius: var(--r-lg);
  background: var(--surface-2);
  cursor: pointer;
  transition: all 150ms cubic-bezier(0.2, 0.8, 0.2, 1);
  text-align: left;
  font-family: inherit;
  color: var(--text);
  width: 100%;
}

.export-action:hover {
  border-color: var(--accent);
  background: var(--surface);
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.export-action-icon {
  font-size: 34px;
  margin-bottom: 10px;
  line-height: 1;
}

.export-action-title {
  font-weight: 700;
  font-size: 15px;
  letter-spacing: -0.015em;
}

.export-action-sub {
  font-size: 12px;
  color: var(--text-3);
  margin-top: 4px;
  line-height: 1.5;
}
</style>
"""