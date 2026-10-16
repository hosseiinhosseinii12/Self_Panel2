"""Admin panel routes for the Session Vault."""
from flask import (abort, flash, jsonify, redirect, render_template_string,
                   request, send_file, url_for)

from .config import CONFIG, DB_PATH
from .logging_setup import log_flask
from .audit import record as audit_record
from . import session_manager as sm


SESSIONS_TEMPLATE = """
<div class="page-head">
  <div>
    <div class="crumb">System</div>
    <div class="page-title">Sessions</div>
    <div class="page-sub">
      Encrypted session vault &middot; {{ sessions|length }} total
    </div>
  </div>
</div>

<div class="grid-2">
  <div class="card">
    <h2>Backup sessions</h2>
    <p class="muted mt-2" style="margin-bottom:16px">
      Creates an <strong>encrypted</strong> ZIP of all sessions and uploads
      to every configured destination (Volume, R2, Telegram).
    </p>
    <form method="post" action="{{ url_for('sessions_backup') }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="btn btn-dark">Create session backup</button>
    </form>
  </div>

  <div class="card">
    <h2>Restore sessions</h2>
    <p class="muted mt-2" style="margin-bottom:16px">
      Upload a session backup ZIP (.zip or .zip.enc). Existing sessions
      for the same user will be replaced.
    </p>
    <form method="post" action="{{ url_for('sessions_restore') }}"
          enctype="multipart/form-data">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <input type="file" name="file" accept=".zip,.enc">
      <div class="mt-3">
        <button class="btn btn-danger">Restore sessions</button>
      </div>
    </form>
  </div>
</div>

<div class="card">
  <h2>
    Session status
    <span class="sub">{{ sessions|length }} session(s)</span>
  </h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>ID</th><th>User</th><th>Label</th>
          <th>Telegram</th><th>Active</th>
          <th>Last checked</th><th></th>
        </tr>
      </thead>
      <tbody>
      {% for s in sessions %}
      <tr>
        <td class="mono">{{ s.id }}</td>
        <td class="mono">#{{ s.user_id }}</td>
        <td>{{ s.label or '—' }}</td>
        <td>
          {% if s.telegram_username %}
            <span class="badge">{{ '@' + s.telegram_username }}</span>
          {% else %}
            <span class="muted">—</span>
          {% endif %}
        </td>
        <td>
          {% if s.is_active %}<span class="badge green">yes</span>
          {% else %}<span class="badge red">no</span>{% endif %}
        </td>
        <td class="mono muted">{{ (s.last_checked_at or '—')[:19] }}</td>
        <td style="text-align:right">
          <form method="post" action="{{ url_for('session_test', sid=s.id) }}" style="display:inline">
            <input type="hidden" name="csrf" value="{{ csrf }}">
            <button class="btn btn-sm">Test</button>
          </form>
          <form method="post" action="{{ url_for('session_backup_one', sid=s.id) }}" style="display:inline">
            <input type="hidden" name="csrf" value="{{ csrf }}">
            <button class="btn btn-sm">Backup</button>
          </form>
          <form method="post" action="{{ url_for('session_delete', sid=s.id) }}" style="display:inline"
                onsubmit="return confirm('Delete this session?');">
            <input type="hidden" name="csrf" value="{{ csrf }}">
            <button class="btn btn-sm btn-danger">Delete</button>
          </form>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="7" class="empty">No sessions yet.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<div class="card">
  <h2>Session backups</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>File</th><th>Size</th><th>Modified</th><th>Encrypted</th><th></th></tr>
      </thead>
      <tbody>
      {% for b in backups %}
      <tr>
        <td class="mono">{{ b.name }}</td>
        <td class="mono">{{ b.size_kb }} KB</td>
        <td class="muted mono">{{ b.modified }}</td>
        <td>
          {% if b.encrypted %}<span class="badge green">yes</span>
          {% else %}<span class="badge amber">no</span>{% endif %}
        </td>
        <td style="text-align:right">
          <a class="btn btn-sm" href="{{ url_for('download_session_backup', name=b.name) }}">Download</a>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="5" class="empty">No session backups yet.</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
"""


def register_admin_session_routes(app, login_required, csrf_check,
                                  csrf_token, render_page, get_runtime_safe):
    """Register session vault routes on the admin Flask app."""

    @app.route("/admin/sessions")
    @login_required
    def sessions_page():
        body = render_template_string(
            SESSIONS_TEMPLATE,
            sessions=sm.list_sessions(include_inactive=True),
            backups=sm.list_session_backups(),
            csrf=csrf_token(),
        )
        return render_page("Sessions", body, "sessions")

    @app.route("/admin/sessions/backup", methods=["POST"])
    @login_required
    def sessions_backup():
        csrf_check()
        try:
            from .session_sync import scheduled_session_backup
            path = scheduled_session_backup()
            if path:
                audit_record(0, "sessions_backup", ip=request.remote_addr,
                             meta={"file": path.name})
                flash(f"Session backup created: {path.name}")
            else:
                flash("Backup created but upload failed; check logs.", "error")
        except Exception as e:
            log_flask.error(f"session backup failed: {e}")
            flash(f"Backup failed: {e}", "error")
        return redirect(url_for("sessions_page"))

    @app.route("/admin/sessions/backup/<int:sid>", methods=["POST"])
    @login_required
    def session_backup_one(sid: int):
        csrf_check()
        try:
            rt = get_runtime_safe()
            s = sm.get_session(sid, decrypt_it=False)
            if not s:
                flash("Session not found.", "error")
                return redirect(url_for("sessions_page"))
            uid = int(s["user_id"])
            client = rt.user_clients.get(uid) if rt else None
            if client:
                sm.save_from_client(uid, client)
            audit_record(0, "session_backup_one", target=str(sid),
                         ip=request.remote_addr)
            flash(f"Session {sid} backed up.")
        except Exception as e:
            flash(f"Backup failed: {e}", "error")
        return redirect(url_for("sessions_page"))

    @app.route("/admin/sessions/restore", methods=["POST"])
    @login_required
    def sessions_restore():
        csrf_check()
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Upload a session backup file.", "error")
            return redirect(url_for("sessions_page"))
        encrypted = f.filename.endswith(".enc")
        try:
            data = f.read()
            result = sm.restore_sessions_from_zip(data, encrypted=encrypted)
            if result["errors"]:
                flash(
                    f"Restored {result['restored']} sessions, "
                    f"{len(result['errors'])} errors.",
                    "error",
                )
            else:
                flash(f"Restored {result['restored']} sessions.")
            audit_record(0, "sessions_restore", ip=request.remote_addr,
                         meta=result)
        except Exception as e:
            log_flask.error(f"session restore failed: {e}")
            flash(f"Restore failed: {e}", "error")
        return redirect(url_for("sessions_page"))

    @app.route("/admin/sessions/test/<int:sid>", methods=["POST"])
    @login_required
    def session_test(sid: int):
        csrf_check()
        import asyncio
        try:
            from .bootstrap import _async_loop
            if not _async_loop or not _async_loop.is_running():
                flash("Bot event loop not running.", "error")
                return redirect(url_for("sessions_page"))
            fut = asyncio.run_coroutine_threadsafe(sm.test_session(sid),
                                                    _async_loop)
            result = fut.result(timeout=30)
            if result.get("ok"):
                flash(f"Session valid: @{result.get('username')}")
            else:
                flash(f"Session invalid: {result.get('error')}", "error")
            audit_record(0, "session_test", target=str(sid),
                         ip=request.remote_addr, meta=result)
        except Exception as e:
            flash(f"Test failed: {e}", "error")
        return redirect(url_for("sessions_page"))

    @app.route("/admin/sessions/delete/<int:sid>", methods=["POST"])
    @login_required
    def session_delete(sid: int):
        csrf_check()
        sm.delete_session(sid)
        audit_record(0, "session_delete", target=str(sid),
                     ip=request.remote_addr)
        flash(f"Session {sid} deleted.")
        return redirect(url_for("sessions_page"))

    @app.route("/admin/sessions/download/<name>")
    @login_required
    def download_session_backup(name: str):
        base = sm.SESSIONS_BACKUP_DIR.resolve()
        target = (base / name).resolve()
        if not str(target).startswith(str(base)) or not target.exists():
            abort(404)
        return send_file(target, as_attachment=True, download_name=name)

    @app.route("/admin/sessions/fetch", methods=["POST"])
    @login_required
    def sessions_fetch():
        csrf_check()
        try:
            from .session_sync import fetch_all_sessions
            result = fetch_all_sessions()
            audit_record(0, "sessions_fetch", ip=request.remote_addr,
                         meta=result)
            flash(f"Fetch result: {result}")
        except Exception as e:
            flash(f"Fetch failed: {e}", "error")
        return redirect(url_for("sessions_page"))