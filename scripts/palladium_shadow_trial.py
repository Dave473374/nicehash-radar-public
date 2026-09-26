"""Bounded forward-only development trial; reuses the public sampler and archive.

No order/account calls, notifications, purchases or production signal changes.
The existing public collector remains the only scheduled collection workflow.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import median
import time
import urllib.error

from apply_math_consistency_shadow import positive, expected_blocks_from_difficulty
from enrich_scrypt_market_history import timestamp
from sample_public_market_burst import take_sample, get_json

PROTOCOL = Path('research/palladium-shadow-protocol-v1.json')
HISTORY = Path('calibration/palladium-shadow-history.jsonl')
STATE = Path('research/palladium-shadow-state.json')
REPORT = Path('research/palladium-shadow-report.json')
PUBLIC_HISTORY = Path('calibration/public-market-history.jsonl')
LATEST_ECONOMICS = Path('research/scrypt-economics-latest.json')
PACKAGES = {'Palladium S': 'PRIMARY', 'Palladium M': 'SECONDARY'}
RULES = dict(trialHours=72, intervalSeconds=60, batchSamples=31,
    minConsecutiveGapSeconds=30, maxConsecutiveGapSeconds=90,
    maxQuoteAndFxAgeSeconds=120, workRiseMinPercent=10.0,
    conditionalReturnMustExceedPercent=100.0, requireBothChainMathPass=True,
    additionalConfirmationSamples=2, confirmationWorkFraction=.95, cooldownSeconds=3600)
POLICY_FALSE = ('publicMarketLagProtocolChanged', 'currentProductionModelChanged',
                'canRaiseSignal', 'canSendNotifications', 'automaticPurchase')
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_PUBLIC_BYTES = 85 * 1024 * 1024


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def utcnow():
    return datetime.now(timezone.utc)


def load_protocol(path=PROTOCOL):
    p = json.loads(Path(path).read_bytes())
    if (p.get('schemaVersion') != 1 or p.get('id') != 'PALLADIUM_QUOTE_IMPROVEMENT_SHADOW_V1'
            or p.get('packages') != PACKAGES or p.get('rules') != RULES
            or any(p.get(k) is not False for k in POLICY_FALSE)
            or p.get('verifiedNetReturnPercent') is not None or timestamp(p.get('lockedAt')) is None):
        raise ValueError('Frozen v1 protocol changed or invalid; use a separately reviewed version')
    return p


def model_code_digest():
    names = ('palladium_shadow_trial.py', 'sample_public_market_burst.py',
             'enrich_scrypt_market_history.py', 'apply_math_consistency_shadow.py',
             'collect_public_market_history.py')
    return digest({name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() for name in names})


def fresh_state(p, start, code):
    if start.tzinfo is None or start < timestamp(p['lockedAt']):
        raise ValueError('Trial cannot start before the frozen protocol')
    return dict(schemaVersion=1, protocolHash=digest(p), modelCodeHash=code,
        startedAt=start.isoformat(), expiresAt=(start+timedelta(hours=RULES['trialHours'])).isoformat(),
        lastReceiptAt=None, firstReceiptAt=None, archiveSha256=None, suspendedHttpStatus=None,
        attempts=0, publicSamples=0, receiptGapsSeconds=[], episodes=[],
        packages={name: dict(previous=None, maxSourceAt=None, activeEpisodeId=None,
            cooldownUntil=None, validSamples=0, invalidSamples=0, duplicateOrOldSamples=0,
            seriesResets=0) for name in PACKAGES})


def validate_state(s, p, code):
    start, end = timestamp(s.get('startedAt')), timestamp(s.get('expiresAt'))
    if (s.get('schemaVersion') != 1 or s.get('protocolHash') != digest(p)
            or s.get('modelCodeHash') != code or start is None or end is None
            or start < timestamp(p['lockedAt']) or end-start != timedelta(hours=72)
            or set(s.get('packages', {})) != set(PACKAGES)
            or not isinstance(s.get('episodes'), list) or not isinstance(s.get('receiptGapsSeconds'), list)):
        raise ValueError('State/protocol/model mismatch: refuse restart or reinterpretation')
    if len(encoded(s)) > MAX_STATE_BYTES:
        raise ValueError('State size bound exceeded')


def mode(s, now):
    if s is None:
        return 'trial'
    if now >= timestamp(s['expiresAt']):
        return 'legacy'
    return 'suspended' if s['suspendedHttpStatus'] is not None else 'trial'


def observation(snapshot, name):
    """Whitelist public fields; independently reconstruct both conditional values."""
    try:
        if (not isinstance(snapshot, dict) or snapshot.get('source') != 'NICEHASH_PUBLIC_MARKET'
                or any(snapshot.get(k) is not False for k in ('credentials_used','private_api_used','admin_api_used'))):
            raise ValueError('PROVENANCE')
        e = snapshot.get('scryptEconomics') or {}
        at, source = timestamp(snapshot.get('collected_at')), timestamp(e.get('sourceQuoteAt'))
        if (e.get('status') != 'CONDITIONAL_MODEL_ONLY' or e.get('canRaiseSignal') is not False
                or e.get('currentProductionModelChanged') is not False
                or at is None or source is None or timestamp(e.get('marketObservedAt')) != at
                or not 0 <= (at-source).total_seconds() <= RULES['maxQuoteAndFxAgeSeconds']):
            raise ValueError('MISSING_STALE_OR_FUTURE_QUOTE')
        for coin in ('BTC', 'LTC', 'DOGE'):
            fx = (e.get('fx') or {}).get(coin) or {}
            age = fx.get('ageAtMarketReceiptSeconds')
            if (isinstance(age, bool) or not isinstance(age, (int,float)) or not math.isfinite(age)
                    or not 0 <= age <= RULES['maxQuoteAndFxAgeSeconds']):
                raise ValueError('STALE_FX')
        candidates = [x for x in e.get('packages', []) if isinstance(x,dict) and x.get('package') == name]
        if len(candidates) != 1:
            raise ValueError('MISSING_OR_DUPLICATE_PACKAGE')
        q = candidates[0]
        cost, h, duration = (positive(q.get(k)) for k in ('quoteCostBtc','quoteHashrateHps','quoteDurationSeconds'))
        if q.get('status') != 'CONDITIONAL_MODEL_ONLY' or None in (cost,h,duration):
            raise ValueError('INVALID_PACKAGE_WORK')
        chains = q.get('chains')
        if not isinstance(chains,list) or len(chains)!=2 or {c.get('coin') for c in chains}!={'LTC','DOGE'}:
            raise ValueError('EXPECTED_TWO_CHAINS')
        components=[]
        for c in sorted(chains,key=lambda c:c['coin']):
            d,n,t,r,fx=(positive(c.get(k)) for k in ('difficulty','networkHashrateHps',
                'targetBlockTimeSeconds','blockRewardField','coinBtc'))
            if None in (d,n,t,r,fx):
                raise ValueError('INVALID_CHAIN_INPUT')
            if t != (150 if c['coin']=='LTC' else 60):
                raise ValueError('UNEXPECTED_TARGET_TIME')
            lam=expected_blocks_from_difficulty(1e12,86400,d)
            if lam is None:raise ValueError('INVALID_DIFFICULTY')
            components.append(dict(coin=c['coin'], difficulty=d, networkHashrateHps=n,
                targetBlockTimeSeconds=t, blockRewardField=r, coinBtc=fx,
                fairDifficulty=lam*r*fx, fairHashrate=1e12/n*86400/t*r*fx,
                mathStatus=c.get('sourceMathStatus')))
        work=h/1e12*duration/3600
        rate=cost/(work/24)
        fair_d=sum(c['fairDifficulty'] for c in components)
        fair_h=sum(c['fairHashrate'] for c in components)
        ret_d,ret_h=100*fair_d/rate,100*fair_h/rate
        if not all(math.isfinite(x) and x>0 for x in (work,rate,fair_d,fair_h,ret_d,ret_h)):
            raise ValueError('NONFINITE_ECONOMICS')
        if not math.isclose(rate, float(q.get('quoteImpliedCostBtcPerThDay',0)),rel_tol=1e-8):
            raise ValueError('INCONSISTENT_QUOTE_WORK')
        if not math.isclose(ret_d,float(q.get('conditionalExpectedReturnPercent',0)),rel_tol=1e-8):
            raise ValueError('INCONSISTENT_DIFFICULTY_RETURN')
        version=e.get('relayVersion')
        if not isinstance(version,str) or not 0<len(version)<=48:
            raise ValueError('INVALID_VERSION')
        return dict(status='VALID', package=name, priority=PACKAGES[name], quoteAt=source.isoformat(),
            receivedAt=at.isoformat(), relayVersion=version, quoteCostBtc=cost, hashrateHps=h,
            durationSeconds=duration, workThHoursPerBtc=work/cost, costBtcPerThDay=rate,
            returnDifficultyPercent=ret_d, returnHashratePercent=ret_h,
            bothMathPass=q.get('sourceMathClear') is True and all(c['mathStatus']=='PASS' for c in components),
            chains=components, feeBasisVerified=False, deliveredWorkVerified=False)
    except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError) as err:
        # Never copy raw input or response bodies into a public error report.
        reason=str(err) if type(err) is ValueError and str(err).isupper() else 'INVALID_INPUT'
        return dict(status='UNAVAILABLE', package=name, reason=reason)


def same_series(a,b):
    return (a['relayVersion']==b['relayVersion'] and a['quoteCostBtc']==b['quoteCostBtc']
        and a['durationSeconds']==b['durationSeconds']
        and [(c['coin'],c['blockRewardField'],c['targetBlockTimeSeconds']) for c in a['chains']]
            == [(c['coin'],c['blockRewardField'],c['targetBlockTimeSeconds']) for c in b['chains']])


def gate(p):
    return p['bothMathPass'] and min(p['returnDifficultyPercent'],p['returnHashratePercent'])>100


def consecutive(a,b):
    return all(RULES['minConsecutiveGapSeconds'] <= (timestamp(a[k])-timestamp(b[k])).total_seconds()
        <= RULES['maxConsecutiveGapSeconds'] for k in ('receivedAt','quoteAt'))


def process_sample(state, snapshot, at, error=None):
    """Online state transition. No future data, historical backfill or real outcomes."""
    at=at.astimezone(timezone.utc)
    last=timestamp(state['lastReceiptAt'])
    if last is not None and at<=last:
        raise ValueError('Non-increasing live receipt time')
    if at < timestamp(state['startedAt']) or at>=timestamp(state['expiresAt']):
        raise ValueError('Sample is outside the trial interval')
    if last is not None:state['receiptGapsSeconds'].append((at-last).total_seconds())
    state['lastReceiptAt']=at.isoformat()
    state['firstReceiptAt']=state['firstReceiptAt'] or at.isoformat()
    state['attempts']+=1
    if snapshot is not None:state['publicSamples']+=1
    record=dict(collected_at=at.isoformat(), protocolHash=state['protocolHash'],
        modelCodeHash=state['modelCodeHash'], trialStartedAt=state['startedAt'],
        source='PUBLIC_PALLADIUM_FORWARD_SHADOW_TRIAL', sampleError=error,
        publicSnapshotHash=digest(snapshot) if snapshot is not None else None,
        packages=[], canRaiseSignal=False, canSendNotifications=False, automaticPurchase=False)
    for name in PACKAGES:
        slot=state['packages'][name]
        p=observation(snapshot,name)
        active=next((x for x in state['episodes'] if x['id']==slot['activeEpisodeId']),None)
        label='BASELINE'
        def end(reason):
            if active is not None:
                active.update(endObservedAt=at.isoformat(),endReason=reason)
                slot['activeEpisodeId']=None
        if p['status']!='VALID':
            slot['invalidSamples']+=1
            slot['previous']=None
            end('INVALID_OR_MISSING_SAMPLE')
            label='UNAVAILABLE'
        elif abs((timestamp(p['receivedAt'])-at).total_seconds())>2:
            slot['invalidSamples']+=1;slot['previous']=None
            end('RECEIPT_TIME_MISMATCH');label='RECEIPT_TIME_MISMATCH'
        elif slot['maxSourceAt'] and timestamp(p['quoteAt'])<=timestamp(slot['maxSourceAt']):
            slot['duplicateOrOldSamples']+=1;slot['previous']=None
            end('DUPLICATE_OR_OLD_SOURCE');label='DUPLICATE_OR_OLD_SOURCE'
        else:
            slot['validSamples']+=1
            slot['maxSourceAt']=p['quoteAt']
            previous=slot['previous']
            comparable=previous is not None and same_series(p,previous) and consecutive(p,previous)
            if previous is not None and not comparable:
                slot['seriesResets']+=1;end('GAP_OR_SERIES_CHANGE');active=None
                label='BASELINE_RESET'
            if active is not None:
                support=gate(p) and p['workThHoursPerBtc']>=active['entryWorkPerBtc']*.95
                if comparable and support:
                    active['supportingObservations']+=1
                    active['lastSupportedAt']=at.isoformat()
                    if active['supportingObservations']>=3 and active['confirmedAt'] is None:
                        active['confirmedAt']=at.isoformat();label='REPEAT_CONFIRMED_SHADOW_ONLY'
                    else:label='SUPPORTING_OBSERVATION'
                else:
                    end('GATE_OR_WORK_NO_LONGER_MET');active=None;label='CANDIDATE_ENDED'
            cooldown=timestamp(slot['cooldownUntil'])
            if active is None and comparable:
                rise=100*(p['workThHoursPerBtc']/previous['workThHoursPerBtc']-1)
                p['workRiseVsPreviousPercent']=rise
                if rise>=RULES['workRiseMinPercent'] and gate(p):
                    if cooldown is not None and at<cooldown:
                        label='COOLDOWN'
                    else:
                        ident=digest([state['protocolHash'],name,p['quoteAt'],at.isoformat()])
                        episode=dict(id=ident,package=name,priority=PACKAGES[name],
                            firstObservedAt=at.isoformat(),lastSupportedAt=at.isoformat(),
                            sourceQuoteAt=p['quoteAt'],baselineQuoteAt=previous['quoteAt'],
                            workRisePercent=rise,entryWorkPerBtc=p['workThHoursPerBtc'],
                            entryReturnDifficultyPercent=p['returnDifficultyPercent'],
                            entryReturnHashratePercent=p['returnHashratePercent'],
                            supportingObservations=1,confirmedAt=None,endObservedAt=None,endReason=None,
                            continuousDurationVerified=False,canRaiseSignal=False)
                        state['episodes'].append(episode);slot['activeEpisodeId']=ident
                        slot['cooldownUntil']=(at+timedelta(seconds=3600)).isoformat()
                        label='CANDIDATE_SHADOW_ONLY'
                elif label in ('BASELINE','BASELINE_RESET'):
                    label='NO_CANDIDATE'
            slot['previous']=p
        record['packages'].append(dict(observation=p,evaluation=label,activeEpisodeId=slot['activeEpisodeId']))
    return record


def make_report(state, now, batch, production):
    gaps=state['receiptGapsSeconds']
    span=(timestamp(state['lastReceiptAt'])-timestamp(state['firstReceiptAt'])).total_seconds() if state['lastReceiptAt'] else 0
    return dict(schemaVersion=1, role='PALLADIUM_FORWARD_SHADOW_TRIAL', generatedAt=now.isoformat(),
        protocolHash=state['protocolHash'],modelCodeHash=state['modelCodeHash'],
        startedAt=state['startedAt'],expiresAt=state['expiresAt'],lastReceiptAt=state['lastReceiptAt'],
        status='COMPLETED' if mode(state,now)=='legacy' else 'SUSPENDED_HTTP_POLICY' if mode(state,now)=='suspended' else 'COLLECTING',
        evidenceRole='FORWARD_SHADOW_OBSERVATIONS' if production else 'PR_SMOKE_EXCLUDED_FROM_TRIAL',
        attempts=state['attempts'],publicSamples=state['publicSamples'],
        observationSpanSeconds=span,requestedIntervalSeconds=60,
        medianReceiptGapSeconds=median(gaps) if gaps else None,
        maximumReceiptGapSeconds=max(gaps) if gaps else None,
        gapsOver90Seconds=sum(x>90 for x in gaps),
        missingMinuteSlotsLowerBound=sum(max(0,int(x//60)-1) for x in gaps),
        packages={name:dict(priority=PACKAGES[name],
            validSamples=slot['validSamples'],invalidSamples=slot['invalidSamples'],
            duplicateOrOldSamples=slot['duplicateOrOldSamples'],seriesResets=slot['seriesResets'],
            candidates=sum(e['package']==name for e in state['episodes']),
            repeatConfirmed=sum(e['package']==name and e['confirmedAt'] is not None for e in state['episodes']))
            for name,slot in state['packages'].items()},
        episodes=state['episodes'],lastBatch=batch,archiveSha256=state['archiveSha256'],
        runtime='EXISTING_GITHUB_ACTIONS_COLLECTOR_BOUNDED_DEVELOPMENT_BATCHES',
        continuousCoverageGuaranteed=False,netEvVerified=False,componentFreshnessIndependentlyVerified=False,
        canEstimateHitRate=False,canRaiseSignal=False,canSendNotifications=False,automaticPurchase=False,
        currentProductionModelChanged=False,publicMarketLagProtocolChanged=False,
        note='Confirmation is three distinct observed quotes, not proof of continuous persistence, executable delivery or profit. Inter-batch gaps reset baselines. No HIT/MISS outcomes are inferred.')


def atomic(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.shadow-tmp')
    try:
        with tmp.open('wb') as h:h.write(data);h.flush();os.fsync(h.fileno())
        tmp.replace(path)
    finally:tmp.unlink(missing_ok=True)


def load_live_state(protocol,code):
    from radar_snapshot_archive import history_exists,history_digest
    exists=history_exists(HISTORY)
    if not STATE.exists():
        if exists:raise ValueError('Archive exists without state; do not restart the trial')
        return None
    if STATE.stat().st_size>MAX_STATE_BYTES:raise ValueError('State is too large')
    s=json.loads(STATE.read_bytes());validate_state(s,protocol,code)
    if not exists or history_digest(HISTORY)!=s['archiveSha256']:
        raise ValueError('State/archive mismatch; no silent recovery or truncated history')
    return s


def persist_public(samples,now):
    """Same 30-day public history contract; never an unbounded single file."""
    old=PUBLIC_HISTORY.read_bytes() if PUBLIC_HISTORY.exists() else b''
    if len(old)>MAX_PUBLIC_BYTES:raise ValueError('Public history size bound exceeded before append')
    rows=[json.loads(x) for x in old.splitlines() if x.strip()]
    kept={}
    for row in rows+samples:
        at=timestamp(row.get('collected_at'))
        if at is None:raise ValueError('Invalid public history timestamp')
        if now-timedelta(days=30)<=at<=now:
            if at in kept and kept[at]!=row:raise ValueError('Conflicting public sample identity')
            kept[at]=row
    raw=b''.join(encoded(row)+b'\n' for _,row in sorted(kept.items()))
    if len(raw)>MAX_PUBLIC_BYTES:raise ValueError('Public history approaches file limit; stop trial rather than drop evidence')
    if (PUBLIC_HISTORY.read_bytes() if PUBLIC_HISTORY.exists() else b'')!=old:
        raise ValueError('Public history changed concurrently')
    atomic(PUBLIC_HISTORY,raw)
    if samples:
        latest=max(samples,key=lambda s:timestamp(s['collected_at']))
        atomic(LATEST_ECONOMICS,encoded(latest['scryptEconomics'])+b'\n')


def collect(p,state,code,samples,output,production,*,sampler=None,clock=utcnow,mono=time.monotonic,sleep=time.sleep):
    if type(samples) is not int or not 1<=samples<=31:raise ValueError('Bounded sample count required')
    if state is None:state=fresh_state(p,clock(),code)
    else:validate_state(state,p,code)
    if mode(state,clock())!='trial':raise ValueError('Trial expired or suspended')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    checkpoint=output/'new-shadow-samples.jsonl'
    if checkpoint.exists():raise ValueError('Refuse to overwrite a prior batch checkpoint')
    metadata={}
    if sampler is None:
        def sampler():
            if not metadata:
                info=get_json('/main/api/v2/public/buy/info/')
                registry=get_json('/main/api/v2/mining/algorithms/')
                metadata.update(info=info,registry=registry)
            return take_sample(metadata['info'],metadata['registry'])
    rows,public=[],[]
    deadline=mono()
    starts=[]
    for i in range(samples):
        sleep(max(0,deadline-mono()))
        if clock()>=timestamp(state['expiresAt']):break
        began=mono();starts.append(began)
        snap,error=None,None
        try:
            snap=sampler()
            if not isinstance(snap,dict) or snap.get('source')!='NICEHASH_PUBLIC_MARKET':
                raise ValueError('Public snapshot expected')
            if any(snap.get(k) is not False for k in ('credentials_used','private_api_used','admin_api_used')):
                raise ValueError('Public-only provenance failed')
        except urllib.error.HTTPError as exc:
            snap=None;error='HTTP_'+str(exc.code)
            if exc.code in (401,403,429):state['suspendedHttpStatus']=exc.code
        except Exception as exc:
            snap=None;error=type(exc).__name__
        at=timestamp(snap.get('collected_at')) if snap is not None else clock()
        # Never label a late return as an in-window observation after automatic expiry.
        if at is None or at>=timestamp(state['expiresAt']):break
        row=process_sample(state,snap,at,error)
        rows.append(row)
        if snap is not None:public.append(snap)
        with checkpoint.open('ab') as h:h.write(encoded(row)+b'\n');h.flush();os.fsync(h.fileno())
        if state['suspendedHttpStatus'] is not None:break
        deadline=began+60  # no catch-up burst after a slow request
    batch=dict(requested=samples,attempted=len(rows),collected=len(public),
        newCandidates=sum(x['evaluation']=='CANDIDATE_SHADOW_ONLY' for r in rows for x in r['packages']),
        newRepeatConfirmations=sum(x['evaluation']=='REPEAT_CONFIRMED_SHADOW_ONLY' for r in rows for x in r['packages']),
        complete=len(rows)==samples and all(r['sampleError'] is None for r in rows),
        startGapSeconds=[b-a for a,b in zip(starts,starts[1:])],
        runId=str(os.getenv('GITHUB_RUN_ID','local')),developmentSmoke=not production)
    atomic(output/'public-samples.jsonl',b''.join(encoded(x)+b'\n' for x in public))
    if production:
        from radar_snapshot_archive import append_history,history_digest
        if not rows:raise ValueError('No observations; trial activation not persisted')
        persist_public(public,clock())
        append_history(HISTORY,rows,expected_sha256=state['archiveSha256'])
        state['archiveSha256']=history_digest(HISTORY)
        validate_state(state,p,code)
        atomic(STATE,encoded(state)+b'\n')
    report=make_report(state,clock(),batch,production)
    atomic(output/'report.json',encoded(report)+b'\n')
    if production:atomic(REPORT,encoded(report)+b'\n')
    print(json.dumps(report,sort_keys=True))
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=('plan','collect','stage'))
    ap.add_argument('--samples',type=int,default=31)
    ap.add_argument('--smoke',action='store_true')
    ap.add_argument('--output-dir',type=Path,default=Path('/tmp/palladium-shadow-batch'))
    args=ap.parse_args()
    p=load_protocol();code=model_code_digest()
    if args.command=='stage':
        from radar_snapshot_archive import stage_history
        load_live_state(p,code);stage_history(HISTORY)
        return
    s=None if args.smoke else load_live_state(p,code)
    current=mode(s,utcnow())
    if args.command=='plan':
        print(current)
        if os.getenv('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'],'a') as h:h.write('mode='+current+'\n')
        if s is not None and current=='legacy':
            atomic(REPORT,encoded(make_report(s,utcnow(),{'automaticExpiry':True},True))+b'\n')
        return
    if not args.smoke and (os.getenv('GITHUB_REF')!='refs/heads/main'
            or os.getenv('GITHUB_EVENT_NAME') not in ('push','schedule','workflow_dispatch')):
        raise ValueError('Only main production workflow may persist this prospective trial')
    if args.smoke and args.samples>6:raise ValueError('PR smoke is capped at six samples')
    collect(p,s,code,args.samples,args.output_dir,not args.smoke)


if __name__=='__main__':main()
