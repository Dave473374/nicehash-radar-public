"""Collect aggregate public stats plus explicit display-unit metadata, never orders."""
import json
import math
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = 'https://api2.nicehash.com'
OUTPUT = Path('calibration/public-market-history.jsonl')
RETAIN_DAYS = 30
RELEVANT = {'SCRYPT', 'SHA256ASICBOOST', 'SHA256ASICBOOST_USDT', 'EQUIHASH', 'KHEAVYHASH'}
PUBLIC_PATHS = frozenset(('/main/api/v2/public/buy/info/',
    '/main/api/v2/public/stats/global/current/', '/main/api/v2/mining/algorithms/'))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('Public collector redirect refused')


def get_json(path):
    if path not in PUBLIC_PATHS:
        raise ValueError('Public path is not allowlisted')
    request = urllib.request.Request(BASE + path, method='GET', headers={
        'User-Agent': 'NiceHash-Radar-Public-Market-History/2.0', 'Accept': 'application/json'})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
        if response.status != 200 or response.geturl() != BASE + path:
            raise RuntimeError('Unexpected public response')
        data = response.read(4_000_001)
        if len(data) > 4_000_000:
            raise ValueError('Public response too large')
        payload = json.loads(data.decode('utf-8'))
        if not isinstance(payload, dict):
            raise ValueError('Public object required')
        return payload


def positive(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def iso_to_dt(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except ValueError:
        return None


def unit_contract(settings, registry, observed_at):
    """Registry factors describe displayed units; they do NOT prove raw p's denomination."""
    name = str(settings.get('name') or '').upper()
    currency = 'USDT' if name.endswith('_USDT') else 'BTC'
    result = {'status': 'UNAVAILABLE', 'source': 'PUBLIC_MINING_ALGORITHMS',
        'observedAt': observed_at, 'currencyMarket': None,
        'rawPriceDenominationVerified': False, 'executableMarketPriceVerified': False,
        'nativePerRawPriceUnit': None, 'priceScaleMeaning': 'FORMATTING_PRECISION_NOT_RAW_CONVERSION'}
    if not isinstance(registry, dict) or not registry:
        return result
    markets = registry.get('enabledMarkets')
    markets = markets.split(',') if isinstance(markets, str) else markets if isinstance(markets, list) else []
    markets = {str(x).strip().upper() for x in markets}
    mf, pf = positive(registry.get('marketFactor')), positive(registry.get('priceFactor'))
    speed_unit, price_unit = registry.get('displayMarketFactor'), registry.get('displayPriceFactor')
    if str(registry.get('algorithm') or '').upper() != name or markets != {currency}:
        result['status'] = 'CONFLICTING_CURRENCY_OR_ALGORITHM'
        return result
    if mf is None or pf is None or not isinstance(speed_unit, str) or not speed_unit or not isinstance(price_unit, str) or not price_unit:
        result['status'] = 'INVALID_REGISTRY'
        return result
    if speed_unit != settings.get('speedUnit'):
        result['status'] = 'CONFLICTING_SPEED_UNIT'
        return result
    result.update(status='DISPLAY_UNITS_CONFIRMED_RAW_PRICE_UNVERIFIED', currencyMarket=currency,
        marketFactor=mf, priceFactor=pf, speedDisplayUnit=speed_unit, priceDisplayUnit=price_unit,
        priceToSpeedDisplayFactor=pf / mf,
        differentPriceAndSpeedUnits=pf != mf,
        priceScale=registry.get('priceScale') if isinstance(registry.get('priceScale'), int) and not isinstance(registry.get('priceScale'), bool) else None)
    return result


def snapshot(buy_info, current, registry, at):
    settings_by_id = {}
    for item in buy_info.get('miningAlgorithms', []):
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').upper()
        if name not in RELEVANT:
            continue
        try:
            algo_id = int(item['algo'])
        except (KeyError, TypeError, ValueError):
            continue
        settings_by_id[algo_id] = {'name': name, 'algoId': algo_id,
            'speedUnit': item.get('speed_text'), 'multi': item.get('multi'), 'priceMulti': item.get('price_multi')}
    registry_by_name = {}
    for item in (registry or {}).get('miningAlgorithms', []):
        if isinstance(item, dict):
            name = str(item.get('algorithm') or '').upper()
            # A conflicting duplicate is deliberately not accepted.
            registry_by_name[name] = item if name not in registry_by_name else None
    algorithms = {}
    for row in current.get('algos', []):
        if not isinstance(row, dict):
            continue
        try:
            settings = settings_by_id.get(int(row.get('a')))
        except (TypeError, ValueError):
            continue
        if not settings:
            continue
        name = settings['name']
        algorithms[name] = {**settings, 'speedRaw': row.get('s'), 'priceRaw': row.get('p'),
            'rigs': row.get('r'), 'orders': row.get('o'), 'volumeRaw': row.get('v'),
            'unitContract': unit_contract(settings, registry_by_name.get(name), at)}
    if not algorithms:
        raise ValueError('No relevant public market algorithms found')
    return {'schemaVersion': 2, 'collected_at': at, 'source': 'NICEHASH_PUBLIC_MARKET',
        'credentials_used': False, 'private_api_used': False, 'admin_api_used': False,
        'timestampRole': 'LOCAL_RECEIPT_NOT_VERIFIED_UPSTREAM_PRICE_TIME', 'algorithms': algorithms}


def main():
    buy_info = get_json('/main/api/v2/public/buy/info/')
    # Metadata is optional; failure never invents a currency or conversion.
    try:
        registry = get_json('/main/api/v2/mining/algorithms/')
    except Exception:
        registry = {}
        print('Public display-unit metadata unavailable; raw price remains unverified')
    current = get_json('/main/api/v2/public/stats/global/current/')
    now = datetime.now(timezone.utc)
    latest = snapshot(buy_info, current, registry, now.isoformat())
    rows = []
    if OUTPUT.exists():
        for line in OUTPUT.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except ValueError:
                continue
    kept = {}
    for row in rows + [latest]:
        ts = iso_to_dt(row.get('collected_at'))
        if ts is not None and now - timedelta(days=RETAIN_DAYS) <= ts <= now:
            kept.setdefault(ts, row)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(''.join(json.dumps(row, separators=(',', ':'), allow_nan=False) + '\n'
        for _, row in sorted(kept.items())), encoding='utf-8')
    print('PUBLIC MARKET HISTORY OK; no credentials, no private requests')
    print('Snapshots retained:', len(kept), '| algorithms:', ', '.join(sorted(latest['algorithms'])))


if __name__ == '__main__':
    main()
