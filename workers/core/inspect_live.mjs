// Read-only compatibility check on the real public feed. NEVER changes its time.
import {readFileSync,writeFileSync,existsSync} from 'node:fs';
import {evaluate} from './policy.mjs';
const dir=process.env.RADAR_PREFLIGHT_OUT||'/tmp/radar-core-preflight';
const path=dir+'/live-feed.json';
const result={schema:'BUY_RADAR_CORE_LIVE_COMPATIBILITY_V1',evaluatedAt:new Date().toISOString(),
  realPublicFeedRead:existsSync(path),deploymentPerformed:false,notificationSent:false};
if(existsSync(path)) {
  const report=evaluate(JSON.parse(readFileSync(path,'utf8')));
  Object.assign(result,{sourceAt:report.sourceAt,sourceAgeSeconds:report.sourceAgeSeconds,
    state:report.state,reasons:report.reasons,packages:report.packages.map(p=>({
      name:p.package,state:p.state,reason:p.reason,math:p.math,actionable:p.actionable,signal:p.signal,
      costEur:p.costEur,expectedReturnPercent:p.expectedReturnPercent}))});
} else result.state='LIVE_SOURCE_UNAVAILABLE';
writeFileSync(dir+'/compatibility.json',JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result,null,2));
