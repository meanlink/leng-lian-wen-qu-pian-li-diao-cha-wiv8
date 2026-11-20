"""温区上界敏感性调查：逐档复算、结论翻转定位、版本化确认切换。

业务约束：
- 创建时锁定测温批次指纹、上界起止值、步长与参数指纹，逐档保留参数与结果；
- 任一档失败则整项进入 blocked（待处理），不允许以部分档位批准；
- 中断后可恢复：重试只处理 pending/failed 档位，已完成档位沿用原结果、不重复计入；
- 负责人确认时复核批次与参数指纹，漂移即拒绝；确认后按同一版本一次性切换并生成简报；
- 简报为不可变快照，已发简报始终保留其原版本。
"""
import os
import csv
import io
import json
import math
import time
import hashlib
from fastapi import HTTPException
from src.models import Dataset, SensitivityRun, SensitivityTier, SensitivityBrief
from src.services.business import analyze
from src.config.settings import ARTIFACT_DIR

MIN_UPPER, MAX_UPPER = 2.0, 15.0
MAX_TIERS = 200
ALPHAS = (0.01, 0.05, 0.1)


def _fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _grid(lower, upper, step):
    """生成上界档位网格，末尾含端点，全部限制在算法支持的 2–15°C 内。"""
    values, current = [], lower
    while current < upper - step * 1e-9:
        values.append(round(current, 6))
        current += step
    values.append(round(upper, 6))
    cleaned, seen = [], set()
    for v in values:
        if not (MIN_UPPER <= v <= MAX_UPPER):
            raise HTTPException(422, f'温区上界档位 {v}°C 超出支持范围 {MIN_UPPER:g}–{MAX_UPPER:g}°C')
        if v not in seen:
            seen.add(v)
            cleaned.append(v)
    return cleaned


def _tier_outcome(upper, result):
    """从单档分析结果抽取口径稳健性所需的最小可复核指标。

    随上界变化的结论是各路线“超温判定”（是否存在超温点）；
    KS 双侧检验比较整条温度分布，其 p 值与上界无关，单独保留作参考。
    """
    metrics = {m['label']: m for m in result.get('metrics', [])}
    ks_verdict = '存在分布差异' if '存在分布差异' in result.get('summary', '') else '未检出分布差异'
    rows = result.get('table', {}).get('rows', [])
    route_b = rows[-1] if rows else {}
    route_a = rows[0] if rows else {}
    def excursion(row):
        over = row.get('over') or 0
        return '存在超温点' if over and over > 0 else '无超温点'
    return {
        'upper': upper,
        'verdict': excursion(route_b),  # 主结论：B 线（被复核路线）超温判定，随上界翻转
        'excursion_b': excursion(route_b),
        'excursion_a': excursion(route_a),
        'over_count_a': route_a.get('over'), 'over_rate_a': route_a.get('rate'),
        'over_count_b': route_b.get('over'), 'over_rate_b': route_b.get('rate'),
        'ks_verdict': ks_verdict,
        'pvalue': metrics.get('双侧 p 值', {}).get('value'),
        'd_statistic': metrics.get('KS 距离 D', {}).get('value'),
        'summary': result.get('summary', ''),
    }


# 逐档比对的结论维度：facet 为展示名，key 为档位结果中的字段
_FLIP_FACETS = (
    ('超温判定（路线 B）', 'verdict'),
    ('超温判定（路线 A）', 'excursion_a'),
    ('KS 分布结论', 'ks_verdict'),
)


def _flip_intervals(tiers):
    """按档位顺序定位各结论维度发生翻转的上界区间；档位未齐全时返回空。"""
    ordered = sorted(tiers, key=lambda t: t.ordinal)
    flips = []
    for prev, cur in zip(ordered, ordered[1:]):
        if prev.status != 'completed' or cur.status != 'completed':
            return []
        a, b = prev.result or {}, cur.result or {}
        for facet, key in _FLIP_FACETS:
            if a.get(key) != b.get(key):
                flips.append({'from_upper': prev.upper, 'to_upper': cur.upper, 'facet': facet,
                              'from_verdict': a.get(key), 'to_verdict': b.get(key)})
    return flips


def create(db, data, user):
    dataset = db.get(Dataset, data.dataset_id)
    if not dataset:
        raise HTTPException(404, '数据批次不存在')
    try:
        lower, upper, step = float(data.lower), float(data.upper), float(data.step)
        alpha = float(data.alpha)
    except (TypeError, ValueError):
        raise HTTPException(422, '上界起止值、步长与显著性水平须为数值')
    for label, v in (('上界起始值', lower), ('上界截止值', upper), ('步长', step)):
        if not math.isfinite(v):
            raise HTTPException(422, f'{label} 必须是有限数值')
    if not (MIN_UPPER <= lower <= MAX_UPPER and MIN_UPPER <= upper <= MAX_UPPER):
        raise HTTPException(422, f'上界起止值须在 {MIN_UPPER:g}–{MAX_UPPER:g}°C 内')
    if lower >= upper:
        raise HTTPException(422, '上界起始值必须严格小于截止值')
    if step <= 0:
        raise HTTPException(422, '步长必须为正数')
    if alpha not in ALPHAS:
        raise HTTPException(422, '显著性水平只接受 0.01/0.05/0.1')
    values = _grid(lower, upper, step)
    if len(values) > MAX_TIERS:
        raise HTTPException(422, f'档位数量 {len(values)} 超过上限 {MAX_TIERS}，请放大步长')
    lock = {'dataset_id': dataset.id, 'dataset_checksum': dataset.checksum,
            'lower': lower, 'upper': upper, 'step': step, 'alpha': alpha}
    run = SensitivityRun(
        name=data.name, dataset_id=dataset.id, dataset_checksum=dataset.checksum,
        lower=lower, upper=upper, step=step, alpha=alpha,
        params_fingerprint=_fingerprint(lock), locked_params=dict(lock),
        status='queued', version=1,
        created_by=user.id, created_at=time.time())
    db.add(run)
    db.flush()
    for ordinal, value in enumerate(values):
        db.add(SensitivityTier(run_id=run.id, upper=value, ordinal=ordinal,
                               status='pending', attempts=0))
    db.flush()
    return run


def enqueue(run_id):
    from redis import Redis
    from src.config.settings import REDIS_URL
    try:
        r = Redis.from_url(REDIS_URL)
        r.rpush('sensitivity:queue', run_id)
        r.close()
    except Exception:
        pass  # SQL queued 状态是持久化兜底，worker tick 会兜底扫描
def execute_run(run_id):
    """执行（或恢复执行）一次敏感性调查；已完成档位不重算、不重复计入。"""
    from src.config.database import Session
    from src.services.audit import record
    with Session() as db:
        run = db.get(SensitivityRun, run_id)
        if not run or run.status in ('completed', 'switched'):
            return
        run.status = 'running'
        run.error = ''
        db.commit()
        dataset = db.get(Dataset, run.dataset_id)
        tiers = list(db.query(SensitivityTier).filter_by(run_id=run.id).order_by(SensitivityTier.ordinal))
        for tier in tiers:
            if tier.status == 'completed':
                continue  # 断点续算：沿用原档位结果
            tier.status = 'running'
            tier.attempts += 1
            db.commit()
            try:
                result = analyze(dataset.records, {'upper': tier.upper, 'alpha': run.alpha})
                json.dumps(result, allow_nan=False)
                tier.result = _tier_outcome(tier.upper, result)
                tier.status = 'completed'
                tier.error = ''
            except Exception as exc:  # 单档失败可恢复，记录后继续评估其余档位
                tier.status = 'failed'
                tier.error = str(exc)[:990]
            tier.updated_at = time.time()
            db.commit()
        failed = [t for t in tiers if t.status == 'failed']
        if failed:
            run.status = 'blocked'
            run.error = f'{len(failed)} 个档位失败，整项待处理；成功档位不能用于批准'
            record(db, 'worker', 'sensitivity.blocked',
                   {'id': run.id, 'blocked_tiers': [t.upper for t in failed]})
        else:
            run.status = 'completed'
            run.finished_at = time.time()
            record(db, 'worker', 'sensitivity.completed', {'id': run.id, 'tiers': len(tiers)})
        db.commit()


def retry_run(db, run_id, user):
    run = db.get(SensitivityRun, run_id)
    if not run:
        raise HTTPException(404, '敏感性调查不存在')
    if run.switched:
        raise HTTPException(409, '该版本已切换，不能重算；如需调整请新建调查')
    if run.status not in ('blocked', 'queued', 'running'):
        raise HTTPException(409, f'当前状态 {run.status} 无需重试')
    pending = list(db.query(SensitivityTier).filter(
        SensitivityTier.run_id == run_id,
        SensitivityTier.status.in_(('pending', 'failed'))).order_by(SensitivityTier.ordinal))
    if not pending:
        raise HTTPException(409, '没有待处理或失败档位，无需重试')
    run.status = 'queued'
    run.error = ''
    from src.services.audit import record
    record(db, user.email, 'sensitivity.retry',
           {'id': run.id, 'retry_tiers': [t.upper for t in pending]})
    return run


def confirm_and_switch(db, run_id, user):
    """负责人确认：校验锁定口径未漂移，全部档位成功后一次性切换并生成简报。"""
    from src.services.audit import record
    run = db.get(SensitivityRun, run_id)
    if not run:
        raise HTTPException(404, '敏感性调查不存在')
    if run.switched or run.status == 'switched':
        raise HTTPException(409, '该版本已切换；已发简报保留原版本，不能重复切换')
    dataset = db.get(Dataset, run.dataset_id)
    if not dataset:
        raise HTTPException(409, '锁定的测温批次已不存在，拒绝切换')
    locked = dict(run.locked_params or {})
    if locked.get('dataset_checksum') != run.dataset_checksum or dataset.checksum != locked.get('dataset_checksum'):
        record(db, user.email, 'sensitivity.switch.rejected',
               {'id': run.id, 'reason': '测温批次指纹变化'})
        raise HTTPException(409, '确认期间测温批次发生变化，口径已漂移，拒绝切换')
    current = {'dataset_id': dataset.id, 'dataset_checksum': dataset.checksum,
               'lower': run.lower, 'upper': run.upper, 'step': run.step, 'alpha': run.alpha}
    # 与创建时固化的不可变快照做等值比对；任何字段被改动都会失配
    if _fingerprint(current) != run.params_fingerprint or current != locked:
        record(db, user.email, 'sensitivity.switch.rejected',
               {'id': run.id, 'reason': '参数指纹变化'})
        raise HTTPException(409, '确认期间上界起止值、步长或显著性水平发生变化，拒绝切换')
    tiers = list(db.query(SensitivityTier).filter_by(run_id=run.id).order_by(SensitivityTier.ordinal))
    failed = [t for t in tiers if t.status != 'completed']
    if failed:
        raise HTTPException(409, f'{len(failed)} 个档位未成功（含阻塞档位），不能用部分档位批准')
    flips = _flip_intervals(tiers)
    # 同一版本一次性切换：状态、时间、版本在同一事务落库
    run.switched = True
    run.status = 'switched'
    run.switched_at = time.time()
    run.switched_by = user.id
    db.flush()
    brief = _write_brief(db, run, tiers, flips, user)
    record(db, user.email, 'sensitivity.switched',
           {'id': run.id, 'version': run.version, 'brief': brief.id, 'flip_intervals': flips})
    return run, brief


def _write_brief(db, run, tiers, flips, user):
    keep = ('upper', 'verdict', 'excursion_a', 'excursion_b', 'ks_verdict', 'pvalue',
            'd_statistic', 'over_count_a', 'over_rate_a', 'over_count_b', 'over_rate_b', 'summary')
    tier_rows = [{k: (t.result or {}).get(k) for k in keep} for t in tiers]
    facets = sorted({f.get('facet') for f in flips if f.get('facet')})
    snapshot = {
        'run_id': run.id, 'version': run.version, 'name': run.name,
        'locked': {'dataset_id': run.dataset_id, 'dataset_checksum': run.dataset_checksum,
                   'lower': run.lower, 'upper': run.upper, 'step': run.step,
                   'alpha': run.alpha, 'params_fingerprint': run.params_fingerprint},
        'generated_at': time.time(), 'generated_by': user.email,
        'flip_intervals': flips, 'robust': not flips, 'flip_facets': facets,
        'tiers': tier_rows,
    }
    payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode()
    checksum = hashlib.sha256(payload).hexdigest()
    brief = SensitivityBrief(run_id=run.id, version=run.version, filename='',
                             checksum=checksum, snapshot=snapshot,
                             created_by=user.id, created_at=time.time())
    db.add(brief)
    db.flush()
    brief.filename = f'sensitivity-{run.id}-v{run.version}-brief-{brief.id}'
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    with open(os.path.join(ARTIFACT_DIR, brief.filename + '.json'), 'wb') as f:
        f.write(payload)
    out = io.StringIO()
    out.write('\ufeff')
    writer = csv.writer(out)
    writer.writerow(['温区上界°C', 'B线超温判定', 'A线超温判定', 'KS结论', '双侧p值', 'KS距离D',
                     'A线超温点', 'A线超温率%', 'B线超温点', 'B线超温率%', '摘要'])
    for row in tier_rows:
        writer.writerow([row['upper'], row['verdict'], row['excursion_a'], row['ks_verdict'],
                         row['pvalue'], row['d_statistic'], row['over_count_a'], row['over_rate_a'],
                         row['over_count_b'], row['over_rate_b'], row['summary']])
    with open(os.path.join(ARTIFACT_DIR, brief.filename + '.csv'), 'w', encoding='utf-8', newline='') as f:
        f.write(out.getvalue())
    db.flush()
    return brief
