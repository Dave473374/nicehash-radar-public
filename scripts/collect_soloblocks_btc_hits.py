"""Collect NiceHash EasyMining BTC block heights from SoloBlocks.io.

Research-only public evidence. This collector never accesses a NiceHash account,
never creates orders, and never supplies a MISS denominator or BUY signal.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import urllib.request

BASE = "https://soloblocks.io"
DEFAULT_OUTPUT = Path("research/soloblocks-btc-hits.jsonl")
MAX_RESPONSE_BYTES = 2_000_000
PAGE_SIZE = 10

_BLOCK_LINK_RE = re.compile(
    r"href\s*=\s*[\"'](?:https://soloblocks\.io)?/block/(\d+)(?:[/?#][^\"']*)?[\"']",
    re.IGNORECASE,
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("SoloBlocks redirect refused")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def history_section(document: str) -> str:
    lower = document.lower()
    start = lower.find("full block history")
    if start < 0:
        raise ValueError("SoloBlocks Full Block History marker missing")
    end = lower.find("blocks by pool", start + 1)
    if end < 0 or end <= start:
        raise ValueError("SoloBlocks Blocks by Pool marker missing")
    return document[start:end]


def extract_block_heights(document: str):
    section = history_section(document)
    heights = []
    seen = set()
    for raw in _BLOCK_LINK_RE.findall(section):
        height = int(raw)
        if height < 1 or height in seen:
            continue
        seen.add(height)
        heights.append(height)

    if heights:
        label_count = section.lower().count("nicehash easymining")
        if label_count < len(heights):
            raise ValueError("SoloBlocks NiceHash filter attribution mismatch")
    return heights


def page_url(page: int) -> str:
    if page < 1:
        raise ValueError("page must be >= 1")
    if page == 1:
        return BASE + "/?pool=nicehash"
    return BASE + f"/?page={page}&pool=nicehash"


def fetch_page(page: int, opener=None):
    url = page_url(page)
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": "nicehash-radar-public/soloblocks-research-1.0",
            "Accept": "text/html",
        },
    )
    opener = opener or urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=30) as response:
        if response.status != 200 or response.geturl() != url:
            raise RuntimeError("Unexpected SoloBlocks response")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("SoloBlocks response too large")
    return extract_block_heights(raw.decode("utf-8", errors="strict"))


def to_record(height: int, collected_at: str):
    height = int(height)
    return {
        "schemaVersion": 1,
        "eventId": f"SOLOBLOCKS|BTC|{height}",
        "collectedAt": collected_at,
        "coin": "BTC",
        "blockHeight": height,
        "blockHash": None,
        "packageId": None,
        "packageName": None,
        "easyMiningFamily": "Gold",
        "packageVariantKnown": False,
        "source": "SOLOBLOCKS",
        "sourceUrl": f"{BASE}/block/{height}",
        "sourceAttribution": "NiceHash EasyMining",
        "independentVerificationRequired": True,
        "canRaiseBuySignal": False,
        "canSupplyMissDenominator": False,
    }


def load_existing(path: Path):
    rows = {}
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            continue
        if str(row.get("coin") or "").upper() != "BTC":
            continue
        try:
            height = int(row.get("blockHeight"))
        except (TypeError, ValueError):
            continue
        rows[height] = row
    return rows


def collect(output: Path, max_pages: int, fetcher=fetch_page, now=None):
    if max_pages < 1 or max_pages > 100:
        raise ValueError("max_pages must be 1..100")
    now = now or utc_now()
    existing = load_existing(output)
    page_fingerprints = set()
    added = 0
    pages_checked = 0
    stop_reason = "MAX_PAGES"

    for page in range(1, max_pages + 1):
        heights = list(fetcher(page))
        pages_checked += 1
        if not heights:
            stop_reason = "EMPTY_PAGE"
            break

        fingerprint = tuple(heights)
        if fingerprint in page_fingerprints:
            stop_reason = "REPEATED_PAGE"
            break
        page_fingerprints.add(fingerprint)

        for height in heights:
            height = int(height)
            if height not in existing:
                existing[height] = to_record(height, now)
                added += 1

        if len(heights) < PAGE_SIZE:
            stop_reason = "PARTIAL_PAGE"
            break

    rows = [existing[h] for h in sorted(existing, reverse=True)]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(r, separators=(",", ":"), allow_nan=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    return {
        "pagesChecked": pages_checked,
        "recordsStored": len(rows),
        "recordsAdded": added,
        "stopReason": stop_reason,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-pages", type=int, default=30)
    args = parser.parse_args()
    result = collect(args.output, args.max_pages)
    print("SOLOBLOCKS BTC HIT COLLECTION COMPLETE; research-only; public GET only")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
