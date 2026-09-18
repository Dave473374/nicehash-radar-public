from pathlib import Path

ROOT = Path(".")
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules"}

AUTH_MARKERS = [
    "NICEHASH_API_SECRET",
    "X-Auth",
    "hmac.new",
]

AUTH_CODE_ALLOWLIST = {
    "scripts/nicehash_private_readonly.py",
}

WORKFLOW_SECRET_ALLOWLIST = {
    ".github/workflows/collect-calibration.yml",
    ".github/workflows/test-nicehash-private.yml",
}

FORBIDDEN_PRIVATE_METHOD_PATTERNS = [
    "requests.post(",
    "requests.put(",
    "requests.patch(",
    "requests.delete(",
]

violations = []

for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    if any(part in SKIP_DIRS for part in path.parts):
        continue

    relative = path.as_posix()

    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue

    if any(marker in content for marker in AUTH_MARKERS):
        allowed = (
            relative in AUTH_CODE_ALLOWLIST
            or relative in WORKFLOW_SECRET_ALLOWLIST
        )
        if not allowed:
            violations.append(
                f"private API auth material used outside allowlist: {relative}"
            )

    if "api2.nicehash.com" in content:
        if relative not in AUTH_CODE_ALLOWLIST and not relative.startswith(
            "scripts/collect_public_market_history.py"
        ) and relative not in {"fetch_recent_blocks.py"}:
            violations.append(
                f"direct api2.nicehash.com host use outside approved modules: {relative}"
            )

    if relative in AUTH_CODE_ALLOWLIST:
        for pattern in FORBIDDEN_PRIVATE_METHOD_PATTERNS:
            if pattern in content:
                violations.append(
                    f"write-capable private HTTP method found: {pattern}"
                )

if violations:
    print("PRIVATE API POLICY GUARD FAILED")
    for violation in violations:
        print("-", violation)
    raise SystemExit(1)

print("PRIVATE API POLICY GUARD OK")
print("Private API auth is centralized in the read-only allowlisted module.")
