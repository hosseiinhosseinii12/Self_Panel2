"""HTML templates for the invite migrator panel (admin)."""


# ===========================================================================
# ADMIN — main page (list of jobs + create form)
# ===========================================================================
INVITE_ADMIN_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Automation</div>
    <div class="page-title">Invite Migrator</div>
    <div class="page-sub">Copy members from a source chat to a target chat</div>
  </div>
</div>

{% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat, msg in msgs %}
    <div class="flash {{ 'err' if cat=='error' else '' }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<div class="card">
  <h2>Create a new job</h2>
  <form method="post" action="{{ url_for('invite_new') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">

    <div class="field-row">
      <div>
        <label>Source chat (ID, @username, or invite link)</label>
        <input type="text" name="source_chat"
               placeholder="e.g. -1001234567890 or @groupname"
               required>
      </div>
      <div>
        <label>Target chat (you must be admin)</label>
        <input type="text" name="target_chat"
               placeholder="e.g. -1009876543210 or @target"
               required>
      </div>
    </div>

    <div class="field-row mt-3">
      <div>
        <label>Batch size (max 10 per link)</label>
        <input type="number" name="batch_size" value="10" min="1" max="10">
      </div>
      <div>
        <label>Delay between adds (seconds)</label>
        <input type="number" name="delay_seconds" value="30" min="5" max="600">
      </div>
    </div>

    <div class="muted mt-3" style="font-size:12px;line-height:1.6">
      <strong>How it works:</strong>
      Each invite link can be used for at most <strong>10 people</strong>.
      The bot will try to add each member directly. If direct add fails
      (privacy settings), it will send them an invite link via PV.
      After 10 uses, the link is revoked and a new one is created.
    </div>

    <div class="mt-4">
      <button class="btn btn-primary">Create job</button>
    </div>
  </form>
</div>

<div class="card">
  <h2>Jobs <span class="sub">{{ jobs|length }}</span></h2>

  {% if jobs %}
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Source</th>
          <th>Target</th>
          <th>Status</th>
          <th class="text-right">Total</th>
          <th class="text-right">Direct</th>
          <th class="text-right">Link</th>
          <th class="text-right">Failed</th>
          <th>Created (Tehran)</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
      {% for j in jobs %}
      <tr>
        <td class="mono"><a href="{{ url_for('invite_job_detail', job_id=j.id) }}">{{ j.id }}</a></td>
        <td class="mono muted">{{ j.source_chat }}</td>
        <td class="mono muted">{{ j.target_chat }}</td>
        <td>
          {% if j.status == 'running' %}<span class="badge amber">running</span>
          {% elif j.status == 'done' %}<span class="badge green">done</span>
          {% elif j.status == 'failed' %}<span class="badge red">failed</span>
          {% else %}<span class="badge">{{ j.status }}</span>{% endif %}
        </td>
        <td class="mono text-right">{{ j.total_members }}</td>
        <td class="mono text-right" style="color:var(--green)">{{ j.added_directly }}</td>
        <td class="mono text-right" style="color:var(--blue)">{{ j.sent_link }}</td>
        <td class="mono text-right" style="color:var(--red)">{{ j.failed }}</td>
        <td class="mono muted">{{ j.created_at_tehran }}</td>
        <td class="text-right">
          <a class="btn btn-sm" href="{{ url_for('invite_job_detail', job_id=j.id) }}">Open →</a>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="empty">
    <div class="empty-icon">📥</div>
    No jobs yet. Create your first one above.
  </div>
  {% endif %}
</div>
"""


# ===========================================================================
# ADMIN — job detail page
# ===========================================================================
INVITE_JOB_TEMPLATE = r"""
<div class="page-head">
  <div>
    <div class="crumb">Invite / Job</div>
    <div class="page-title mono">{{ job.id }}</div>
    <div class="page-sub">
      {{ job.source_chat }} → {{ job.target_chat }}
    </div>
  </div>
  <div class="page-actions">
    <a class="btn" href="{{ url_for('invite_page') }}">← Back</a>

    {% if job.status in ('pending', 'failed') %}
    <form method="post" action="{{ url_for('invite_job_run', job_id=job.id) }}"
          style="display:inline"
          onsubmit="return confirm('Start this job?');">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="btn btn-success">▶ Run job</button>
    </form>
    {% endif %}

    {% if job.status != 'running' %}
    <form method="post" action="{{ url_for('invite_job_delete', job_id=job.id) }}"
          style="display:inline"
          onsubmit="return confirm('Delete this job and all its items?');">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="btn btn-danger">Delete</button>
    </form>
    {% endif %}
  </div>
</div>

{% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat, msg in msgs %}
    <div class="flash {{ 'err' if cat=='error' else '' }}">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<div class="stats">
  <div class="stat">
    <div class="k">Status</div>
    <div class="v" style="font-size:20px">
      {% if job.status == 'running' %}<span style="color:var(--amber)">running</span>
      {% elif job.status == 'done' %}<span style="color:var(--green)">done</span>
      {% elif job.status == 'failed' %}<span style="color:var(--red)">failed</span>
      {% else %}{{ job.status }}{% endif %}
    </div>
  </div>
  <div class="stat">
    <div class="k">Total candidates</div>
    <div class="v">{{ job.total_members }}</div>
  </div>
  <div class="stat">
    <div class="k">Added directly</div>
    <div class="v" style="color:var(--green)">{{ job.added_directly }}</div>
  </div>
  <div class="stat">
    <div class="k">Link sent</div>
    <div class="v" style="color:var(--blue)">{{ job.sent_link }}</div>
  </div>
  <div class="stat">
    <div class="k">Failed</div>
    <div class="v" style="color:var(--red)">{{ job.failed }}</div>
  </div>
  <div class="stat">
    <div class="k">Skipped</div>
    <div class="v" style="color:var(--text-2)">{{ job.skipped }}</div>
  </div>
</div>

<div class="grid-2">
  <div class="card">
    <h2>Job details</h2>
    <div class="table-wrap" style="border:none;box-shadow:none">
      <table>
        <tbody>
          <tr><td class="muted">Source chat</td>
              <td class="mono text-right">{{ job.source_chat }}</td></tr>
          <tr><td class="muted">Target chat</td>
              <td class="mono text-right">{{ job.target_chat }}</td></tr>
          <tr><td class="muted">Batch size</td>
              <td class="mono text-right">{{ job.batch_size }} / link</td></tr>
          <tr><td class="muted">Delay</td>
              <td class="mono text-right">{{ job.delay_seconds }} s</td></tr>
          <tr><td class="muted">Created</td>
              <td class="mono text-right">{{ job.created_at_tehran }}</td></tr>
          <tr><td class="muted">Started</td>
              <td class="mono text-right">{{ job.started_at_tehran }}</td></tr>
          <tr><td class="muted">Finished</td>
              <td class="mono text-right">{{ job.finished_at_tehran }}</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>Progress</h2>
    <div class="table-wrap" style="border:none;box-shadow:none">
      <table>
        <tbody>
          <tr><td class="muted">Added directly</td>
              <td class="mono text-right" style="color:var(--green)">
                {{ counts.direct }}
              </td></tr>
          <tr><td class="muted">Link sent</td>
              <td class="mono text-right" style="color:var(--blue)">
                {{ counts.link }}
              </td></tr>
          <tr><td class="muted">Failed</td>
              <td class="mono text-right" style="color:var(--red)">
                {{ counts.failed }}
              </td></tr>
          <tr><td class="muted">Total processed</td>
              <td class="mono text-right">{{ counts.total }}</td></tr>
        </tbody>
      </table>
    </div>

    {% if job.last_error %}
    <div class="muted mt-3" style="font-size:12px;color:var(--red)">
      <strong>Last error:</strong> {{ job.last_error }}
    </div>
    {% endif %}

    {% if job.status == 'running' %}
    <div class="muted mt-3" style="font-size:12px">
      Job is running in the background. Refresh this page to see updates.
    </div>
    {% endif %}
  </div>
</div>

<div class="card">
  <h2>
    Processed members <span class="sub">{{ items|length }}</span>
  </h2>

  <div style="margin-bottom:12px;display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap">
    <form method="post" action="{{ url_for('invite_job_clear_items', job_id=job.id) }}"
          onsubmit="return confirm('Clear all item logs for this job?');"
          style="display:inline">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="btn btn-sm btn-danger">Clear item list</button>
    </form>
  </div>

  {% if items %}
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>When (Tehran)</th>
          <th>User</th>
          <th>Name</th>
          <th>Method</th>
          <th>Status</th>
          <th>Error</th>
        </tr>
      </thead>
      <tbody>
      {% for it in items %}
      <tr>
        <td class="mono muted">{{ it.created_at_tehran }}</td>
        <td class="mono">
          <a href="tg://user?id={{ it.target_user_id }}" target="_blank">
            #{{ it.target_user_id }}
          </a>
          {% if it.username %}<span class="muted">@{{ it.username }}</span>{% endif %}
        </td>
        <td class="muted">{{ it.first_name or '—' }}</td>
        <td class="mono">
          {% if it.method == 'direct' %}<span class="badge green">direct</span>
          {% elif it.method == 'link' %}<span class="badge blue">link</span>
          {% else %}<span class="badge">{{ it.method }}</span>{% endif %}
        </td>
        <td>
          {% if it.status == 'success' %}<span class="badge green">success</span>
          {% elif it.status == 'failed' %}<span class="badge red">failed</span>
          {% elif it.status == 'skipped' %}<span class="badge amber">skipped</span>
          {% else %}<span class="badge">{{ it.status }}</span>{% endif %}
        </td>
        <td class="muted" style="font-size:11.5px;max-width:240px;word-break:break-word">
          {{ it.error or '—' }}
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="empty">
    <div class="empty-icon">📥</div>
    No processed members yet.
    {% if job.status in ('pending', 'failed') %}
      Click "▶ Run job" above to start.
    {% endif %}
  </div>
  {% endif %}
</div>
"""