#!/usr/bin/env python3
"""
SELF BOT — entrypoint.
"""
import os
import sys
import threading
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from selfbot.config import CONFIG, DB_PATH, release_lock           # noqa: E402
from selfbot.logging_setup import log, log_bot, log_flask           # noqa: E402
from selfbot.bootstrap import boot_all, shutdown_all                # noqa: E402
from selfbot.admin_panel import (build_admin_app,                    # noqa: E402
                                  _login_required, _csrf_check,
                                  _csrf_token, _current_admin_id,
                                  _current_admin_name)
from selfbot.user_panel import build_user_app                       # noqa: E402
from selfbot.api import attach_api_routes                           # noqa: E402
from selfbot.bot_handlers import build_bot_runtime                  # noqa: E402

_lock_fh = None
_HAS_LOCK = False

try:
    import fcntl
    _lock_fh = open(DB_PATH / "lock", "w")
    fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _lock_fh.write(str(os.getpid()))
    _lock_fh.flush()
    _HAS_LOCK = True
except (BlockingIOError, OSError):
    _HAS_LOCK = False
    if _lock_fh:
        try:
            _lock_fh.close()
        except Exception:
            pass
        _lock_fh = None
except Exception:
    _HAS_LOCK = False

_runtime = None
_runtime_lock = threading.Lock()


def _get_runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = build_bot_runtime()
        return _runtime


# ===========================================================================
# Build Flask app
# ===========================================================================
admin_app = build_admin_app()
user_app_bp = build_user_app()

flask_app = admin_app
attach_api_routes(flask_app)
flask_app.register_blueprint(user_app_bp)


# ===========================================================================
# Register casino routes (admin + user)
# ===========================================================================
def _register_casino_routes():
    try:
        from selfbot.casino_routes import (
            register_casino_admin_routes,
            register_casino_user_routes,
        )
    except Exception as e:
        log_flask.exception(f"could not import casino_routes: {e}")
        return

    # --- Admin routes ---
    try:
        register_casino_admin_routes(
            flask_app,
            login_required=_login_required,
            csrf_check=_csrf_check,
            csrf_token=_csrf_token,
            render_page=admin_app.view_functions.get("_render_page")
                       and _make_render_page(),
            get_runtime_safe=_get_runtime,
            current_admin_id=_current_admin_id,
            current_admin_name=_current_admin_name,
        )
        log_flask.info("casino admin routes registered")
    except Exception as e:
        log_flask.exception(f"could not register casino admin routes: {e}")

    # --- User routes (on blueprint) ---
    try:
        def _render_layout_for_casino(title, body, active="casino"):
            from selfbot.user_panel import _render_layout_for_casino as _r
            return _r(title, body, active)

        def _get_uid():
            from flask import session
            return session.get("user_id")

        def _user_login_required(fn):
            from functools import wraps
            from flask import redirect, url_for, session
            @wraps(fn)
            def wrapper(*a, **kw):
                if not session.get("user_id"):
                    return redirect(url_for("user.login_page"))
                return fn(*a, **kw)
            return wrapper

        def _user_csrf_token():
            from selfbot.user_panel import _csrf_token as _t
            return _t()

        def _user_csrf_check():
            from selfbot.user_panel import _csrf_check as _c
            return _c()

        register_casino_user_routes(
            user_app_bp,
            login_required=_user_login_required,
            csrf_check=_user_csrf_check,
            csrf_token=_user_csrf_token,
            render_layout=_render_layout_for_casino,
            get_uid=_get_uid,
        )
        log_flask.info("casino user routes registered")
    except Exception as e:
        log_flask.exception(f"could not register casino user routes: {e}")


def _make_render_page():
    """Return the _render_page function from admin_panel."""
    # This is a helper to expose the internal _render_page of admin_panel.
    # The function is defined inside build_admin_app as a closure, so we
    # can't access it directly. Instead we use Flask's render_template_string
    # with the BASE_TEMPLATE directly.
    from flask import render_template_string
    from selfbot.admin_panel import BASE_TEMPLATE

    def _render_page(title: str, body: str, page: str):
        return render_template_string(
            BASE_TEMPLATE, title=title, body=body, page=page)

    return _render_page


_register_casino_routes()


# ===========================================================================
# Boot bot
# ===========================================================================
def _boot_bots():
    try:
        boot_all(_get_runtime())
    except Exception as e:
        log_bot.exception(f"boot failed: {e}")


_boot_started_lock = threading.Lock()
_boot_started = False


def _ensure_started():
    global _boot_started
    with _boot_started_lock:
        if _boot_started:
            return
        _boot_started = True
    t = threading.Thread(target=_boot_bots, daemon=True, name="selfbot-boot")
    t.start()


if _HAS_LOCK:
    _ensure_started()


import atexit  # noqa: E402


@atexit.register
def _on_exit():
    try:
        shutdown_all(_get_runtime())
    except Exception as e:
        log.error(f"shutdown error: {e}")
    try:
        if _lock_fh:
            import fcntl
            fcntl.flock(_lock_fh, fcntl.LOCK_UN)
            _lock_fh.close()
    except Exception:
        pass
    try:
        release_lock()
    except Exception:
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", CONFIG.get("port", 8080)))
    log_flask.info(f"starting admin panel on 0.0.0.0:{port}")
    try:
        flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    finally:
        _on_exit()