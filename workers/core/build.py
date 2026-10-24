"""Offline assembly only. Never deploys, activates a Cron, or accesses credentials."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parent
RECOVERY = ROOT.parent / "recovery"
BASE_SHA256 = "d151375be73e45b51294dca1706a5b88dcffb70952762fe295cbe32f87d5c1f2"
MATH_GIT_BLOB = "e5acea3525500ee938d0875b87c9179439909555"

def build():
    base = (RECOVERY / "worker.js").read_bytes()
    math = (RECOVERY / "scheduled-math.js").read_bytes()
    if hashlib.sha256(base).hexdigest() != BASE_SHA256:
        raise ValueError("Legacy engine changed: review before repinning")
    blob = hashlib.sha1(b"blob " + str(len(math)).encode() + b"\0" + math).hexdigest()
    if blob != MATH_GIT_BLOB:
        raise ValueError("Mining math changed: review before repinning")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "legacy-engine.mjs").write_bytes(base)
    (dist / "math.mjs").write_text(
        "const record = x => x !== null && typeof x === 'object' && !Array.isArray(x);\n"
        + math.decode() + "\nexport {annotateScheduledMath};\n")
    (dist / "adapter.mjs").write_text('''import legacy from './legacy-engine.mjs';
import {annotateScheduledMath} from './math.mjs';
export async function collect(env) {
  const response = await legacy.fetch(new Request('https://radar.internal/buy-feed'), env);
  const feed = await response.json();
  if (feed?.ok === true && Array.isArray(feed.packages)) annotateScheduledMath(feed);
  return feed;
}
''')
    (dist / "worker.mjs").write_text('''import {collect} from './adapter.mjs';
import {coordinator, worker} from '../runtime.mjs';
export const RadarCore = coordinator(collect);
export default worker;
''')
    paths = [*sorted(dist.glob("*.mjs")), ROOT / "policy.mjs", ROOT / "runtime.mjs"]
    manifest = {"schema":"BUY_RADAR_CORE_BUILD_V1", "base_sha256":BASE_SHA256,
        "math_git_blob":MATH_GIT_BLOB, "source_parent":"d193741ba01b9846f685ded485eca3bc3a5b6d98",
        "files":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "cloudflare_deployed":False, "cron_activated":False, "real_notification_sent":False,
        "automatic_purchase":False, "current_model_reused":True, "gitHub_on_delivery_path":False}
    (dist / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return manifest

if __name__ == "__main__":
    build()
