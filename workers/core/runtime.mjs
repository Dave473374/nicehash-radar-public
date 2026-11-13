import {POLICY, MAX_AGE_MS, object, timestamp, evaluate, initialState, advance} from './policy.mjs';
const CRON = '*/5 * * * *';
const json = (value,status=200) => new Response(JSON.stringify(value), {status,
  headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'}});

// One Durable Object owns collection, transitions and the PRIVATE durable outbox.
// collect is the existing public NiceHash/Kraken engine, not another collector.
export function coordinator(collect) {
  return class RadarCore {
    constructor(ctx,env) { this.ctx=ctx; this.env=env; this.queue=Promise.resolve(); }
    async fetch(request) {
      const path = new URL(request.url).pathname;
      if (request.method==='POST' && path==='/tick') {
        const tick=await request.json();
        const work=this.queue.catch(()=>{}).then(()=>this.tick(tick));
        this.queue=work;
        return json(await work);
      }
      if (['GET','HEAD'].includes(request.method) && ['/health','/buy-feed'].includes(path)) {
        const state=await this.ctx.storage.get('state') ?? initialState();
        const feed=await this.ctx.storage.get('feed');
        const report=evaluate(feed);
        if (state.lastError) {
          report.state='DATA_BLOCKED'; report.reasons=[state.lastError];
          report.packages=report.packages.map(p=>({...p,state:'DATA_BLOCKED',reason:state.lastError,actionable:false,signal:null}));
        }
        const delivery=this.env.CORE_MODE!=='active' ? 'NOT_ACTIVATED' : !this.env.RADAR_NOTIFY ? 'UNCONFIGURED'
          : Object.keys(state.uncertain).length || state.outbox.some(e=>e.status==='UNCERTAIN') ? 'UNCERTAIN'
          : state.outbox.length ? 'PENDING' : state.lastAccepted ? 'PROVIDER_ACCEPTED' : 'NOT_TESTED';
        const health={schema:POLICY, servedAt:new Date().toISOString(), sourceAt:report.sourceAt,
          sourceAgeSeconds:report.sourceAgeSeconds, data:report.state, reasons:report.reasons,
          lastAttempt:state.lastAttempt??null, schedulingDelaySeconds:state.schedulingDelaySeconds??null,
          delivery, pendingCount:state.outbox.length, acceptedCount:state.acceptedCount,
          deviceReceived:'UNKNOWN', productionReady:false, automaticPurchase:false};
        const response=json(path==='/health' ? health : {...report,health});
        return request.method==='HEAD' ? new Response(null,response) : response;
      }
      return json({error:'READ_ONLY_OR_INTERNAL_ROUTE'},405);
    }
    async tick(controller) {
      const now=Date.now();
      if (!['observe','active'].includes(this.env.CORE_MODE)) throw new Error('CORE_NOT_ACTIVATED');
      if (controller.cron!==CRON || !Number.isFinite(controller.scheduledTime) ||
          controller.scheduledTime>now || now-controller.scheduledTime>MAX_AGE_MS) throw new Error('INVALID_OR_DELAYED_CRON');
      let state=await this.ctx.storage.get('state') ?? initialState();
      if (state.version!==1) throw new Error('UNKNOWN_DURABLE_STATE_VERSION');
      const slot=Math.floor(controller.scheduledTime/300_000);
      if (slot<=state.slot) return {state:'DUPLICATE_OR_OLDER_TICK_SKIPPED'};
      state.slot=slot; state.lastAttempt=new Date(now).toISOString();
      state.schedulingDelaySeconds=(now-controller.scheduledTime)/1000;
      // A crash here does not allow the same tick to collect a second time.
      // It is NOT an exactly-once guarantee for upstream requests or delivery.
      state.lastError='COLLECTION_IN_PROGRESS_OR_INTERRUPTED';
      await this.ctx.storage.put('state',state);
      let feed=await this.ctx.storage.get('feed'), report;
      try {
        const next=await collect(this.env);
        report=evaluate(next);
        if (state.sourceAt && timestamp(next?.checked_at)<timestamp(state.sourceAt)) {
          report.state='DATA_BLOCKED'; report.reasons=['SOURCE_TIME_REGRESSION'];
        }
        if (report.state!=='DATA_BLOCKED') feed=next;
        state.lastError=report.state==='DATA_BLOCKED' ? report.reasons.join(',') : null;
      } catch {
        report={schema:POLICY, state:'DATA_BLOCKED', sourceAt:state.sourceAt,
          evaluatedAt:new Date().toISOString(), packages:[], reasons:['COLLECTION_FAILED']};
        state.lastError='COLLECTION_FAILED';
      }
      state=advance(state,report,Date.now());
      await this.ctx.storage.put({state,feed:feed??null});
      state=await this.deliver(state,report);
      return {state:report.state, sourceAt:report.sourceAt, pending:state.outbox.length,
        delivery:this.env.CORE_MODE!=='active' ? 'NOT_ACTIVATED' : !this.env.RADAR_NOTIFY ? 'UNCONFIGURED' : 'CHECK_RECEIPTS',
        automaticPurchase:false};
    }
    async deliver(state,report) {
      if (this.env.CORE_MODE!=='active' || !this.env.RADAR_NOTIFY) return state;
      let requests=0;
      for (const event of [...state.outbox]) {
        if (requests>=3 || event.expiresAt<=Date.now() || event.attempts>=3) continue;
        if (event.key) {
          const p=report.packages?.find(p=>p.key===event.key);
          const fp=p && `${p.actionable ? p.signal : p.reason}|${p.math}`;
          if (report.state==='DATA_BLOCKED' || !p?.observed || fp!==event.fingerprint ||
              Date.now()-timestamp(report.sourceAt)>MAX_AGE_MS) continue;
        }
        // Persist BEFORE I/O. An ambiguous timeout never becomes a delivery success.
        event.attempts++; event.status='UNCERTAIN';
        await this.ctx.storage.put('state',state);
        requests++;
        try {
          const response=await this.env.RADAR_NOTIFY.fetch('https://notify.internal/notify', {
            method:'POST', headers:{'Content-Type':'application/json','Idempotency-Key':event.id},
            body:JSON.stringify({schema:POLICY,event_id:event.id,kind:event.kind,text:event.text,
              expires_at:new Date(event.expiresAt).toISOString(),automatic_purchase:false}),
            signal:AbortSignal.timeout(8000), redirect:'error'});
          const text=await response.text();
          if (text.length>4096) throw new Error('INVALID_RECEIPT');
          const receipt=JSON.parse(text);
          if (!response.ok || !object(receipt) || receipt.accepted!==true ||
              receipt.event_id!==event.id || typeof receipt.receipt_id!=='string' ||
              !receipt.receipt_id.length || receipt.receipt_id.length>512) throw new Error('NOT_ACKNOWLEDGED');
          state.acceptedCount++;
          state.lastAccepted={eventId:event.id, at:new Date().toISOString(), receipt:receipt.receipt_id};
          if (event.key) state.accepted[event.key]=event.fingerprint;
          state.outbox=state.outbox.filter(e=>e.id!==event.id);
        } catch { /* Preserve the same immutable event ID for bounded retries. */ }
        await this.ctx.storage.put('state',state);
      }
      return state;
    }
  };
}

export const worker = {
  async scheduled(controller,env) {
    if (!['observe','active'].includes(env.CORE_MODE)) throw new Error('CORE_NOT_ACTIVATED');
    if (!env.RADAR_CORE) throw new Error('RADAR_CORE_BINDING_MISSING');
    const stub=env.RADAR_CORE.get(env.RADAR_CORE.idFromName('buy-radar-core-v1'));
    const response=await stub.fetch('https://core.internal/tick', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({cron:controller.cron,scheduledTime:controller.scheduledTime})});
    if (!response.ok) throw new Error('CORE_TICK_FAILED');
    return response.json();
  },
  async fetch(request,env) {
    const path=new URL(request.url).pathname;
    if (!['GET','HEAD'].includes(request.method)) return json({error:'READ_ONLY'},405);
    if (!['/health','/buy-feed'].includes(path)) return json({error:'NOT_FOUND'},404);
    if (!env.RADAR_CORE) return json({schema:POLICY,error:'RADAR_CORE_BINDING_MISSING',productionReady:false},503);
    return env.RADAR_CORE.get(env.RADAR_CORE.idFromName('buy-radar-core-v1')).fetch(request);
  }
};
