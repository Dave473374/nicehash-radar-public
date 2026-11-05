"""Bounded READ-ONLY rollout preflight; never deploys, changes a schedule or sends a push.
Cloudflare credentials are used ONLY for the exact existing Worker's settings GET.
Only whitelisted booleans and public source timestamps are printed; no secret values.
"""
import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

WORKER = 'nicehash-easymining-relay'
ORIGIN = 'https://nicehash-easymining-relay.david-e5e.workers.dev'
LIMIT = 2_000_000

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def get_json(url, token=None):
    headers = {'Accept': 'application/json', 'User-Agent': 'BUY-Radar-Core-ReadOnly-Preflight/1', 'Cache-Control': 'no-cache'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=25) as response:
            raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            return None, 'RESPONSE_TOO_LARGE'
        result = json.loads(raw)
        if not isinstance(result, dict):
            return None, 'INVALID_JSON_OBJECT'
        return result, None
    except urllib.error.HTTPError as exc:
        return None, 'HTTP_' + str(exc.code)
    except Exception:
        return None, 'TRANSPORT_OR_JSON_ERROR'

def credential_presence(environ):
    token = environ.get('CLOUDFLARE_API_TOKEN') or environ.get('CF_API_TOKEN') or ''
    account = environ.get('CLOUDFLARE_ACCOUNT_ID') or environ.get('CF_ACCOUNT_ID') or ''
    missing = []
    if not token: missing.append('CLOUDFLARE_API_TOKEN')
    if not account: missing.append('CLOUDFLARE_ACCOUNT_ID')
    if missing: return None, None, {'status':'CREDENTIALS_MISSING', 'missing':missing}
    if not re.fullmatch(r'[0-9a-fA-F]{32}', account):
        return None, None, {'status':'ACCOUNT_ID_FORMAT_INVALID'}
    return token, account, {'status':'CONFIGURED_NOT_YET_VERIFIED'}

def run(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {'schema':'BUY_RADAR_CORE_PREFLIGHT_V1', 'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'code_sha':os.getenv('GITHUB_SHA'), 'worker':WORKER, 'read_only':True,
        'deployment_performed':False, 'cron_changed':False, 'notification_sent':False,
        'automatic_purchase':False, 'public':{}}
    for endpoint in ('health','buy-feed'):
        data, error = get_json(ORIGIN + '/' + endpoint)
        if error:
            report['public'][endpoint] = {'transport':error}
            continue
        public = {key:data[key] for key in ('status','ok','relay_version','source_revision','schema_version','checked_at','served_at') if key in data}
        at = data.get('checked_at')
        try:
            parsed = dt.datetime.fromisoformat(at.replace('Z','+00:00'))
            if parsed.tzinfo is None: raise ValueError('timezone missing')
            public['source_age_seconds'] = round((dt.datetime.now(dt.timezone.utc)-parsed).total_seconds(),3)
        except (ValueError, AttributeError, TypeError):
            public['source_age_seconds'] = None
        public['packages_count'] = len(data.get('packages',[])) if isinstance(data.get('packages'),list) else None
        public['collector_state'] = (data.get('collector') or {}).get('state')
        public['decision_state'] = (data.get('production_health') or {}).get('state')
        report['public'][endpoint] = public
        if endpoint == 'buy-feed':
            (output_dir/'live-feed.json').write_text(json.dumps(data))
    token, account, credentials = credential_presence(os.environ)
    report['cloudflare'] = credentials
    if token:
        data,error = get_json(f'https://api.cloudflare.com/client/v4/accounts/{account}/workers/scripts/{WORKER}/settings',token)
        if error:
            report['cloudflare']={'status':'ACCESS_NOT_VERIFIED','error':error}
        elif data.get('success') is not True or not isinstance(data.get('result'),dict):
            report['cloudflare']={'status':'SETTINGS_READ_REJECTED'}
        else:
            settings=data['result']; bindings=settings.get('bindings',[])
            if not isinstance(bindings,list): bindings=[]
            def bound(name,kind):
                return any(isinstance(b,dict) and b.get('name')==name and b.get('type')==kind for b in bindings)
            report['cloudflare']={'status':'EXISTING_WORKER_SETTINGS_READ_OK',
                'radar_history_bound':bound('RADAR_HISTORY','kv_namespace'),
                'radar_core_bound':bound('RADAR_CORE','durable_object_namespace'),
                'notification_service_bound':bound('RADAR_NOTIFY','service'),
                'deployment_permission_tested':False}
    (output_dir/'preflight.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return report

if __name__=='__main__':
    run(Path(os.getenv('RADAR_PREFLIGHT_OUT','/tmp/radar-core-preflight')))
