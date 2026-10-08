// Port of scripts/apply_math_consistency_shadow.py v2; audit-only, never a signal override.
function mathPositive(v) {
  if (typeof v !== 'number' && typeof v !== 'string') return null;
  if (typeof v === 'string' && !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(v.trim())) return null;
  const n = Number(v); return Number.isFinite(n) && n > 0 ? n : null;
}
function scheduledChainCheck(p, c) {
  const algorithm = String(c.algorithm || '').toUpperCase(), coin = String(c.currency || '').toUpperCase();
  const supported = {SCRYPT:['LTC','DOGE'], SHA256ASICBOOST:['BTC','BCH'], SHA256ASICBOOST_USDT:['BTC','BCH']};
  if (!supported[algorithm]?.includes(coin)) return {status:'NOT_APPLICABLE',algorithm:algorithm || null,reason:'Difficulty convention not verified for this coin/algorithm pair.'};
  const [h,t,d,supplied] = [p.package_hashrate_hps,p.duration_seconds,c.network_difficulty,c.expected_blocks].map(mathPositive);
  const TWO32 = 2 ** 32, factor = 2 ** 48 / 65535;
  const expected = h !== null && t !== null && d !== null ? h / d * (t / TWO32) : null;
  if (!Number.isFinite(expected) || expected <= 0 || supplied === null) return {status:'UNKNOWN',algorithm,reason:'Inputs must be finite and strictly positive.'};
  const difference = 100 * (supplied / expected - 1);
  if (!Number.isFinite(difference)) return {status:'UNKNOWN',algorithm,reason:'Numeric overflow.'};
  const result = {status:Math.abs(difference) <= 5 ? 'PASS' : Math.abs(difference) <= 15 ? 'WARNING' : 'CRITICAL',algorithm,
    feedExpectedBlocks:supplied,difficultyExpectedBlocks:expected,
    signedDifferencePercent:Number(difference.toFixed(6)),absoluteDifferencePercent:Number(Math.abs(difference).toFixed(6)),
    difficulty:d,formula:'hashrate_hps * duration_seconds / (difficulty * 2^32)',
    differenceKind:'MODEL_DISAGREEMENT_NOT_A_VERIFIED_DATA_ERROR',difficultyConvention:'CONDITIONAL_BDIFF_APPROXIMATION',
    difficultyConventionVerifiedForUpstreamField:false,bdiffRefinedExpectedBlocks:expected * TWO32 / factor,
    constantApproximationErrorPercent:(factor / TWO32 - 1) * 100,externalInputsIndependentlyVerified:false,
    decomposition:{status:'MISSING_NETWORK_INPUTS'}};
  const network=mathPositive(c.network_hashpower_hps), target=mathPositive(c.block_time_seconds);
  if (network !== null && target !== null) {
    const implied=d / network * TWO32, n=h / network * t / target, ratio=implied / target;
    if ([implied,n,ratio].every(v=>Number.isFinite(v)&&v>0)) {
      const residual=100*(supplied/n-1);
      result.decomposition={status:Math.abs(residual)<=.01?'EXPLAINED_BY_TARGET_TIME_MODEL':'FEED_FORMULA_NOT_RECONCILED',
        targetBlockTimeSeconds:target,impliedBlockTimeSeconds:implied,networkHashrateDerivedExpectedBlocks:n,
        feedVsNetworkFormulaPercent:residual,networkVsDifficultyMultiplier:ratio,
        identity:'lambda_H/lambda_D = (D*2^32/H_network)/T_target',impliedIntervalIsObservedBlockInterval:false,independentValidation:false};
    }
  }
  return result;
}
function annotateScheduledMath(feed) {
  const summary={PASS:0,WARNING:0,CRITICAL:0,UNKNOWN:0,NOT_APPLICABLE:0};
  for (const p of feed.packages) {
    const checks=[];
    for (const [key,label] of [['primary_chain','PRIMARY'],['merge_chain','MERGE']])
      if (record(p[key]) && Object.keys(p[key]).length) checks.push({...scheduledChainCheck(p,p[key]),chain:label});
    const status=['CRITICAL','WARNING','UNKNOWN','PASS'].find(s=>checks.some(c=>c.status===s)) || 'NOT_APPLICABLE';
    summary[status]++;
    p.math_consistency_shadow={model_version:2,status,production_override:false,checks,
      thresholds:{passMaxAbsoluteDifferencePercent:5,warningMaxAbsoluteDifferencePercent:15},
      policy:'Conditional consistency only, not proof of source truth or pricing edge. CURRENT and existing disagreement thresholds are unchanged.'};
  }
  feed.math_consistency_shadow={model_version:2,model_use:'MATH_CONSISTENCY_AUDIT_ONLY',production_model:'CURRENT',
    production_model_changed:false,supported_algorithms:['SCRYPT','SHA256ASICBOOST','SHA256ASICBOOST_USDT'],summary};
  return feed;
}
