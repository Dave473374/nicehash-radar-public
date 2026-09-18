from pathlib import Path

ROOT = Path(".")
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules"}

FORBIDDEN_TEXT = [
    "adminapi." + "nicehash.com",
    "/main/api/v2/" + "admin/",
]

FORBIDDEN_PATH_PARTS = [
    "test_" + "admin_orders_access.py",
    "collect_global_" + "completed_orders.py",
    "refresh-global-" + "order-calibration.yml",
]

violations = []

for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    if any(part in SKIP_DIRS for part in path.parts):
        continue

    relative = path.as_posix()

    if any(part in relative for part in FORBIDDEN_PATH_PARTS):
        violations.append(f"forbidden path: {relative}")
        continue

    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue

    for marker in FORBIDDEN_TEXT:
        if marker in text:
            violations.append(
                f"forbidden Admin access marker in {relative}: {marker}"
            )

if violations:
    print("ADMIN ACCESS GUARD FAILED")
    for violation in violations:
        print("-", violation)
    raise SystemExit(1)

print("ADMIN ACCESS GUARD OK")
print("No forbidden NiceHash Admin access code is present in the working tree.")
