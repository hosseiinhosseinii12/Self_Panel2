"""HTML templates for the Casino automator panel (admin + user + export).

No meow/meowie-profile cards. Only Casino UI + Export page.
"""


# ===========================================================================
# ADMIN
# ===========================================================================
CASINO_ADMIN_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Automation</div>
    <div class="page-title">Casino</div>
    <div class="page-sub">Self-bot Wheel of Fortune automation</div>
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
# USER
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