"""One-off minute-cadence diagnostic using EXISTING public collection code.

No cron, account access, credentials, signal changes or canonical-history writes.
Only whitelisted aggregate public snapshots are exported as research artifacts.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import median
import time
import urllib.error
import urllib.request

from collect_public_market_history import get_json, snapshot, NoRedirect
from enrich_scrypt_market_history import build_economics

RELAY='https://nicehash-easymining-relay.david-e5e.workers.dev/buy-feed'
CURRENT='/main/api/v2/public/stats/global/current/'
MAX_SAMPLES=31
MIN_INTERVAL=60


def relay_feed():
    req=urllib.request.Request(RELAY,method='GET',headers={
        'User-Agent':'NiceHash-Radar-Public-Cadence-Diagnostic/1.0','Accept':'application/json'})
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=25) as response:
        if response.status!=200 or response.geturl()!=RELAY:
            raise RuntimeError('Unexpected public relay response')
        raw=response.read(4_000_001)
        if len(raw)>4_000_000:raise ValueError('Public response too large')
        data=json.loads(raw)
        if not isinstance(data,dict):raise ValueError('Public object required')
        return data


def take_sample(buy_info,registry):
    # Quote first, market second. Both reference times are retained.
    feed=relay_feed()
    market_payload=get_json(CURRENT)
    now=datetime.now(timezone.utc)
    market=snapshot(buy_info,market_payload,registry,now.isoformat())
    market['scryptEconomics']=build_economics(feed,market,now,[market])
    return market


def run_burst(sample_fn,samples=6,interval=60,*,monotonic=time.monotonic,sleep=time.sleep):
    if isinstance(samples,bool) or not isinstance(samples,int) or not 1<=samples<=MAX_SAMPLES:
        raise ValueError('Bounded integer sample count required')
    if isinstance(interval,bool) or not isinstance(interval,(float,int)) or not math.isfinite(interval) or not MIN_INTERVAL<=interval<=300:
        raise ValueError('Interval must be 60-300 seconds')
    if (samples-1)*interval>1800:raise ValueError('At most 30 minutes per diagnostic')
    results=[]
    deadline=monotonic()
    first_start=None
    for index in range(samples):
        sleep(max(0,deadline-monotonic()))
        started=monotonic()
        if first_start is None:first_start=started
        row={'index':index,'elapsedStartSeconds':started-first_start,'status':'UNAVAILABLE'}
        stop=False
        try:
            snap=sample_fn()
            if not isinstance(snap,dict) or snap.get('source')!='NICEHASH_PUBLIC_MARKET':
                raise ValueError('Public snapshot required')
            if any(snap.get(k) is not False for k in ('credentials_used','private_api_used','admin_api_used')):
                raise ValueError('Invalid public provenance')
            row.update(status='COLLECTED',snapshot=snap)
        except urllib.error.HTTPError as e:
            row.update(errorType='HTTPError',httpStatus=e.code)
            # Never keep polling after authorization/forbidden/rate-limit response.
            stop=e.code in (401,403,429)
        except Exception as e:
            row['errorType']=type(e).__name__
        row['durationSeconds']=monotonic()-started
        results.append(row)
        if stop:
            row['stoppedForHttpPolicy']=True
            break
        # Do not bunch requests to catch up after a slow iteration.
        deadline=max(deadline+interval,started+interval)
    return results


def summarize(rows,requested,interval):
    starts=[r['elapsedStartSeconds'] for r in rows]
    gaps=[b-a for a,b in zip(starts,starts[1:])]
    good=[r['snapshot'] for r in rows if r['status']=='COLLECTED']
    quote_times=[(s.get('scryptEconomics') or {}).get('sourceQuoteAt') for s in good]
    quote_times=[x for x in quote_times if x]
    economic=[s for s in good if (s.get('scryptEconomics') or {}).get('status')=='CONDITIONAL_MODEL_ONLY']
    ages=[s['scryptEconomics']['quoteAgeAtMarketReceiptSeconds'] for s in economic]
    return {'schemaVersion':1,'role':'BOUNDED_PUBLIC_CADENCE_DIAGNOSTIC',
        'generatedAt':datetime.now(timezone.utc).isoformat(),
        'requestedSamples':requested,'attemptedSamples':len(rows),'collectedSamples':len(good),
        'usableEconomicsSamples':len(economic),'uniqueSourceQuoteTimestamps':len(set(quote_times)),
        'repeatedSourceTimestamps':len(quote_times)-len(set(quote_times)),
        'requestedIntervalSeconds':interval,
        'medianObservedStartGapSeconds':median(gaps) if gaps else None,
        'maxObservedStartGapSeconds':max(gaps) if gaps else None,
        'maxQuoteAgeAtMarketReceiptSeconds':max(ages) if ages else None,
        'observedSpanSeconds':starts[-1]-starts[0] if starts else 0,
        'sourceTimingIsIndependentlyVerified':False,
        'continuous24hCoverageVerified':False,'recurringDeploymentEnabled':False,
        'canonicalHistoryModified':False,'currentProductionModelChanged':False,
        'canRaiseSignal':False,'automaticPurchase':False,'adminApiUsed':False,'privateApiUsed':False,
        'limitations':['A short burst verifies only measured cadence during this bounded run.',
            'New source quote timestamps do not independently prove all component inputs refreshed.',
            'No permanent one-minute scheduler is installed by this diagnostic.',
            'Output uses existing public parsers and conditional economics, not verified net EV.']}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--samples',type=int,default=6)
    ap.add_argument('--interval',type=float,default=60)
    ap.add_argument('--output-dir',type=Path,default=Path('/tmp/public-market-burst'))
    args=ap.parse_args()
    out=args.output_dir.resolve()
    # Do not write into the source repository's production/calibration folders.
    if out==Path.cwd().resolve() or Path.cwd().resolve() in out.parents:
        ap.error('Diagnostic outputs must be outside the repository')
    # Validate arguments before any public request.
    if not 1<=args.samples<=MAX_SAMPLES or not MIN_INTERVAL<=args.interval<=300 or (args.samples-1)*args.interval>1800:
        ap.error('Use 1-31 samples, 60-300 seconds, at most 30 minutes')
    buy_info=get_json('/main/api/v2/public/buy/info/')
    registry=get_json('/main/api/v2/mining/algorithms/')
    rows=run_burst(lambda:take_sample(buy_info,registry),args.samples,args.interval)
    report=summarize(rows,args.samples,args.interval)
    out.mkdir(parents=True,exist_ok=True)
    (out/'samples.jsonl').write_text(''.join(json.dumps(r,separators=(',',':'),allow_nan=False)+'\n' for r in rows))
    (out/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps(report,allow_nan=False))
    if report['collectedSamples']!=args.samples:raise SystemExit('Burst incomplete; inspect report, no success claim')


if __name__=='__main__':main()
