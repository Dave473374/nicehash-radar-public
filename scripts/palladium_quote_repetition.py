"""Describe repeated M quote improvements from the existing full-Git audit.

No network, new collector, outcome inference or change to the locked lag rule.
This is a retrospective diagnostic, NOT held-out trading validation.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import palladium_m_intraday as base

DISCOVERY_DATES = frozenset(base.DATES)
MIN_WORK_RISE_PERCENT = 10.0
MAX_PREVIOUS_GAP_SECONDS = 1200
MAX_CONFIRMATION_GAP_SECONDS = 300
COOLDOWN_SECONDS = 3600


def economic_gate(p):
    return (p.get('sourceMathClear') is True
            and p['returnDifficultyPercent'] > 100
            and p['returnHashratePercent'] > 100)


def brief(p):
    return {k: p.get(k) for k in ('sourceCommit', 'quoteAt', 'availableAt', 'dateLocal',
        'relayVersion', 'workThHoursFor001Btc', 'costBtcPerThDay',
        'returnDifficultyPercent', 'returnHashratePercent', 'sourceMathClear')}


def summarize(rows, events, eligible_count):
    return {'quoteCount': len(rows), 'eligibleAdjacentComparisons': eligible_count,
        'observedDays': sorted({p['dateLocal'] for p in rows}),
        'bothAbove100AndMathPassQuotes': sum(economic_gate(p) for p in rows),
        'workRise10PercentEvents': len(events),
        'workRiseAndEconomicGateEvents': sum(e['economicGate'] for e in events),
        'rapidRepeatConfirmedEvents': sum(e['economicGate'] and e['confirmationWithin5m'] for e in events),
        'workRisePercent': base.describe([e['workChangePercent'] for e in events]),
        'eventsAreIndependentTrials': False}


def analyze(rows, dates):
    counts = Counter()
    rows = base.deduplicate(rows, counts)
    selected = [p for p in rows if p['dateLocal'] in dates]
    events, eligible = [], Counter()
    for i in range(1, len(rows)):
        p, prev = rows[i], rows[i-1]
        if p['dateLocal'] not in dates:
            continue
        at, earlier = base.timestamp(p['availableAt']), base.timestamp(prev['availableAt'])
        gap = (at-earlier).total_seconds()
        if not 0 < gap <= MAX_PREVIOUS_GAP_SECONDS or not base.same_series(p, prev):
            counts['gapOrSeriesBoundary'] += 1
            continue
        group = 'discovery' if p['dateLocal'] in DISCOVERY_DATES else 'additional'
        eligible[group] += 1
        delta = base.change(p['workThHoursFor001Btc'], prev['workThHoursFor001Btc'])
        if delta < MIN_WORK_RISE_PERCENT:
            continue
        nxt = rows[i+1] if i+1<len(rows) else None
        next_gap = ((base.timestamp(nxt['availableAt'])-at).total_seconds() if nxt else None)
        next_valid = (nxt is not None and 0 < next_gap <= MAX_PREVIOUS_GAP_SECONDS
                      and base.same_series(p, nxt))
        confirmation = (next_valid and next_gap<=MAX_CONFIRMATION_GAP_SECONDS
            and nxt['quoteAt']!=p['quoteAt'] and economic_gate(nxt)
            and nxt['workThHoursFor001Btc']>=p['workThHoursFor001Btc']*.95)
        event = {'group': group, 'dateLocal': p['dateLocal'], 'localAt': at.astimezone(base.TZ).isoformat(),
            'previous': brief(prev), 'quote': brief(p), 'gapMinutes': gap/60,
            'workChangePercent': delta,
            'costChangePercent': base.change(p['costBtcPerThDay'],prev['costBtcPerThDay']),
            'difficultyFairChangePercent': base.change(p['fairDifficultyBtcPerThDay'],prev['fairDifficultyBtcPerThDay']),
            'hashrateFairChangePercent': base.change(p['fairHashrateBtcPerThDay'],prev['fairHashrateBtcPerThDay']),
            'economicGate': economic_gate(p), 'confirmationWithin5m': bool(confirmation),
            'nextObserved': brief(nxt) if next_valid else None,
            'nextGapMinutes': next_gap/60 if next_valid else None,
            'nextWorkChangePercent': base.change(nxt['workThHoursFor001Btc'],p['workThHoursFor001Btc']) if next_valid else None,
            'nextEconomicGate': economic_gate(nxt) if next_valid else None,
            'nextObservationRole': 'RETROSPECTIVE_LABEL_NOT_ENTRY_FEATURE', 'canRaiseSignal': False}
        events.append(event)
    candidates = [e for e in events if e['economicGate']]
    spaced, last = [], None
    for e in candidates:
        at=base.timestamp(e['quote']['availableAt'])
        if last is None or (at-last).total_seconds()>=COOLDOWN_SECONDS:
            spaced.append(e)
            last=at
    summaries = {}
    for group in ('discovery', 'additional'):
        rs=[r for r in selected if ('discovery' if r['dateLocal'] in DISCOVERY_DATES else 'additional')==group]
        es=[e for e in events if e['group']==group]
        summaries[group]=summarize(rs,es,eligible[group])
    return {'schemaVersion': 1, 'role': 'RETROSPECTIVE_PACKAGE_QUOTE_REPETITION_DIAGNOSTIC',
        'generatedAt': datetime.now(timezone.utc).isoformat(), 'focusDates': sorted(dates),
        'discoveryDates': sorted(DISCOVERY_DATES), 'counts': dict(counts),
        'rule': {'fixedBeforeThisRun': True, 'minWorkRisePercent': MIN_WORK_RISE_PERCENT,
            'maxPreviousGapSeconds': MAX_PREVIOUS_GAP_SECONDS,
            'bothConditionalReturnsMustExceed': 100, 'bothChainMathChecksMustPass': True,
            'confirmationMaxGapSeconds': MAX_CONFIRMATION_GAP_SECONDS,
            'confirmationMinimumWorkFraction': .95, 'episodeSpacingSeconds': COOLDOWN_SECONDS},
        'groups': summaries, 'quoteCount': len(selected),
        'quoteCountsByDate': dict(sorted(Counter(r['dateLocal'] for r in selected).items())),
        'quoteCountsByRelay': dict(Counter(r['relayVersion'] for r in selected)),
        'unavailableDates': sorted(set(dates)-{r['dateLocal'] for r in selected}),
        'economicCandidates': candidates, 'spacedEconomicCandidates': spaced,
        'allWorkRiseEvents': events,
        'prospectiveValidation': False, 'canRaiseSignal': False,
        'currentProductionModelChanged': False, 'canEstimateHitRate': False,
        'automaticPurchase': False, 'verifiedNetReturnPercent': None,
        'verdict': 'NO_VERIFIED_EDGE',
        'limitations': [
            'The discovery dates were outcome-selected. Additional dates are unused retrospective data, not a live holdout.',
            'Rule is a diagnostic screen; no threshold search or production promotion is performed.',
            'Counts describe observed quotes, not independent trials or purchasable opportunities.',
            'A missing next quote or confirmation means unknown persistence, not a failed trade.',
            'Confirmed here means two recorded quotes, NOT verified delivery, price execution, fee basis or profit.',
            'Both models share inputs; consistency is not independent verification.',
            'This is not the existing frozen fair-value-rise/public-market lag hypothesis.',
        ]}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--quotes',type=Path,default=Path('/tmp/palladium-m-git-quotes.jsonl'))
    ap.add_argument('--output',type=Path,default=Path('/tmp/palladium-m-repetition-report.json'))
    ap.add_argument('--start',default='2026-09-05')
    ap.add_argument('--end',default='2026-09-25')
    args=ap.parse_args()
    if args.quotes.resolve()==args.output.resolve(): ap.error('Distinct paths required')
    start,end=(datetime.fromisoformat(s).date() for s in (args.start,args.end))
    if end<start or (end-start).days>60: ap.error('Ordered range of at most 61 days required')
    if end>=datetime.now(base.TZ).date(): ap.error('Only complete historical local dates are allowed')
    dates={(start+timedelta(days=n)).isoformat() for n in range((end-start).days+1)}
    raw=args.quotes.read_bytes()
    rows=[json.loads(line) for line in raw.splitlines() if line.strip()]
    report=analyze(rows,dates)
    if args.quotes.read_bytes()!=raw: raise RuntimeError('Source changed during analysis')
    report['inputSha256']=hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('allWorkRiseEvents',)},allow_nan=False))


if __name__=='__main__': main()
