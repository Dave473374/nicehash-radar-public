"""Read-only evaluator for the existing frozen Palladium S/M shadow trial.

No HTTP, collection, account data, alerts or experiment mutation. Exact public
sources and unchanged trial code must reproduce every saved attempt and state.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import median
import subprocess
from zoneinfo import ZoneInfo

import palladium_shadow_trial as trial
from radar_snapshot_archive import open_history, manifest_path, archive_dir, verify_history

PLAN = Path('research/palladium-shadow-evaluation-plan-v1.json')
OUTPUT = Path('research/palladium-shadow-evaluation.json')
MARKDOWN = Path('research/palladium-shadow-evaluation.md')
EXPECTED_PROTOCOL = '1fcd3fcd9c45a45a86b781e7423b46a1702cc0d9c1a951beae9947af1993f3a3'
TZ = ZoneInfo('Europe/Ljubljana')
MAX_ROWS = 20000
MAX_INPUT_BYTES = 128 * 1024 * 1024
REJECTED_LABELS = frozenset(('UNAVAILABLE', 'RECEIPT_TIME_MISMATCH', 'DUPLICATE_OR_OLD_SOURCE'))
SOURCE_FILES = (trial.PROTOCOL, trial.STATE, trial.REPORT, trial.PUBLIC_HISTORY, PLAN)


class EvidenceError(ValueError):
    """Incomplete or conflicting evidence is not a zero-candidate result."""


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def aware(value):
    at = trial.timestamp(value)
    require(at is not None, 'An aware timestamp is required')
    return at


def strict_json(raw):
    def bad(value):
        raise EvidenceError('Non-finite JSON value')
    try:
        return json.loads(raw, parse_constant=bad)
    except (ValueError, UnicodeError) as exc:
        raise EvidenceError('Invalid finite UTF-8 JSON evidence') from exc


def read_bytes(path):
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), 'Missing or nonregular evidence file')
    require(path.stat().st_size <= MAX_INPUT_BYTES, 'Evidence file exceeds safety bound')
    return path.read_bytes()


def load_plan(path=PLAN):
    p = strict_json(read_bytes(path))
    require(type(p.get('schemaVersion')) is int and p['schemaVersion'] == 1
        and p.get('id') == 'PALLADIUM_SHADOW_EVALUATION_V1'
        and p.get('expectedProtocolHash') == EXPECTED_PROTOCOL
        and p.get('fixedMetricUnits', {}).get('slots') == 60
        and all(p.get(k) is False for k in ('changesTrial', 'canRaiseSignal',
            'canSendNotifications', 'automaticPurchase', 'canEstimateHitRate'))
        and p.get('verifiedProfit') is None, 'Invalid evaluation plan')
    return p


def describe(values):
    values = list(values)
    require(all(isinstance(v, (int, float)) and not isinstance(v, bool)
        and math.isfinite(v) for v in values), 'Invalid metric values')
    return dict(n=len(values), min=min(values) if values else None,
        median=median(values) if values else None, max=max(values) if values else None)


def percent(n, d):
    return 100 * n / d if d else None


def source_reasons(snapshot, name, observation, sample_error):
    """Preserve the distinction between omitted packages and actual availability."""
    if sample_error is not None:
        return ['ACQUISITION_ERROR:' + str(sample_error)]
    if snapshot is None:
        return ['NO_LINKED_PUBLIC_SAMPLE']
    e = snapshot.get('scryptEconomics') or {}
    reasons = list(e.get('reasons') or [])
    packages = e.get('packages') or []
    for p in packages:
        if isinstance(p, dict) and p.get('package') == name:
            reasons.extend(p.get('reasons') or [])
    if not reasons:
        reasons = [observation.get('reason', 'INVALID_OR_UNUSABLE_OBSERVATION')]
    require(all(isinstance(r, str) and len(r) < 200 for r in reasons), 'Invalid source reason')
    return sorted(set(reasons))


def replay(rows, public_rows, state, protocol):
    require(trial.digest(protocol) == EXPECTED_PROTOCOL, 'Wrong trial protocol')
    trial.validate_state(state, protocol, trial.model_code_digest())
    require(isinstance(rows, list) and 0 < len(rows) <= MAX_ROWS, 'Missing or oversized attempt history')
    require(state['attempts'] == len(rows), 'State attempt count differs from history')
    wanted = {r.get('publicSnapshotHash') for r in rows if r.get('publicSnapshotHash') is not None}
    sources = {}
    for p in public_rows:
        require(isinstance(p, dict), 'Public evidence row must be an object')
        key = trial.digest(p)
        if key in wanted:
            sources[key] = p
    require(wanted <= set(sources), 'Exact public source missing; do not substitute a newer quote')
    reconstructed = trial.fresh_state(protocol, aware(state['startedAt']), state['modelCodeHash'])
    for row in rows:
        require(isinstance(row, dict), 'Attempt must be an object')
        require(row.get('source') == 'PUBLIC_PALLADIUM_FORWARD_SHADOW_TRIAL', 'Nonproduction/smoke evidence excluded')
        require(row.get('protocolHash') == state['protocolHash']
            and row.get('modelCodeHash') == state['modelCodeHash']
            and row.get('trialStartedAt') == state['startedAt'], 'Mixed experiment identity')
        p = sources.get(row.get('publicSnapshotHash'))
        err = row.get('sampleError')
        require(err is None or isinstance(err, str) and len(err) < 100, 'Invalid acquisition error')
        if err in ('HTTP_401', 'HTTP_403', 'HTTP_429'):
            reconstructed['suspendedHttpStatus'] = int(err.split('_')[1])
        expected = trial.process_sample(reconstructed, p, aware(row.get('collected_at')), err)
        require(trial.encoded(expected) == trial.encoded(row), 'Archived evaluation differs from frozen-code replay')
    reconstructed['archiveSha256'] = state['archiveSha256']
    require(trial.encoded(reconstructed) == trial.encoded(state), 'Stored state differs from exact replay')
    return sources


def collector_report_matches(report, state):
    require(report.get('evidenceRole') == 'FORWARD_SHADOW_OBSERVATIONS', 'PR smoke report is not trial evidence')
    for key in ('protocolHash', 'modelCodeHash', 'archiveSha256', 'startedAt',
                'expiresAt', 'attempts', 'publicSamples', 'episodes', 'lastReceiptAt'):
        require(report.get(key) == state.get(key), 'Collector report/state mismatch: ' + key)
    for name, slot in state['packages'].items():
        rp = report.get('packages', {}).get(name, {})
        for key in ('validSamples', 'invalidSamples', 'duplicateOrOldSamples', 'seriesResets'):
            require(rp.get(key) == slot[key], 'Collector package counts are inconsistent')
        require(rp.get('candidates') == sum(e['package'] == name for e in state['episodes']), 'Collector candidate mismatch')
        require(rp.get('repeatConfirmed') == sum(e['package'] == name and e['confirmedAt'] is not None
            for e in state['episodes']), 'Collector confirmation mismatch')
    require(all(report.get(k) is False for k in ('canRaiseSignal', 'canSendNotifications',
        'automaticPurchase', 'currentProductionModelChanged')), 'Unexpected production promotion')


def evaluate(rows, public_rows, state, collector_report, protocol, plan, now):
    """Pure, deterministic calculation at an explicit cutoff; inputs are not mutated."""
    now = aware(now.isoformat())
    require(plan['expectedProtocolHash'] == EXPECTED_PROTOCOL, 'Wrong evaluation plan')
    start, end = aware(state['startedAt']), aware(state['expiresAt'])
    require(now >= start, 'Cutoff precedes trial activation')
    sources = replay(rows, public_rows, state, protocol)
    collector_report_matches(collector_report, state)
    times = [aware(r['collected_at']) for r in rows]
    require(times[-1] <= now, 'Cutoff precedes persisted observations')
    report_at = aware(collector_report['generatedAt'])
    require(times[-1] <= report_at <= now, 'Collector report timestamp inconsistent with cutoff')
    cutoff = min(now, end)
    closed_slots = int((cutoff - start).total_seconds() // 60)
    def slot(at):
        k = int((at-start).total_seconds() // 60)
        return k if 0 <= k < closed_slots else None
    occupied = {slot(t) for t in times} - {None}
    gaps = [(b-a).total_seconds() for a, b in zip(times, times[1:])]
    batch = collector_report.get('lastBatch') or {}
    n = batch.get('attempted')
    batching = []
    if type(n) is int and 0 < n <= len(rows):
        batching = [(report_at-t).total_seconds() for t in times[-n:]]
    packages = {}
    for name, priority in trial.PACKAGES.items():
        reasons, labels, dates, math_status = Counter(), Counter(), {}, Counter()
        valid, qualified, streaks, current = [], [], [], []
        comparisons = 0
        def flush():
            if current:
                streaks.append(dict(observations=len(current), firstAt=current[0]['receivedAt'],
                    lastAt=current[-1]['receivedAt'],
                    endpointSpanSeconds=(aware(current[-1]['receivedAt'])-aware(current[0]['receivedAt'])).total_seconds()))
                current.clear()
        for row in rows:
            entries = [e for e in row['packages'] if e['observation']['package'] == name]
            require(len(entries) == 1, 'Missing or duplicate per-package evaluation')
            entry = entries[0]; p = entry['observation']; label = entry['evaluation']
            at = aware(row['collected_at']); day = at.astimezone(TZ).date().isoformat()
            daily = dates.setdefault(day, dict(attempts=0, usable=0, unusable=0))
            daily['attempts'] += 1; labels[label] += 1
            usable = p['status'] == 'VALID' and label not in REJECTED_LABELS
            if usable:
                daily['usable'] += 1; valid.append(p)
                for c in p['chains']:
                    math_status[c['coin'] + ':' + str(c['mathStatus'])] += 1
                if trial.gate(p):
                    qualified.append(p)
                if current and (not trial.same_series(p, current[-1]) or not trial.consecutive(p, current[-1])):
                    flush()
                if current:
                    comparisons += 1
                current.append(p)
            else:
                daily['unusable'] += 1; flush()
                if label == 'DUPLICATE_OR_OLD_SOURCE':
                    reasons[label] += 1
                else:
                    for reason in source_reasons(sources.get(row.get('publicSnapshotHash')),
                            name, p, row.get('sampleError')):
                        reasons[reason] += 1
        flush()
        usable_slots = {slot(aware(p['receivedAt'])) for p in valid} - {None}
        episodes = []
        for event in state['episodes']:
            if event['package'] != name:
                continue
            e = dict(event)
            first, supported = aware(e['firstObservedAt']), aware(e['lastSupportedAt'])
            e['observedSupportSpanSeconds'] = (supported-first).total_seconds()
            e['confirmationAfterSeconds'] = (aware(e['confirmedAt'])-first).total_seconds() if e['confirmedAt'] else None
            e['rightCensoredAtLastSupportedObservation'] = e['endObservedAt'] is None
            e['recordedEndClass'] = ('OBSERVED_GATE_OR_WORK_FAILURE' if e['endReason'] == 'GATE_OR_WORK_NO_LONGER_MET'
                else 'OBSERVATION_INTERRUPTED' if e['endReason'] else 'NO_END_OBSERVATION')
            e['changeTimeBracketSeconds'] = (aware(e['endObservedAt'])-supported).total_seconds() if e['endObservedAt'] else None
            e['continuousConditionDurationSeconds'] = None
            e['miningOutcome'] = None
            episodes.append(e)
        packages[name] = dict(priority=priority, attempts=len(rows), usableObservations=len(valid),
            unusableObservations=len(rows)-len(valid), usableShareOfAttemptsPercent=percent(len(valid),len(rows)),
            occupiedClosedMinuteSlotsWithUsableQuote=len(usable_slots),
            usableClosedSlotSharePercent=percent(len(usable_slots),closed_slots),
            unusableReasonCounts=dict(sorted(reasons.items())), reasonCountsMayOverlap=True,
            evaluationLabelCounts=dict(sorted(labels.items())), mathStatusCounts=dict(sorted(math_status.items())),
            bothModelReturnsAndMathGateObservations=len(qualified), eligibleConsecutiveComparisons=comparisons,
            distinctUsableQuoteTimestamps=len({p['quoteAt'] for p in valid}),
            streakCount=len(streaks), longestUsableStreak=max(streaks,key=lambda s:s['observations']) if streaks else None,
            usableStreakObservationCounts=describe(s['observations'] for s in streaks),
            candidateCount=len(episodes), repeatConfirmedCount=sum(e['confirmedAt'] is not None for e in episodes),
            unconfirmedCandidateCount=sum(e['confirmedAt'] is None for e in episodes),
            rightCensoredCandidateCount=sum(e['endObservedAt'] is None for e in episodes),
            confirmedDistinctLocalDates=sorted({aware(e['confirmedAt']).astimezone(TZ).date().isoformat()
                for e in episodes if e['confirmedAt']}),
            episodes=episodes, byLocalDate=dates,
            confirmedUnavailableFalseObservations=None, exactAvailableMinutes=None,
            availabilityEvidence='USABLE_QUOTE_PROXY_ONLY_RAW_AVAILABLE_FLAG_NOT_ARCHIVED',
            realizedProfit=None, hitRate=None)
    timer_elapsed = now >= end
    return dict(schemaVersion=1, role='PALLADIUM_SHADOW_EVALUATION_READ_ONLY', generatedAt=now.isoformat(),
        evaluationPlanHash=trial.digest(plan), protocolHash=state['protocolHash'], modelCodeHash=state['modelCodeHash'],
        archiveSha256=state['archiveSha256'], startedAt=start.isoformat(), expiresAt=end.isoformat(),
        status='TRIAL_TIME_ELAPSED_REVIEW_RECORDED_EVIDENCE' if timer_elapsed else 'INTERIM_TRIAL_RUNNING',
        trialClockElapsed=timer_elapsed, collectorReportedStatus=collector_report.get('status'),
        actualCollectorStopIndependentlyVerified=False,
        integrity=dict(exactFrozenReplay=True, archiveAttemptsMatchState=True, exactPublicSourceHashesMatched=True,
            collectorReportMatchesState=True),
        coverage=dict(persistedAttempts=len(rows), publicResponses=state['publicSamples'],
            firstPersistedReceiptAt=times[0].isoformat(), lastPersistedReceiptAt=times[-1].isoformat(),
            closedMinuteSlotsElapsed=closed_slots, occupiedClosedMinuteSlots=len(occupied),
            closedMinuteSlotsWithNoPersistedAttempt=closed_slots-len(occupied),
            occupiedClosedSlotSharePercent=percent(len(occupied),closed_slots),
            receiptGapsSeconds=describe(gaps), gapsOver90Seconds=sum(g>90 for g in gaps),
            initialUnobservedSeconds=(times[0]-start).total_seconds(),
            unobservedTailSeconds=max(0,(cutoff-times[-1]).total_seconds()),
            latestPersistedReceiptAgeSeconds=(now-times[-1]).total_seconds(),
            inFlightUnpublishedSamplesKnown=False,
            note='Only records in this checkout count. An unobserved tail can be an in-progress/unpublished batch; it is not a proven outage or unavailable package. Fixed slot occupancy is not continuous uptime.'),
        publication=dict(collectorBatchReportGeneratedAt=report_at.isoformat(),
            lastBatchObservationToReportGenerationSeconds=describe(batching),
            trueGitPublicationLatencySeconds=None, phoneDeliveryLatencySeconds=None,
            note='Report-generation delay is a batching measurement only; Git commit/publication, phone delivery and execution are not measured.'),
        packages=packages, canRaiseSignal=False, canSendNotifications=False, automaticPurchase=False,
        currentProductionModelChanged=False, canEstimateHitRate=False, verifiedNetReturnPercent=None,
        verdict='NO_VERIFIED_EDGE',
        limitations=[
            'Evaluation metrics were registered after the trial began; this is not pre-trial preregistration.',
            'A missing/duplicate package reason does not prove available:false or explain upstream unavailability.',
            'A VALID input is usable for analysis, not a mathematically PASS or profitable quote.',
            'Unobserved time, invalid replies, duplicates and interrupted episodes are not losses or MISS outcomes.',
            'Three supporting observations do not prove continuous persistence or executed work.',
            'Zero candidates, even with good sampling, does not prove no opportunity exists. No automatic threshold tuning or promotion.',
            'S and M observations and repeated episodes are not independent mining trials.',
        ])


def render(report):
    c = report['coverage']
    lines = ['# Palladium S/M — pregled shadow preizkusa', '',
        'Stanje: **' + report['status'] + '**. Vsi časi v tabeli so v območju Europe/Ljubljana.', '',
        '- Zadnji shranjeni vzorec: ' + aware(c['lastPersistedReceiptAt']).astimezone(TZ).isoformat(),
        '- Shranjeni poskusi: ' + str(c['persistedAttempts']),
        '- Večje vrzeli med shranjenimi poskusi (>90 s): ' + str(c['gapsOver90Seconds']),
        '- Čas brez poznejšega objavljenega vzorca do preseka: ' + str(round(c['unobservedTailSeconds'],1)) + ' s; serija je lahko še v teku.', '',
        '| Paket | Uporabni vzorci | Neuporabni | Kandidati | Potrjeni s 3 opažanji |',
        '|---|---:|---:|---:|---:|']
    for name,p in report['packages'].items():
        lines.append('| '+name+' | '+str(p['usableObservations'])+' | '+str(p['unusableObservations'])+' | '+str(p['candidateCount'])+' | '+str(p['repeatConfirmedCount'])+' |')
    lines += ['', '**Pomembno:** neuporaben ali izpuščen paket ni samodejno potrjen `available:false`.',
        'Točnega števila minut razpoložljivosti iz teh podatkov ne določamo.',
        'Veljaven vzorec ni isto kot MATH PASS; podrobna porazdelitev je v JSON poročilu.', '',
        'Arhiv, njegovi izvorni javni vzorci, zamrznjena obdelava in končno stanje so medsebojno preverjeni.',
        'Pravila preizkusa, BUY signali in obvestila niso spremenjeni. Zadetki, izgube in realizirani donos niso izmerjeni.',
        'Nič kandidatov ni dokaz, da priložnosti ne obstajajo. To poročilo ne daje nakupnega priporočila.', '']
    return '\n'.join(lines)


def input_fingerprints():
    files = list(SOURCE_FILES)
    mp = manifest_path(trial.HISTORY)
    if mp.exists():
        files.append(mp)
        manifest = strict_json(read_bytes(mp))
        # Actual archive reader validates traversal, sizes and hashes before use.
        files.extend(archive_dir(trial.HISTORY) / c['file'] for c in manifest['chunks'])
    else:
        files.append(trial.HISTORY)
    files.extend(Path(trial.__file__).parent/name for name in (
        'palladium_shadow_trial.py','sample_public_market_burst.py','enrich_scrypt_market_history.py',
        'apply_math_consistency_shadow.py','collect_public_market_history.py','radar_snapshot_archive.py'))
    return {str(p):hashlib.sha256(read_bytes(p)).hexdigest() for p in sorted(set(files))}


def check_outputs(paths, protected):
    resolved = [Path(p).resolve() for p in paths]
    require(len(set(resolved)) == len(resolved), 'Output paths must differ')
    forbidden = {Path(p).resolve() for p in protected}
    roots = [Path(p).resolve() for p in ('scripts','tests','calibration')]
    for p in resolved:
        require(p not in forbidden and not any(r == p or r in p.parents for r in roots),
            'Evaluator output would overwrite protected input or source code')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=OUTPUT)
    ap.add_argument('--markdown', type=Path, default=MARKDOWN)
    ap.add_argument('--now', help='Aware cutoff; default current UTC. No future rows are truncated silently.')
    args = ap.parse_args()
    now = aware(args.now) if args.now else datetime.now(timezone.utc)
    # Verify the archive before inspecting descriptor paths for provenance.
    verified = verify_history(trial.HISTORY)
    before = input_fingerprints()
    check_outputs((args.output,args.markdown),before)
    protocol = trial.load_protocol(); plan = load_plan()
    state = strict_json(read_bytes(trial.STATE)); collector = strict_json(read_bytes(trial.REPORT))
    require(verified['logicalSha256'] == state['archiveSha256'], 'Archive digest differs from state')
    with open_history(trial.HISTORY,'rb') as handle:
        rows = [strict_json(line) for line in handle if line.strip()]
    def public_rows():
        with trial.PUBLIC_HISTORY.open('rb') as handle:
            for line in handle:
                if line.strip():yield strict_json(line)
    report = evaluate(rows,public_rows(),state,collector,protocol,plan,now)
    require(before == input_fingerprints(), 'Source changed during evaluation')
    report['integrity']['inputsUnchanged'] = True
    report['inputSha256'] = before
    report['evaluatorCodeSha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    try:
        report['sourceCommit'] = subprocess.check_output(['git','rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError):
        report['sourceCommit'] = None
    for path,data in ((args.output,trial.encoded(report)+b'\n'),(args.markdown,render(report).encode())):
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_name(path.name+'.evaluation-tmp')
        temporary.write_bytes(data);temporary.replace(path)
    print(json.dumps({k:report[k] for k in ('status','sourceCommit','integrity','coverage','publication','verdict')},sort_keys=True))
    for name,p in report['packages'].items():
        print(name,json.dumps({k:p[k] for k in ('usableObservations','unusableObservations','unusableReasonCounts',
            'eligibleConsecutiveComparisons','longestUsableStreak','candidateCount','repeatConfirmedCount')},sort_keys=True))


if __name__ == '__main__':
    main()
