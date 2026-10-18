"""Admin + user routes for the Casino automator.

Casino access is locked by default for non-owner users.
Users can request access; admin approves from /admin/casino/access.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from flask import (flash, redirect, render_template_string, request,
                   Response, url_for)

from .audit import record as audit_record
from .config import CONFIG
from .logging_setup import log_flask
from . import casino as CAS
from . import casino_panel as CP
from . import casino_export as EXP
from . import db
from . import requests_mod as RM


# ===========================================================================
# Tehran timezone helpers
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


def _enrich_cfg_times(cfg: dict) -> dict:
    cfg["last_run_tehran"] = _to_tehran(cfg.get("last_run", ""))
    cfg["next_run_tehran"] = _to_tehran(cfg.get("next_run", ""))
    return cfg


def _enrich_runs(runs: list) -> list:
    for r in runs:
        r["started_at_tehran"] = _to_tehran(r.get("started_at", ""))
        r["finished_at_tehran"] = _to_tehran(r.get("finished_at", ""))
    return runs


def _enrich_logs(logs: list) -> list:
    for l in logs:
        l["created_at_tehran"] = _to_tehran(l.get("created_at", ""))
    return logs


def _enrich_requests(reqs: list) -> list:
    for r in reqs:
        r["created_at_tehran"] = _to_tehran(r.get("created_at", ""))
        r["decided_at_tehran"] = _to_tehran(r.get("decided_at", ""))
    return reqs


def _compute_filter_counts(runs: list) -> dict:
    return {
        "all": len(runs),
        "win": len(EXP.filter_runs(runs, "win")),
        "lose": len(EXP.filter_runs(runs, "lose")),
        "push": len(EXP.filter_runs(runs, "push")),
        "win_lose": len(EXP.filter_runs(runs, "win_lose")),
        "win_push": len(EXP.filter_runs(runs, "win_push")),
        "lose_push": len(EXP.filter_runs(runs, "lose_push")),
    }


# ===========================================================================
# Admin routes
# ===========================================================================
def register_casino_admin_routes(app, login_required, csrf_check, csrf_token,
                                 render_page, get_runtime_safe,
                                 current_admin_id, current_admin_name):
    """Register casino routes for the admin panel."""

    def _owner_id() -> int:
        return int(CONFIG.get("owner_id", 338266658))

    # ---------------- Main casino page ----------------
    @app.route("/admin/casino")
    @login_required
    def casino_page():
        uid = _owner_id()
        cfg = CAS.get_config(uid)
        stats = db.casino_stats(uid)
        runs = db.list_casino_runs(uid, limit=30)
        logs = db.list_casino_logs(uid, limit=30)

        _enrich_cfg_times(cfg)
        _enrich_runs(runs)
        _enrich_logs(logs)

        unlocked_users = db.list_casino_unlocked_users()
        pending_access = RM.pending_casino_requests()

        body = render_template_string(
            CP.CASINO_ADMIN_TEMPLATE,
            cfg=cfg, stats=stats, runs=runs, logs=logs, csrf=csrf_token(),
            unlocked_users=unlocked_users,
            unlocked_count=len(unlocked_users),
            pending_access_count=len(pending_access),
        )
        return render_page("Casino", body, "casino")

    # ---------------- Access management page ----------------
    @app.route("/admin/casino/access")
    @login_required
    def casino_access_page():
        owner = _owner_id()
        pending = RM.pending_casino_requests()
        _enrich_requests(pending)

        # all decided requests (for history)
        decided = db.list_requests(status=None, req_type="casino_access",
                                    limit=100)
        decided = [r for r in decided if r.get("status") != "pending"]
        _enrich_requests(decided)

        unlocked_users = db.list_casino_unlocked_users()
        # enrich with user info
        for u in unlocked_users:
            uid = int(u.get("id", 0))
            u["username_display"] = (
                f"@{u.get('username')}" if u.get("username") else "—"
            )
            u["first_name"] = u.get("first_name", "") or "—"

        body = render_template_string(
            CP.CASINO_ACCESS_TEMPLATE,
            pending=pending,
            decided=decided[:50],
            unlocked_users=unlocked_users,
            unlocked_count=len(unlocked_users),
            owner_id=owner,
            csrf=csrf_token(),
        )
        return render_page("Casino Access", body, "casino")

    @app.route("/admin/casino/access/<rid>/approve", methods=["POST"])
    @login_required
    def casino_access_approve(rid: str):
        csrf_check()
        r = RM.approve(rid, current_admin_id())
        if r:
            audit_record(current_admin_id(), "casino_access_approve",
                         target=str(r.get("user_id")),
                         ip=request.remote_addr,
                         admin_name=current_admin_name(),
                         meta={"req": rid})
            flash(f"Casino access APPROVED for user {r.get('user_id')}.")
        else:
            flash("Request not found or already decided.", "error")
        return redirect(url_for("casino_access_page"))

    @app.route("/admin/casino/access/<rid>/reject", methods=["POST"])
    @login_required
    def casino_access_reject(rid: str):
        csrf_check()
        r = RM.reject(rid, current_admin_id())
        if r:
            audit_record(current_admin_id(), "casino_access_reject",
                         target=str(r.get("user_id")),
                         ip=request.remote_addr,
                         admin_name=current_admin_name(),
                         meta={"req": rid})
            flash(f"Casino access REJECTED for user {r.get('user_id')}.")
        else:
            flash("Request not found or already decided.", "error")
        return redirect(url_for("casino_access_page"))

    @app.route("/admin/casino/access/unlock/<int:uid>", methods=["POST"])
    @login_required
    def casino_access_unlock(uid: int):
        csrf_check()
        if CAS.is_owner(uid):
            flash("Owner is always unlocked.", "error")
            return redirect(url_for("casino_access_page"))
        CAS.unlock(uid)
        audit_record(current_admin_id(), "casino_access_unlock",
                     target=str(uid), ip=request.remote_addr,
                     admin_name=current_admin_name())
        flash(f"Casino UNLOCKED for user {uid}.")
        return redirect(url_for("casino_access_page"))

    @app.route("/admin/casino/access/lock/<int:uid>", methods=["POST"])
    @login_required
    def casino_access_lock(uid: int):
        csrf_check()
        if CAS.is_owner(uid):
            flash("Owner cannot be locked.", "error")
            return redirect(url_for("casino_access_page"))
        CAS.lock(uid)
        audit_record(current_admin_id(), "casino_access_lock",
                     target=str(uid), ip=request.remote_addr,
                     admin_name=current_admin_name())
        flash(f"Casino LOCKED for user {uid}.")
        return redirect(url_for("casino_access_page"))

    # ---------------- Config / toggle / trigger ----------------
    @app.route("/admin/casino/save", methods=["POST"])
    @login_required
    def casino_save():
        csrf_check()
        uid = _owner_id()

        chat_raw = (request.form.get("chat_id") or "").strip()
        chat_id = None
        if chat_raw:
            try:
                chat_id = int(chat_raw)
            except ValueError:
                flash("Invalid chat ID.", "error")
                return redirect(url_for("casino_page"))

        try:
            initial = int(request.form.get("initial_bet", "100000"))
            initial = max(CAS.MIN_BET, min(CAS.MAX_BET, initial))
        except ValueError:
            initial = CAS.START_BET

        try:
            max_bet = int(request.form.get("max_bet", str(CAS.MAX_BET)))
            max_bet = max(initial, max_bet)
        except ValueError:
            max_bet = CAS.MAX_BET
        max_bet = min(max_bet, CAS.MAX_BET)

        try:
            interval = int(request.form.get("bet_interval", "301"))
            interval = max(60, interval)
        except ValueError:
            interval = CAS.DEFAULT_INTERVAL

        mults = (request.form.get("multipliers") or "1.5,2,2.5,3").strip()
        strategy = request.form.get("strategy_mode", "martingale")
        if strategy not in ("martingale", "flat"):
            strategy = "martingale"

        old_cfg = CAS.get_config(uid)
        fields = dict(
            chat_id=chat_id,
            initial_bet=initial,
            max_bet=max_bet,
            max_allowed_bet=max_bet,
            bet_interval=interval,
            multipliers=mults,
            strategy_mode=strategy,
        )

        if initial != old_cfg["initial_bet"]:
            fields["current_bet"] = initial
            fields["peak_bet"] = initial

        cur = int(fields.get("current_bet", old_cfg["current_bet"]))
        clamped = CAS._round_down_to_cap(cur, max_bet)
        if clamped != cur:
            fields["current_bet"] = clamped

        if chat_id != old_cfg["chat_id"]:
            fields["panel_msg_id"] = 0

        CAS.save_config(uid, **fields)
        audit_record(current_admin_id(), "casino_save", target=str(uid),
                     ip=request.remote_addr,
                     admin_name=current_admin_name(),
                     meta={"chat_id": chat_id, "initial": initial,
                           "interval": interval,
                           "max_bet": max_bet,
                           "multipliers": mults})
        flash("Casino configuration saved.")
        return redirect(url_for("casino_page"))

    @app.route("/admin/casino/toggle", methods=["POST"])
    @login_required
    def casino_toggle():
        csrf_check()
        uid = _owner_id()
        cfg = CAS.get_config(uid)
        new_state = not cfg["enabled"]
        if new_state and not cfg["chat_id"]:
            flash("Set a chat ID first.", "error")
            return redirect(url_for("casino_page"))
        CAS.set_enabled(uid, new_state)

        if new_state:
            rt = get_runtime_safe()
            if rt:
                try:
                    from .bootstrap import _async_loop
                    if _async_loop and _async_loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            CAS.ensure_loop(rt, uid), _async_loop,
                        )
                except Exception as e:
                    log_flask.warning(f"casino loop spawn failed: {e}")

        audit_record(current_admin_id(),
                     "casino_start" if new_state else "casino_stop",
                     target=str(uid), ip=request.remote_addr,
                     admin_name=current_admin_name())
        flash(f"Casino {'started' if new_state else 'stopped'}.")
        return redirect(url_for("casino_page"))

    @app.route("/admin/casino/trigger", methods=["POST"])
    @login_required
    def casino_trigger():
        csrf_check()
        uid = _owner_id()
        rt = get_runtime_safe()
        if not rt:
            flash("Bot not connected.", "error")
            return redirect(url_for("casino_page"))
        try:
            from .bootstrap import _async_loop
            if _async_loop is None or not _async_loop.is_running():
                flash("Bot loop not running.", "error")
                return redirect(url_for("casino_page"))
            fut = asyncio.run_coroutine_threadsafe(
                CAS.trigger_now(rt, uid), _async_loop,
            )
            result = fut.result(timeout=120)
            if result.get("ok"):
                flash("Manual run started.")
            else:
                flash(f"Run failed: {result.get('error')}", "error")
        except Exception as e:
            flash(f"Trigger failed: {e}", "error")
        return redirect(url_for("casino_page"))

    @app.route("/admin/casino/reset", methods=["POST"])
    @login_required
    def casino_reset():
        csrf_check()
        uid = _owner_id()
        CAS.reset_progress(uid)
        audit_record(current_admin_id(), "casino_reset",
                     target=str(uid), ip=request.remote_addr,
                     admin_name=current_admin_name())
        flash("Casino progress reset.")
        return redirect(url_for("casino_page"))

    @app.route("/admin/casino/clear_logs", methods=["POST"])
    @login_required
    def casino_clear_logs():
        csrf_check()
        uid = _owner_id()
        db.clear_casino_logs(uid)
        flash("Logs cleared.")
        return redirect(url_for("casino_page"))

    @app.route("/admin/casino/clear_runs", methods=["POST"])
    @login_required
    def casino_clear_runs():
        csrf_check()
        uid = _owner_id()
        n = db.clear_casino_runs(uid)
        audit_record(current_admin_id(), "casino_clear_runs",
                     target=str(uid), ip=request.remote_addr,
                     admin_name=current_admin_name(),
                     meta={"deleted": n})
        flash(f"Deleted {n} runs.")
        return redirect(url_for("casino_page"))

    # ---------------- Export ----------------
    @app.route("/admin/casino/export", methods=["GET"])
    @login_required
    def casino_export_page():
        uid = _owner_id()
        filter_mode = request.args.get("filter", "all")
        all_runs = db.list_casino_runs(uid, limit=500)
        counts = _compute_filter_counts(all_runs)

        body = render_template_string(
            CP.CASINO_EXPORT_TEMPLATE,
            filter_mode=filter_mode,
            counts=counts,
            csrf=csrf_token(),
            back_url=url_for("casino_page"),
        )
        return render_page("Export Logs", body, "casino")

    @app.route("/admin/casino/export/html", methods=["GET"])
    @login_required
    def casino_export_html():
        uid = _owner_id()
        filter_mode = request.args.get("filter", "all")
        try:
            html = EXP.render_html(uid, filter_mode, limit=500)
        except Exception as e:
            log_flask.error(f"HTML export failed: {e}")
            return f"HTML export failed: {e}", 500

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"casino-log-{filter_mode}-{ts}.html"
        return Response(
            html,
            mimetype="text/html",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )

    @app.route("/admin/casino/export/pdf", methods=["GET"])
    @login_required
    def casino_export_pdf():
        uid = _owner_id()
        filter_mode = request.args.get("filter", "all")
        try:
            pdf_bytes = EXP.render_pdf(uid, filter_mode, limit=500)
        except Exception as e:
            log_flask.error(f"PDF export failed: {e}")
            return f"PDF export failed: {e}", 500

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"casino-log-{filter_mode}-{ts}.pdf"
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )


# ===========================================================================
# User routes
# ===========================================================================
def register_casino_user_routes(bp, login_required, csrf_check, csrf_token,
                                render_layout, get_uid):
    """Register casino routes on the user blueprint."""

    def _owner_id() -> int:
        return int(CONFIG.get("owner_id", 338266658))

    # ---------------- Casino page (locked or full) ----------------
    @bp.route("/casino")
    @login_required
    def casino_page():
        uid = get_uid()
        is_admin = (uid == _owner_id())

        # ---- Access check ----
        if CAS.is_locked(uid):
            pending_req = db.find_pending_request(uid, "casino_access")
            body = render_template_string(
                CP.CASINO_LOCKED_TEMPLATE,
                is_admin=is_admin,
                pending=pending_req,
                csrf=csrf_token(),
            )
            return render_layout("Casino (Locked)", body, "casino")

        # ---- Unlocked: show normal casino page ----
        cfg = CAS.get_config(uid)
        stats = db.casino_stats(uid)
        runs = db.list_casino_runs(uid, limit=30)

        _enrich_cfg_times(cfg)
        _enrich_runs(runs)

        can_edit_chat = is_admin
        can_edit_bet = True
        can_edit_interval = True

        body = render_template_string(
            CP.CASINO_USER_TEMPLATE,
            cfg=cfg, stats=stats, runs=runs, csrf=csrf_token(),
            can_edit_chat=can_edit_chat,
            can_edit_bet=can_edit_bet,
            can_edit_interval=can_edit_interval,
        )
        return render_layout("Casino", body, "casino")

    # ---------------- Request access ----------------
    @bp.route("/casino/request_access", methods=["POST"])
    @login_required
    def casino_request_access():
        csrf_check()
        uid = get_uid()

        if not CAS.is_locked(uid):
            flash("Casino is already unlocked for you.", "error")
            return redirect(url_for("user.casino_page"))

        existing = db.find_pending_request(uid, "casino_access")
        if existing:
            flash("You already have a pending request. Please wait.",
                  "error")
            return redirect(url_for("user.casino_page"))

        note = (request.form.get("note") or "").strip()[:500]
        req = RM.create_casino_access_request(uid, note=note)
        log_flask.info(
            f"user requested casino access uid={uid} req={req.get('id')}"
        )
        flash(
            "Request sent to admin. You will be notified when approved."
        )
        return redirect(url_for("user.casino_page"))

    # ---------------- Config / toggle / trigger ----------------
    @bp.route("/casino/save", methods=["POST"])
    @login_required
    def casino_save():
        csrf_check()
        uid = get_uid()

        if CAS.is_locked(uid):
            flash("Casino is locked. Request access first.", "error")
            return redirect(url_for("user.casino_page"))

        cfg = CAS.get_config(uid)
        is_admin = (uid == _owner_id())

        fields = {}

        if is_admin:
            chat_raw = (request.form.get("chat_id") or "").strip()
            if chat_raw:
                try:
                    new_chat = int(chat_raw)
                    if new_chat != cfg["chat_id"]:
                        fields["chat_id"] = new_chat
                        fields["panel_msg_id"] = 0
                except ValueError:
                    flash("Invalid chat ID.", "error")
                    return redirect(url_for("user.casino_page"))

        try:
            initial = int(request.form.get(
                "initial_bet", str(cfg["initial_bet"])))
            initial = max(CAS.MIN_BET, min(CAS.MAX_BET, initial))
        except ValueError:
            initial = cfg["initial_bet"]

        if initial != cfg["initial_bet"]:
            fields["initial_bet"] = initial
            fields["current_bet"] = initial
            fields["peak_bet"] = initial

        try:
            interval = int(request.form.get(
                "bet_interval", str(cfg["bet_interval"])))
            interval = max(60, interval)
        except ValueError:
            interval = cfg["bet_interval"]
        if interval != cfg["bet_interval"]:
            fields["bet_interval"] = interval

        mults = (request.form.get("multipliers") or "").strip()
        if mults and mults != cfg.get("multipliers", ""):
            fields["multipliers"] = mults

        if fields:
            CAS.save_config(uid, **fields)

        flash("Casino settings saved.")
        return redirect(url_for("user.casino_page"))

    @bp.route("/casino/toggle", methods=["POST"])
    @login_required
    def casino_toggle():
        csrf_check()
        uid = get_uid()

        if CAS.is_locked(uid):
            flash("Casino is locked. Request access first.", "error")
            return redirect(url_for("user.casino_page"))

        cfg = CAS.get_config(uid)
        new_state = not cfg["enabled"]
        if new_state and not cfg["chat_id"]:
            flash("Set a chat ID first.", "error")
            return redirect(url_for("user.casino_page"))
        CAS.set_enabled(uid, new_state)

        if new_state:
            try:
                from app import _get_runtime
                from .bootstrap import _async_loop
                rt = _get_runtime()
                if rt and _async_loop and _async_loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        CAS.ensure_loop(rt, uid), _async_loop,
                    )
            except Exception as e:
                log_flask.warning(f"user casino loop spawn failed: {e}")

        flash(f"Casino {'started' if new_state else 'stopped'}.")
        return redirect(url_for("user.casino_page"))

    @bp.route("/casino/trigger", methods=["POST"])
    @login_required
    def casino_trigger():
        csrf_check()
        uid = get_uid()

        if CAS.is_locked(uid):
            flash("Casino is locked. Request access first.", "error")
            return redirect(url_for("user.casino_page"))

        try:
            from app import _get_runtime
            from .bootstrap import _async_loop
            rt = _get_runtime()
            if not rt or not _async_loop or not _async_loop.is_running():
                flash("Bot not connected.", "error")
                return redirect(url_for("user.casino_page"))
            fut = asyncio.run_coroutine_threadsafe(
                CAS.trigger_now(rt, uid), _async_loop,
            )
            result = fut.result(timeout=120)
            if result.get("ok"):
                flash("Manual run started.")
            else:
                flash(f"Run failed: {result.get('error')}", "error")
        except Exception as e:
            flash(f"Trigger failed: {e}", "error")
        return redirect(url_for("user.casino_page"))

    @bp.route("/casino/clear_runs", methods=["POST"])
    @login_required
    def casino_clear_runs():
        csrf_check()
        uid = get_uid()

        if CAS.is_locked(uid):
            flash("Casino is locked.", "error")
            return redirect(url_for("user.casino_page"))

        n = db.clear_casino_runs(uid)
        flash(f"Deleted {n} runs.")
        return redirect(url_for("user.casino_page"))

    # ---------------- Export ----------------
    @bp.route("/casino/export", methods=["GET"])
    @login_required
    def casino_export_page():
        uid = get_uid()

        if CAS.is_locked(uid):
            flash("Casino is locked.", "error")
            return redirect(url_for("user.casino_page"))

        filter_mode = request.args.get("filter", "all")
        all_runs = db.list_casino_runs(uid, limit=500)
        counts = _compute_filter_counts(all_runs)

        body = render_template_string(
            CP.CASINO_EXPORT_TEMPLATE,
            filter_mode=filter_mode,
            counts=counts,
            csrf=csrf_token(),
            back_url=url_for("user.casino_page"),
        )
        return render_layout("Export Logs", body, "casino")

    @bp.route("/casino/export/html", methods=["GET"])
    @login_required
    def casino_export_html():
        uid = get_uid()

        if CAS.is_locked(uid):
            return "Casino is locked", 403

        filter_mode = request.args.get("filter", "all")
        try:
            html = EXP.render_html(uid, filter_mode, limit=500)
        except Exception as e:
            log_flask.error(f"HTML export failed: {e}")
            return f"HTML export failed: {e}", 500

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"casino-log-{filter_mode}-{ts}.html"
        return Response(
            html,
            mimetype="text/html",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )

    @bp.route("/casino/export/pdf", methods=["GET"])
    @login_required
    def casino_export_pdf():
        uid = get_uid()

        if CAS.is_locked(uid):
            return "Casino is locked", 403

        filter_mode = request.args.get("filter", "all")
        try:
            pdf_bytes = EXP.render_pdf(uid, filter_mode, limit=500)
        except Exception as e:
            log_flask.error(f"PDF export failed: {e}")
            return f"PDF export failed: {e}", 500

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"casino-log-{filter_mode}-{ts}.pdf"
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )