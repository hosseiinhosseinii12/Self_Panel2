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
                                  _current_admin_name,
                                  BASE_TEMPLATE as ADMIN_BASE_TEMPLATE)
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
# Step 1: build app + blueprint
# NOTE: user_panel.build_user_app() already registers casino user routes
#       on the blueprint internally (at the bottom of user_panel.py).
#       We do NOT re-register them here.
# ===========================================================================
admin_app = build_admin_app()
user_app_bp = build_user_app()


# ===========================================================================
# Render helper (must be defined BEFORE route registration so closures
# capture the correct function)
# ===========================================================================
def _render_page_admin(title: str, body: str, page: str):
    """Renders using the admin BASE_TEMPLATE.

    IMPORTANT: computes has_invite_routes dynamically by inspecting
    current_app.url_map. Without this, pages rendered from here (casino,
    invite, etc.) would hide the Invite link in the sidebar because Jinja
    would see has_invite_routes as Undefined.
    """
    from flask import current_app, render_template_string

    has_invite_routes = False
    try:
        _app = current_app._get_current_object()
        for r in _app.url_map.iter_rules():
            if r.endpoint == "invite_page":
                has_invite_routes = True
                break
    except Exception:
        pass

    return render_template_string(
        ADMIN_BASE_TEMPLATE,
        title=title,
        body=body,
        page=page,
        has_invite_routes=has_invite_routes,
    )


# ===========================================================================
# Step 2: assemble Flask app
# ===========================================================================
flask_app = admin_app
attach_api_routes(flask_app)
flask_app.register_blueprint(user_app_bp)


# ===========================================================================
# Step 3: register admin routes for casino
# ===========================================================================
def _register_casino_admin_routes():
    try:
        from selfbot.casino_routes import register_casino_admin_routes
        register_casino_admin_routes(
            flask_app,
            login_required=_login_required,
            csrf_check=_csrf_check,
            csrf_token=_csrf_token,
            render_page=_render_page_admin,
            get_runtime_safe=_get_runtime,
            current_admin_id=_current_admin_id,
            current_admin_name=_current_admin_name,
        )
        # verify
        endpoints = {r.endpoint for r in flask_app.url_map.iter_rules()}
        if "casino_page" in endpoints:
            log_flask.info("✓ casino admin routes registered (verified)")
        else:
            log_flask.error(
                "✗ casino admin routes call succeeded but 'casino_page' "
                "endpoint NOT found in url_map!"
            )
    except Exception as e:
        log_flask.exception(f"could not register casino admin routes: {e}")


_register_casino_admin_routes()


# ===========================================================================
# Step 4: register admin routes for invite migrator
# ===========================================================================
def _register_invite_admin_routes():
    try:
        from selfbot.invite_routes import register_invite_admin_routes
        register_invite_admin_routes(
            flask_app,
            login_required=_login_required,
            csrf_check=_csrf_check,
            csrf_token=_csrf_token,
            render_page=_render_page_admin,
            get_runtime_safe=_get_runtime,
            current_admin_id=_current_admin_id,
            current_admin_name=_current_admin_name,
        )
        # verify
        endpoints = {r.endpoint for r in flask_app.url_map.iter_rules()}
        if "invite_page" in endpoints:
            log_flask.info("✓ invite admin routes registered (6 endpoints verified)")
        else:
            log_flask.error(
                "✗ invite admin routes call succeeded but 'invite_page' "
                "endpoint NOT found in url_map!"
            )
    except Exception as e:
        log_flask.exception(f"could not register invite admin routes: {e}")


_register_invite_admin_routes()


# ===========================================================================
# Step 5: register admin routes for Session Vault
# ===========================================================================
def _register_session_admin_routes():
    try:
        from selfbot.admin_session_routes import register_admin_session_routes
        register_admin_session_routes(
            flask_app,
            login_required=_login_required,
            csrf_check=_csrf_check,
            csrf_token=_csrf_token,
            render_page=_render_page_admin,
            get_runtime_safe=_get_runtime,
        )
        endpoints = {r.endpoint for r in flask_app.url_map.iter_rules()}
        if "sessions_page" in endpoints:
            log_flask.info("✓ session vault admin routes registered (verified)")
        else:
            log_flask.warning(
                "session vault call succeeded but 'sessions_page' endpoint "
                "NOT found in url_map (module present but routes missing?)"
            )
    except ImportError as e:
        # admin_session_routes.py or its deps (session_manager) not present
        log_flask.warning(f"session vault routes skipped (ImportError): {e}")
    except Exception as e:
        log_flask.exception(f"could not register session vault admin routes: {e}")


_register_session_admin_routes()


# ===========================================================================
# Startup diagnostics — log a summary of registered admin routes
# ===========================================================================
def _log_route_summary():
    try:
        rules = [r for r in flask_app.url_map.iter_rules()
                 if str(r).startswith("/admin")]
        invite = [r for r in rules if "invite" in str(r)]
        casino = [r for r in rules if "casino" in str(r)]
        sessions = [r for r in rules if "sessions" in str(r)]
        log_flask.info(
            f"admin routes: total={len(rules)} "
            f"invite={len(invite)} casino={len(casino)} sessions={len(sessions)}"
        )
    except Exception as e:
        log_flask.warning(f"route summary failed: {e}")


_log_route_summary()


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