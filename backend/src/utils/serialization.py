import hashlib, json
from src.services.sensitivity import _flip_intervals
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}
def tier_dict(t):
    return {'id':t.id,'run_id':t.run_id,'upper':t.upper,'ordinal':t.ordinal,'status':t.status,
            'result':t.result or {},'error':t.error,'attempts':t.attempts,'updated_at':t.updated_at}
def sensitivity_dict(run, tiers=None):
    tiers = sorted(tiers or [], key=lambda t:t.ordinal)
    completed = [t for t in tiers if t.status=='completed']
    failed = [t for t in tiers if t.status=='failed']
    flips = _flip_intervals(tiers) if run.status in ('completed','switched') and not failed else []
    all_done = bool(tiers) and len(completed) == len(tiers)
    return {'id':run.id,'name':run.name,'dataset_id':run.dataset_id,
            'dataset_checksum':run.dataset_checksum,'lower':run.lower,'upper':run.upper,
            'step':run.step,'alpha':run.alpha,'params_fingerprint':run.params_fingerprint,
            'locked_params':run.locked_params or {},
            'status':run.status,'error':run.error,'version':run.version,'switched':run.switched,
            'switched_at':run.switched_at,'created_by':run.created_by,'created_at':run.created_at,
            'finished_at':run.finished_at,
            'tier_count':len(tiers),'completed_count':len(completed),'failed_count':len(failed),
            'flip_intervals':flips,'robust':all_done and not flips,
            'blocked_tiers':[t.upper for t in failed],
            'tiers':[tier_dict(t) for t in tiers]}
def brief_dict(b):
    return {'id':b.id,'run_id':b.run_id,'version':b.version,'filename':b.filename,
            'checksum':b.checksum,'snapshot':b.snapshot,'created_by':b.created_by,'created_at':b.created_at}
