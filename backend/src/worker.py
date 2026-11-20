import time, json, logging
from pathlib import Path
from sqlalchemy import select
from redis import Redis
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models import Job, Dataset, Report, SensitivityRun
from src.services.business import analyze
from src.services.reports import deliver
from src.services.sensitivity import execute_run
from src.services.audit import record
logging.basicConfig(level=logging.INFO)
def execute(job_id):
    with Session() as db:
        j=db.get(Job,job_id)
        if not j or j.status!='queued':return
        j.status='running'; db.commit()
        try:
            d=db.get(Dataset,j.dataset_id)
            result=analyze(d.records,j.params)
            json.dumps(result,allow_nan=False)
            j.result=result;j.status='completed';j.finished_at=time.time()
            record(db,'worker','job.completed',{'id':j.id})
        except Exception as e:
            logging.exception('Analysis failed')
            j.status='failed';j.error=str(e)[:990];j.finished_at=time.time()
            record(db,'worker','job.failed',{'id':j.id,'error':j.error})
        db.commit()
def tick():
    with Session() as db:
        queued=list(db.scalars(select(Job.id).where(Job.status=='queued').limit(3)))
    for job_id in queued:execute(job_id)
    # 敏感性调查：Redis 队列丢失时由 SQL queued 状态兜底，每轮最多推进 2 项
    with Session() as db:
        runs=list(db.scalars(select(SensitivityRun.id).where(SensitivityRun.status=='queued').limit(2)))
    for run_id in runs:
        try: execute_run(run_id)
        except Exception: logging.exception('Sensitivity run failed')
    with Session() as db:
        for r in db.scalars(select(Report).where(Report.enabled==True,Report.next_run<=time.time())):
            d=deliver(db,r)
            if d:record(db,'worker','report.delivered',{'id':r.id,'delivery':d.id})
        db.commit()
def main():
    r=Redis.from_url(REDIS_URL,socket_timeout=3)
    # This Compose stack has a single worker. A crash must not strand its jobs.
    with Session() as db:
        for j in db.scalars(select(Job).where(Job.status=='running')):
            j.status='queued';record(db,'worker','job.recovered',{'id':j.id})
        # 中断恢复：running 的敏感性调查回到 queued，已完成档位不重算、不重复计入
        for run in db.scalars(select(SensitivityRun).where(SensitivityRun.status=='running')):
            run.status='queued';record(db,'worker','sensitivity.recovered',{'id':run.id})
        db.commit()
    while True:
        Path('/tmp/heartbeat').touch()
        try:
            item=r.blpop(['analysis:queue','sensitivity:queue'],timeout=1)
            if item:
                queue, payload = item[0].decode(), item[1].decode()
                if queue=='sensitivity:queue':
                    try: execute_run(int(payload))
                    except Exception: logging.exception('Sensitivity run failed')
                else:
                    execute(int(payload))
            tick()
        except Exception:
            logging.exception('Worker retry');time.sleep(2)
if __name__=='__main__':main()
