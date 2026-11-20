import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from src.config.database import get_db
from src.config.settings import ARTIFACT_DIR
from src.models import SensitivityRun, SensitivityTier, SensitivityBrief
from src.schemas.sensitivity import SensitivityInput
from src.middlewares.auth import current_user, can_write, admin_only
from src.services import sensitivity
from src.services.audit import record
from src.utils.serialization import sensitivity_dict, brief_dict
router = APIRouter(prefix='/sensitivity')
def _tiers(db, run_id):
    return list(db.scalars(select(SensitivityTier).where(SensitivityTier.run_id == run_id)
                           .order_by(SensitivityTier.ordinal)))
@router.get('')
def listing(db=Depends(get_db), u=Depends(current_user)):
    runs = list(db.scalars(select(SensitivityRun).order_by(SensitivityRun.id.desc()).limit(100)))
    counts = dict(db.execute(select(SensitivityTier.run_id, func.count()).group_by(SensitivityTier.run_id)).all())
    out = []
    for run in runs:
        payload = sensitivity_dict(run)
        payload['tier_count'] = counts.get(run.id, 0)
        out.append(payload)
    return out
# 静态前缀路由必须在 /{rid} 之前注册，避免 briefs 被当作 rid
@router.get('/briefs/{bid}/download')
def download(bid: int, format: str = 'json', db=Depends(get_db), u=Depends(current_user)):
    brief = db.get(SensitivityBrief, bid)
    if not brief:
        raise HTTPException(404, '简报不存在')
    if format not in ('csv', 'json'):
        raise HTTPException(422, '支持 CSV 或 JSON')
    filename = brief.filename + ('.json' if format == 'json' else '.csv')
    path = os.path.join(ARTIFACT_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(404, '简报文件不存在')
    return FileResponse(path, filename=filename,
                        media_type='application/json' if format == 'json' else 'text/csv; charset=utf-8')
@router.post('', status_code=201)
def create(data: SensitivityInput, db=Depends(get_db), u=Depends(can_write)):
    run = sensitivity.create(db, data, u)
    record(db, u.email, 'sensitivity.create',
           {'id': run.id, 'dataset_id': run.dataset_id, 'dataset_checksum': run.dataset_checksum,
            'lower': run.lower, 'upper': run.upper, 'step': run.step, 'alpha': run.alpha,
            'params_fingerprint': run.params_fingerprint})
    db.commit()
    sensitivity.enqueue(run.id)
    return sensitivity_dict(run, _tiers(db, run.id))
@router.get('/{rid}')
def detail(rid: int, db=Depends(get_db), u=Depends(current_user)):
    run = db.get(SensitivityRun, rid)
    if not run:
        raise HTTPException(404, '敏感性调查不存在')
    return sensitivity_dict(run, _tiers(db, rid))
@router.post('/{rid}/retry')
def retry(rid: int, db=Depends(get_db), u=Depends(can_write)):
    run = sensitivity.retry_run(db, rid, u)
    db.commit()
    sensitivity.enqueue(run.id)
    return sensitivity_dict(run, _tiers(db, rid))
@router.post('/{rid}/confirm')
def confirm(rid: int, db=Depends(get_db), u=Depends(admin_only)):
    try:
        run, brief = sensitivity.confirm_and_switch(db, rid, u)
    except HTTPException:
        # 拒绝原因审计已在服务内写入，异常路径需先落库再抛出，避免会话关闭丢失
        db.commit()
        raise
    db.commit()
    return {'run': sensitivity_dict(run, _tiers(db, rid)), 'brief': brief_dict(brief)}
@router.get('/{rid}/briefs')
def briefs(rid: int, db=Depends(get_db), u=Depends(current_user)):
    if not db.get(SensitivityRun, rid):
        raise HTTPException(404, '敏感性调查不存在')
    rows = db.scalars(select(SensitivityBrief).where(SensitivityBrief.run_id == rid)
                      .order_by(SensitivityBrief.id.desc()))
    return [brief_dict(b) for b in rows]
