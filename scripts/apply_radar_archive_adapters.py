"""One-time, hash-locked source adapter; removed after its reviewed branch commit.

Refuses unknown source revisions and verifies every resulting file before writing.
Only storage I/O changes; package math and eligibility logic stay byte-for-byte.
"""
import hashlib
from pathlib import Path

HASHES = {
 'backfill_radar_snapshots.py': ('aeeea7c8b55805cd693eb8a690dd85b6c462490c572daa2ca45cb3de70394635','982deb44e2d27373ede4716ddf232ccb6a01d80810128cf86ed934d987cf29df'),
 'build_alert_latency_report.py': ('c07b3d36ea6f1c8c247e0ec9f2df806197c2b2d36cb1d29aba6081165ede5cef','2d03f06fbec31ec20aa71a5791e67671b4783c6680092c42dbe3ed6240785f25'),
 'build_daily_package_stats.py': ('b6b96faabef2c68dd3a44d07d0282ea1c2f29b0a55f4179df1fa0ad3829cbceb','f87098f6c5b16aeaeafd50efdc16acf5e616855bf51f02c31cb9e7bb6f7a3a25'),
 'build_market_edge_research.py': ('490560aac8c8b97d3706eb7fe55c515dbdc4392e4b8f43d780b2a3691bdeb2e1','9921b7edcddd65b6c704a8329cdf80be410433318285b5b70c164866d87c1695'),
 'build_palladium_m_signal_exposure.py': ('be339a5be9f4426b4e260c7894e9fc76777b02609cc6364c1152511fa04b7df0','81e4da89bab6ff6aa5a50401c619e1ff60491bf20dfed8db6db3ada8b884a7fc'),
 'collect_calibration.py': ('78b14e403f97318da8d467ad3e3ba43a1b5ed5edfa80c08bf892fe59a3f29d3b','d3adfaf40f6907db24d73c923ecfb100b0fd7901396ac77eeb18c57100b660b0'),
 'export_radar_feature_window.py': ('106c1be48bc15f77ef2d8aad7416dc0007c73c2eedc5bc3a8e93eb4f0fb2ab6e','c086141a84d28285e112ccbcfa5cffb14fb551a7711c8bf0bb66c9eed77f0b9c'),
 'match_global_orders.py': ('9a5dbf7a0dcdf5beb3e82f982c604bfe6859ca56af8f886bc5624eba4a8b02a8','93c5778e862415b470e595b8a5d32e77d4c61a38b2a9d14d47e1ab169358a41d'),
 'match_mining_events.py': ('896a472ed563d701b06714a749bf0646c4a8d15c73da6e179a0e392208d7861e','37bfd46f39f4bad159ba9450e5825048f881680d3ac1d86e8f2755f2f1417e09'),
 'match_private_orders.py': ('df0323658a88e9df00706ac9a9ac285475d7797aa07d702e859bf64428e7545a','8a127a3cf63dc6e0092e34598e664edc0331598c743822ff7b1eb2b1c248460f'),
 'match_private_orders_radar_context.py': ('2505b8f0e8a1217911bb2f7842ec93196a1e777ce93c7977b09c28de7ac2e49e','71fa3dd2487e4a52789652b300523c344376606cefa22328d69213884082b480'),
 'match_radar_blocks.py': ('97f407d375ce500dee3332fadd1082875203670db0420db8c573a6197a9646a1','af001c47aefefd24d4a1e2a8f9e3502b3bfd0273d17dd5af263e9e2f78cf395d'),
}
planned = {}

def edit(name, replacements, imports):
    p=Path('scripts')/name
    original=p.read_bytes()
    digest=hashlib.sha256(original).hexdigest()
    old_hash,new_hash=HASHES[name]
    if digest==new_hash:return
    if digest!=old_hash:raise RuntimeError('Unexpected source revision: '+name)
    text=original.decode('utf-8')
    for old,new in replacements:
        if old not in text:raise RuntimeError('Missing expected code: '+name)
        text=text.replace(old,new)
    lines=text.splitlines(keepends=True)
    at=next(i for i,l in enumerate(lines) if l.startswith('import ') or l.startswith('from ') and not l.startswith('from __future__'))
    names=', '.join(imports)
    lines.insert(at, f'try:\n    from radar_snapshot_archive import {names}\nexcept ModuleNotFoundError:  # Also support package/spec imports from repository root.\n    from scripts.radar_snapshot_archive import {names}\n')
    out=''.join(lines).encode()
    if hashlib.sha256(out).hexdigest()!=new_hash:raise RuntimeError('Adapter output mismatch: '+name)
    planned[p]=(original,out)

edit('backfill_radar_snapshots.py',[
 ('OUTPUT_FILE.exists()','history_exists(OUTPUT_FILE)'),
 ('OUTPUT_FILE.read_text(encoding="utf-8")','read_history_text(OUTPUT_FILE, encoding="utf-8")'),
 ('known_commits, known_feed_hashes, latest_existing = load_existing()','base_history_sha = history_digest(OUTPUT_FILE) if history_exists(OUTPUT_FILE) else None\nknown_commits, known_feed_hashes, latest_existing = load_existing()'),
 ('''    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("a", encoding="utf-8") as handle:
        for snapshot in added:
            handle.write(
                json.dumps(
                    snapshot,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                + "\\n"
            )''','    append_history(OUTPUT_FILE, added, expected_sha256=base_history_sha)')],['history_exists','read_history_text','append_history','history_digest'])
edit('collect_calibration.py',[
 ('OUTPUT_FILE.exists()','history_exists(OUTPUT_FILE)'),
 ('OUTPUT_FILE.read_text(encoding="utf-8")','read_history_text(OUTPUT_FILE, encoding="utf-8")'),
 ('if feed_hash in existing_feed_hashes():','base_history_sha = history_digest(OUTPUT_FILE) if history_exists(OUTPUT_FILE) else None\nif feed_hash in existing_feed_hashes():'),
 ('''OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

with OUTPUT_FILE.open("a", encoding="utf-8") as handle:
    handle.write(
        json.dumps(
            snapshot,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\\n"
    )''','append_history(OUTPUT_FILE, [snapshot], expected_sha256=base_history_sha)')],['history_exists','read_history_text','append_history','history_digest'])
edit('build_alert_latency_report.py',[
 ('if not path.exists():','if not history_exists(path):'),
 ('path.read_text(encoding="utf-8")','read_history_text(path, encoding="utf-8")')],['history_exists','read_history_text'])
edit('build_daily_package_stats.py',[
 ('if not path.exists():','if not history_exists(path):'),
 ('path.read_text(\n        encoding="utf-8"\n    )','read_history_text(path, encoding="utf-8")')],['history_exists','read_history_text'])
edit('build_market_edge_research.py',[
 ("path.open(encoding='utf-8')","open_history(path, encoding='utf-8')"),
 ("path.open('rb')","open_history(path, 'rb')")],['open_history'])
edit('build_palladium_m_signal_exposure.py',[
 ('path.read_text(encoding="utf-8")','read_history_text(path, encoding="utf-8")')],['read_history_text'])
edit('export_radar_feature_window.py',[
 ('RADAR_HISTORY.exists()','history_exists(RADAR_HISTORY)'),
 ('RADAR_HISTORY.read_text(\n    encoding="utf-8"\n)','read_history_text(RADAR_HISTORY, encoding="utf-8")')],['history_exists','read_history_text'])
edit('match_global_orders.py',[
 ('RADAR_HISTORY.exists()','history_exists(RADAR_HISTORY)'),
 ('RADAR_HISTORY.read_text(\n        encoding="utf-8"\n    )','read_history_text(RADAR_HISTORY, encoding="utf-8")')],['history_exists','read_history_text'])
edit('match_mining_events.py',[
 ('open(SNAPSHOTS_FILE, encoding="utf-8")','open_history(SNAPSHOTS_FILE, encoding="utf-8")')],['open_history'])
edit('match_private_orders.py',[
 ('RADAR_HISTORY.read_text(encoding="utf-8")','read_history_text(RADAR_HISTORY, encoding="utf-8")')],['read_history_text'])
edit('match_private_orders_radar_context.py',[
 ('if not path.exists():','if not history_exists(path):'),
 ('args.radar.read_text(encoding="utf-8")','read_history_text(args.radar, encoding="utf-8")')],['history_exists','read_history_text'])
edit('match_radar_blocks.py',[
 ('open(SNAPSHOTS_FILE, encoding="utf-8")','open_history(SNAPSHOTS_FILE, encoding="utf-8")')],['open_history'])

for p,(original,out) in planned.items():
    if p.read_bytes()!=original:raise RuntimeError('Source changed during planning')
for p,(_,out) in planned.items():p.write_bytes(out)
print('HASH-VERIFIED ARCHIVE ADAPTERS:',len(planned))
