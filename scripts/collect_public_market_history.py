import json
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = "https://api2.nicehash.com"
OUTPUT = Path("calibration/public-market-history.jsonl")
RETAIN_DAYS = 30
RELEVANT = {
    "SCRYPT",
    "SHA256ASICBOOST",
    "SHA256ASICBOOST_USDT",
    "EQUIHASH",
    "KHEAVYHASH",
}


def get_json(path):
    req = urllib.request.Request(
        BASE + path,
        headers={
            "User-Agent": "NiceHash-Radar-Public-Market-History/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def iso_to_dt(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


buy_info = get_json("/main/api/v2/public/buy/info/")
current = get_json("/main/api/v2/public/stats/global/current/")

settings_by_id = {}
for item in buy_info.get("miningAlgorithms", []):
    name = str(item.get("name") or "").upper()
    if name not in RELEVANT:
        continue

    algo_id = item.get("algo")
    if algo_id is None:
        continue

    settings_by_id[int(algo_id)] = {
        "name": name,
        "algoId": int(algo_id),
        "speedUnit": item.get("speed_text"),
        "multi": item.get("multi"),
        "priceMulti": item.get("price_multi"),
    }

algorithms = {}
for row in current.get("algos", []):
    algo_id = row.get("a")
    try:
        algo_id = int(algo_id)
    except (TypeError, ValueError):
        continue

    settings = settings_by_id.get(algo_id)
    if not settings:
        continue

    algorithms[settings["name"]] = {
        **settings,
        "speedRaw": row.get("s"),
        "priceRaw": row.get("p"),
        "rigs": row.get("r"),
        "orders": row.get("o"),
        "volumeRaw": row.get("v"),
    }

if not algorithms:
    raise SystemExit("No relevant public market algorithms found")

snapshot = {
    "collected_at": datetime.now(timezone.utc).isoformat(),
    "source": "NICEHASH_PUBLIC_MARKET",
    "credentials_used": False,
    "private_api_used": False,
    "admin_api_used": False,
    "algorithms": algorithms,
}

existing = []
if OUTPUT.exists():
    for line in OUTPUT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            existing.append(row)

cutoff = datetime.now(timezone.utc) - timedelta(days=RETAIN_DAYS)
kept = []
seen = set()

for row in existing + [snapshot]:
    ts = iso_to_dt(row.get("collected_at"))
    if ts is None or ts < cutoff:
        continue

    key = row.get("collected_at")
    if key in seen:
        continue

    seen.add(key)
    kept.append(row)

kept.sort(key=lambda row: row.get("collected_at") or "")
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(
    "".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n" for row in kept),
    encoding="utf-8",
)

print("PUBLIC MARKET HISTORY OK")
print("Credentials used: NO")
print("Private API used: NO")
print("Admin API used: NO")
print("Algorithms:", ", ".join(sorted(algorithms)))
print("Snapshots retained:", len(kept))
print("Output:", OUTPUT)
