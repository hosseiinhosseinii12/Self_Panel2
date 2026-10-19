"""Diagnose import and runtime errors without starting Flask."""
import sys
import traceback

print("=== Python version ===")
print(sys.version)
print()

modules = [
    "selfbot.db",
    "selfbot.casino",
    "selfbot.casino_routes",
    "selfbot.casino_panel",
    "selfbot.casino_export",
    "selfbot.requests_mod",
    "selfbot.admin_panel",
    "selfbot.user_panel",
]

print("=== Import test ===")
for m in modules:
    try:
        __import__(m)
        print(f"  OK    {m}")
    except Exception as e:
        print(f"  FAIL  {m}")
        print(f"        {type(e).__name__}: {e}")
        traceback.print_exc()
        print()

print()
print("=== DB migration test ===")
try:
    from selfbot import db
    db.init_db()
    print("  OK    db.init_db()")
    u = db.get_user(338266658)
    if u:
        print(f"  OK    get_user(338266658) → {len(u)} columns")
        print(f"  casino_unlocked: {'casino_unlocked' in u}")
    else:
        print("  WARN  no owner user in DB (first run?)")
except Exception as e:
    print(f"  FAIL  {type(e).__name__}: {e}")
    traceback.print_exc()

print()
print("=== Casino access helpers ===")
try:
    from selfbot import casino
    print(f"  is_owner: {hasattr(casino, 'is_owner')}")
    print(f"  is_locked: {hasattr(casino, 'is_locked')}")
    print(f"  unlock: {hasattr(casino, 'unlock')}")
    print(f"  lock: {hasattr(casino, 'lock')}")
    print(f"  is_locked(338266658): {casino.is_locked(338266658)}")
    print(f"  is_locked(999999): {casino.is_locked(999999)}")
except Exception as e:
    print(f"  FAIL  {type(e).__name__}: {e}")
    traceback.print_exc()

print()
print("=== requests_mod helpers ===")
try:
    from selfbot import requests_mod as RM
    print(f"  create_casino_access_request: {hasattr(RM, 'create_casino_access_request')}")
    print(f"  pending_casino_requests: {hasattr(RM, 'pending_casino_requests')}")
except Exception as e:
    print(f"  FAIL  {type(e).__name__}: {e}")
    traceback.print_exc()

print()
print("=== db.list_requests signature ===")
try:
    from selfbot import db
    import inspect
    sig = inspect.signature(db.list_requests)
    print(f"  {sig}")
except Exception as e:
    print(f"  FAIL  {type(e).__name__}: {e}")

print()
print("=== Admin app build test ===")
try:
    from selfbot.admin_panel import build_admin_app
    app = build_admin_app()
    print(f"  OK    build_admin_app() → {len(list(app.url_map.iter_rules()))} routes")
    # list routes
    routes = sorted(str(r) for r in app.url_map.iter_rules())
    for r in routes:
        print(f"        {r}")
except Exception as e:
    print(f"  FAIL  {type(e).__name__}: {e}")
    traceback.print_exc()

print()
print("=== User blueprint test ===")
try:
    from selfbot.user_panel import build_user_app
    bp = build_user_app()
    print(f"  OK    build_user_app()")
    rules = sorted(str(r) for r in bp.deferred_functions if hasattr(r, '__name__'))
    print(f"        {len(bp.deferred_functions)} deferred functions")
except Exception as e:
    print(f"  FAIL  {type(e).__name__}: {e}")
    traceback.print_exc()

print()
print("=== DONE ===")