"""Admin routes for the invite migrator."""
import asyncio
from datetime import datetime, timezone, timedelta
from flask import flash, redirect, render_template_string, request, url_for

from .audit import record as audit_record
from .config import CONFIG
from .logging_setup import log_flask
from . import invite_migrator as IM
from . import invite_panel as IP
from . import db


# ===========================================================================
# Tehran timezone helper
# ===========================================================================
TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def _to_tehran(iso_str, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    try:
        if not iso_str:
            return "—"
        s = str(iso_str).strip()
        if not s:
            return "—"
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            return str(iso_str)[:19] or "—"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TEHRAN_TZ).strftime(fmt)
    except Exception:
        return "—"


def _enrich_jobs(jobs: list) -> list:
    for j in jobs:
        j["created_at_tehran"] = _to_tehran(j.get("created_at", ""))
        j["started_at_tehran"] = _to_tehran(j.get("started_at", ""))
        j["finished_at_tehran"] = _to_tehran(j.get("finished_at", ""))
    return jobs


def _enrich_items(items: list) -> list:
    for it in items:
        it["created_at_tehran"] = _to_tehran(it.get("created_at", ""))
    return items


# ===========================================================================
# Register admin routes
# ===========================================================================
def register_invite_admin_routes(app, login_required, csrf_check, csrf_token,
                                  render_page, get_runtime_safe,
                                  current_admin_id, current_admin_name):
    def _owner_id() -> int:
        return int(CONFIG.get("owner_id", 338266658))

    @app.route("/admin/invite")
    @login_required
    def invite_page():
        uid = _owner_id()
        jobs = IM.list_jobs(uid)
        _enrich_jobs(jobs)
        body = render_template_string(
            IP.INVITE_ADMIN_TEMPLATE,
            jobs=jobs, csrf=csrf_token(),
        )
        return render_page("Invite Migrator", body, "invite")

    @app.route("/admin/invite/new", methods=["POST"])
    @login_required
    def invite_new():
        csrf_check()
        uid = _owner_id()

        source = (request.form.get("source_chat") or "").strip()
        target = (request.form.get("target_chat") or "").strip()
        if not source or not target:
            flash("Source and target chat are required.", "error")
            return redirect(url_for("invite_page"))

        try:
            batch = int(request.form.get("batch_size", "10"))
        except ValueError:
            batch = 10
        try:
            delay = int(request.form.get("delay_seconds", "30"))
        except ValueError:
            delay = 30

        job = IM.create_job(uid, source, target, batch, delay)
        audit_record(current_admin_id(), "invite_new", target=job["id"],
                     ip=request.remote_addr,
                     admin_name=current_admin_name(),
                     meta={"source": source, "target": target,
                            "batch": batch, "delay": delay})
        flash(f"Job {job['id']} created.")
        return redirect(url_for("invite_job_detail", job_id=job["id"]))

    @app.route("/admin/invite/<job_id>")
    @login_required
    def invite_job_detail(job_id: str):
        job = IM.get_job(job_id)
        if not job:
            flash("Job not found.", "error")
            return redirect(url_for("invite_page"))

        _enrich_jobs([job])
        items = IM.job_items(job_id)
        _enrich_items(items)
        counts = db.invite_item_counts(job_id)

        body = render_template_string(
            IP.INVITE_JOB_TEMPLATE,
            job=job, items=items, counts=counts, csrf=csrf_token(),
        )
        return render_page(f"Invite Job {job_id}", body, "invite")

    @app.route("/admin/invite/<job_id>/run", methods=["POST"])
    @login_required
    def invite_job_run(job_id: str):
        csrf_check()
        rt = get_runtime_safe()
        if not rt:
            flash("Bot not connected.", "error")
            return redirect(url_for("invite_job_detail", job_id=job_id))

        job = IM.get_job(job_id)
        if not job:
            flash("Job not found.", "error")
            return redirect(url_for("invite_page"))

        if job["status"] == "running":
            flash("Job is already running.", "error")
            return redirect(url_for("invite_job_detail", job_id=job_id))

        if job["status"] == "done":
            flash(
                "Job already finished. Create a new one or delete this.",
                "error",
            )
            return redirect(url_for("invite_job_detail", job_id=job_id))

        try:
            from .bootstrap import _async_loop
            if _async_loop is None or not _async_loop.is_running():
                flash("Bot loop not running.", "error")
                return redirect(url_for("invite_job_detail", job_id=job_id))

            asyncio.run_coroutine_threadsafe(
                IM.run_job(rt, job_id), _async_loop,
            )
            flash("Job started in background. Refresh to see progress.")
        except Exception as e:
            flash(f"Failed to start: {e}", "error")
        return redirect(url_for("invite_job_detail", job_id=job_id))

    @app.route("/admin/invite/<job_id>/delete", methods=["POST"])
    @login_required
    def invite_job_delete(job_id: str):
        csrf_check()
        job = IM.get_job(job_id)
        if job and job["status"] == "running":
            flash("Cannot delete a running job.", "error")
            return redirect(url_for("invite_job_detail", job_id=job_id))
        IM.delete_job(job_id)
        audit_record(current_admin_id(), "invite_delete", target=job_id,
                     ip=request.remote_addr,
                     admin_name=current_admin_name())
        flash(f"Job {job_id} deleted.")
        return redirect(url_for("invite_page"))

    @app.route("/admin/invite/<job_id>/clear_items", methods=["POST"])
    @login_required
    def invite_job_clear_items(job_id: str):
        csrf_check()
        db.clear_invite_items(job_id)
        flash("Item list cleared.")
        return redirect(url_for("invite_job_detail", job_id=job_id))