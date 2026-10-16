"""Admin + user routes for the Casino automator."""
import asyncio
from flask import (flash, redirect, render_template_string, request,
                   url_for)

from .audit import record as audit_record
from .config import CONFIG
from .logging_setup import log_flask
from . import casino as CAS
from . import casino_panel as CP
from . import db


# ===========================================================================
# Admin routes
# ===========================================================================
def register_casino_admin_routes(app, login_required, csrf_check, csrf_token,
                                  render_page, get_runtime_safe,
                                  current_admin_id, current_admin_name):
    """Register casino routes for the admin panel."""

    def _owner_id() -> int:
        return int(CONFIG.get("owner_id", 338266658))

    @app.route("/admin/casino")
    @login_required
    def casino_page():
        uid = _owner_id()
        cfg = CAS.get_config(uid)
        stats = db.casino_stats(uid)
        runs = db.list_casino_runs(uid, limit=30)
        logs = db.list_casino_logs(uid, limit=30)
        body = render_template_string(
            CP.CASINO_ADMIN_TEMPLATE,
            cfg=cfg, stats=stats, runs=runs, logs=logs, csrf=csrf_token(),
        )
        return render_page("Casino", body, "casino")

    @app.route("/admin/casino/save", methods=["POST"])
    @login_required
    def casino_save():
        csrf_check()
        uid = _owner_id()

        # chat_id
        chat_raw = (request.form.get("chat_id") or "").strip()
        chat_id = None
        if chat_raw:
            try:
                chat_id = int(chat_raw)
            except ValueError:
                flash("Invalid chat ID.", "error")
                return redirect(url_for("casino_page"))

        # initial bet
        try:
            initial = int(request.form.get("initial_bet", "100000"))
            initial = max(1, initial)
        except ValueError:
            initial = 100000

        # max bet
        try:
            max_bet = int(request.form.get("max_bet", "100000000"))
            max_bet = max(initial, max_bet)
        except ValueError:
            max_bet = 100_000_000

        # interval
        try:
            interval = int(request.form.get("bet_interval", "301"))
            interval = max(60, interval)
        except ValueError:
            interval = 301

        mults = (request.form.get("multipliers") or "1.5,2,2.5,3").strip()
        strategy = request.form.get("strategy_mode", "martingale")

        CAS.save_config(
            uid,
            chat_id=chat_id,
            initial_bet=initial,
            current_bet=initial,
            max_bet=max_bet,
            bet_interval=interval,
            multipliers=mults,
            strategy_mode=strategy,
        )
        audit_record(current_admin_id(), "casino_save", target=str(uid),
                     ip=request.remote_addr,
                     admin_name=current_admin_name(),
                     meta={"chat_id": chat_id, "initial": initial,
                            "interval": interval})
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

        # spawn loop if starting
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


# ===========================================================================
# User routes (registered on the user blueprint)
# ===========================================================================
def register_casino_user_routes(bp, login_required, csrf_check, csrf_token,
                                 render_layout, get_uid):
    """Register casino routes on the user blueprint.

    `render_layout` is a callable: render_layout(title, body, active) -> HTML
    `get_uid` returns the current user id (or None).
    """

    def _owner_id() -> int:
        return int(CONFIG.get("owner_id", 338266658))

    @bp.route("/casino")
    @login_required
    def casino_page():
        uid = get_uid()
        cfg = CAS.get_config(uid)
        stats = db.casino_stats(uid)
        runs = db.list_casino_runs(uid, limit=30)

        # Permission rules
        is_admin = (uid == _owner_id())
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

    @bp.route("/casino/save", methods=["POST"])
    @login_required
    def casino_save():
        csrf_check()
        uid = get_uid()
        cfg = CAS.get_config(uid)
        is_admin = (uid == _owner_id())

        # chat_id (admin only)
        if is_admin:
            chat_raw = (request.form.get("chat_id") or "").strip()
            if chat_raw:
                try:
                    CAS.set_chat(uid, int(chat_raw))
                except ValueError:
                    flash("Invalid chat ID.", "error")
                    return redirect(url_for("user.casino_page"))

        # initial bet
        try:
            initial = int(request.form.get("initial_bet", str(cfg["initial_bet"])))
            initial = max(1, initial)
        except ValueError:
            initial = cfg["initial_bet"]
        CAS.save_config(uid, initial_bet=initial, current_bet=initial)

        # interval
        try:
            interval = int(request.form.get("bet_interval",
                                             str(cfg["bet_interval"])))
            interval = max(60, interval)
        except ValueError:
            interval = cfg["bet_interval"]
        CAS.set_interval(uid, interval)

        flash("Casino settings saved.")
        return redirect(url_for("user.casino_page"))

    @bp.route("/casino/toggle", methods=["POST"])
    @login_required
    def casino_toggle():
        csrf_check()
        uid = get_uid()
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