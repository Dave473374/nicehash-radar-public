from pathlib import Path
import hashlib

ROOT = Path(".")
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules"}
SELF = "scripts/check_private_api_policy.py"

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
    ".github/workflows/collect-shared-packages.yml",
    ".github/workflows/test-nicehash-private.yml",
    ".github/workflows/validate-buy-radar-evidence.yml",
}

PUBLIC_API_HOST_ALLOWLIST = {
    "fetch_recent_blocks.py",
    "scripts/collect_public_market_history.py",
    "scripts/collect_realized_blocks.py",
}

# These exact reviewed Worker builds only GET the public package endpoint.
# Pin complete bytes rather than exempting a directory or any private-auth rule.
# A future changed build must be reviewed and have its digest explicitly updated.
PUBLIC_WORKER_SOURCE_DIGESTS = {
    "workers/recovery/base-worker-v2.9.0.js": "f150fdcca108302f84362224dec88dafa25e78ec9f868bca25165a5a2d41be57",
    "workers/recovery/worker.js": "d151375be73e45b51294dca1706a5b88dcffb70952762fe295cbe32f87d5c1f2",
    "workers/recovery/worker-scheduled.js": "b1f83880e7370b223e109efc72982b48892cca62099ca74a57ee046ef5f9ee79",
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

    if relative == SELF:
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
        if (
            relative not in AUTH_CODE_ALLOWLIST
            and relative not in PUBLIC_API_HOST_ALLOWLIST
            and hashlib.sha256(path.read_bytes()).hexdigest() != PUBLIC_WORKER_SOURCE_DIGESTS.get(relative)
        ):
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
