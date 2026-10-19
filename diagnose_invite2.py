"""Diagnose invite routes in the ACTUAL flask_app from app.py."""
import sys
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

print("=" * 70)
print("Import app.py (this triggers route registration)")
print("=" * 70)

try:
    import app as app_module
    flask_app = app_module.flask_app
    print("  OK    app imported, flask_app obtained")
except Exception as e:
    print(f"  FAIL  could not import app.py")
    print(f"        {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 70)
print("ALL /admin routes in flask_app")
print("=" * 70)
admin_rules = sorted(
    [r for r in flask_app.url_map.iter_rules() if str(r).startswith("/admin")],
    key=lambda r: str(r),
)
for r in admin_rules:
    print(f"  {str(r):<55}  ->  {r.endpoint}")

print()
print("=" * 70)
print("Invite routes")
print("=" * 70)
invite_rules = [r for r in flask_app.url_map.iter_rules()
                if "invite" in str(r).lower() or "invite" in r.endpoint.lower()]
if invite_rules:
    for r in invite_rules:
        print(f"  ✅ {str(r):<55}  ->  {r.endpoint}")
else:
    print("  ❌ NO INVITE ROUTES FOUND in flask_app!")

print()
print("=" * 70)
print("Endpoint check: 'invite_page'")
print("=" * 70)
found = any(r.endpoint == "invite_page" for r in flask_app.url_map.iter_rules())
print(f"  {'✅ FOUND' if found else '❌ MISSING'}")

print()
print("=" * 70)
print("Endpoint check: 'casino_page'")
print("=" * 70)
found = any(r.endpoint == "casino_page" for r in flask_app.url_map.iter_rules())
print(f"  {'✅ FOUND' if found else '❌ MISSING'}")

print()
print("=" * 70)
print("Endpoint check: 'sessions_page' (optional)")
print("=" * 70)
found = any(r.endpoint == "sessions_page" for r in flask_app.url_map.iter_rules())
print(f"  {'✅ FOUND' if found else '⚠️  MISSING (optional)'}")

print()
print("=" * 70)
print("Simulate the has_invite_routes detection used by _render_page")
print("=" * 70)
try:
    with flask_app.test_request_context("/admin/dashboard"):
        from flask import current_app
        _app = current_app._get_current_object()
        has = any(r.endpoint == "invite_page"
                  for r in _app.url_map.iter_rules())
        print(f"  has_invite_routes = {has}")
        if not has:
            print("  ⚠️  Invite link will be HIDDEN in the admin sidebar!")
except Exception as e:
    print(f"  FAIL: {e}")
    traceback.print_exc()

print()
print("=" * 70)
print("DONE")
print("=" * 70)