import {datasets, sensitivity} from '../api/entities.js';
import {downloadUrl} from '../api/client.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,fmt,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {SensitivityStatus, TierStatus} from '../constants/enums.js';

const verdictClass = v => (v==='存在超温点'||v==='存在分布差异') ? 'verdict-diff'
 : ((v==='无超温点'||v==='未检出分布差异') ? 'verdict-same' : '');

function lockCard(r){
 const l=r.locked_params||{};
 return '<div class="lock-grid">'+[
  ['测温批次指纹', esc(String(r.dataset_checksum).slice(0,20))+'…'],
  ['上界起始 / 截止', fmt(r.lower)+'°C → '+fmt(r.upper)+'°C'],
  ['步长', fmt(r.step)+'°C · 共 '+r.tier_count+' 档'],
  ['显著性水平 α', fmt(r.alpha)],
  ['参数指纹', esc(String(r.params_fingerprint).slice(0,20))+'…'],
  ['版本', 'v'+r.version]
 ].map(([k,v])=>'<div><span>'+k+'</span><strong>'+v+'</strong></div>').join('')+'</div>';
}

function flipPanel(r){
 const flips=r.flip_intervals||[];
 if(r.status!=='completed'&&r.status!=='switched'){
  return '<section class="panel"><div class="section-title"><h2>翻转区间</h2><span>口径稳健性</span></div><p class="empty">全部档位计算完成后定位结论翻转的上界区间。</p></section>';
 }
 if(!flips.length){
  return '<section class="panel robust-ok"><div class="section-title"><h2>翻转区间</h2><span class="badge ok">口径稳健</span></div><p>在锁定的 '+fmt(r.lower)+'–'+fmt(r.upper)+'°C 全部档位上各结论维度均未翻转，质控可据此判断该口径稳健。</p></section>';
 }
 const facets=[...new Set(flips.map(f=>f.facet))];
 return '<section class="panel robust-flip"><div class="section-title"><h2>翻转区间</h2><span class="badge flip">'+flips.length+' 处翻转 · 口径不稳健</span></div>'+
  '<p class="flip-lead">结论随温区上界翻转，质控不能据单一口径批准。涉及维度：'+facets.map(esc).join('、')+'。请在下列区间内复核包装与门开记录。</p>'+
  '<div class="table-wrap"><table><thead><tr><th>结论维度</th><th>翻转区间（上界）</th><th>区间低端结论</th><th>区间高端结论</th></tr></thead><tbody>'+
  flips.map(f=>'<tr><td>'+esc(f.facet||'—')+'</td><td><strong>'+fmt(f.from_upper)+'°C → '+fmt(f.to_upper)+'°C</strong></td><td><span class="'+verdictClass(f.from_verdict)+'">'+esc(f.from_verdict)+'</span></td><td><span class="'+verdictClass(f.to_verdict)+'">'+esc(f.to_verdict)+'</span></td></tr>').join('')+
  '</tbody></table></div></section>';
}

function blockedPanel(r){
 const blocked=(r.tiers||[]).filter(t=>t.status==='failed');
 if(!blocked.length)return '';
 return '<section class="panel blocked-panel"><div class="section-title"><h2>阻塞档位</h2><span class="badge block">'+blocked.length+' 档失败 · 整项待处理</span></div>'+
  '<p class="warning">任一档失败即整项阻塞，不能用部分档位批准。修复数据或口径后由分析员重试，已完成档位不会重复计算。</p>'+
  '<div class="table-wrap"><table><thead><tr><th>档位上界</th><th>状态</th><th>尝试次数</th><th>失败原因</th></tr></thead><tbody>'+
  blocked.map(t=>'<tr><td><strong>'+fmt(t.upper)+'°C</strong></td><td><span class="status failed">'+esc(TierStatus[t.status]||t.status)+'</span></td><td>'+t.attempts+'</td><td>'+esc(t.error||'—')+'</td></tr>').join('')+
  '</tbody></table></div></section>';
}

function tierTable(r){
 const tiers=r.tiers||[];
 return '<section class="panel"><div class="section-title"><h2>逐档参数与结果</h2><span>'+r.completed_count+'/'+r.tier_count+' 档完成</span></div>'+
  '<div class="table-wrap"><table><thead><tr><th>#</th><th>温区上界</th><th>B 线超温判定</th><th>A 线超温判定</th><th>KS 结论</th><th>双侧 p 值</th><th>KS 距离 D</th><th>B 超温点</th><th>B 超温率</th><th>状态</th><th>尝试</th></tr></thead><tbody>'+
  tiers.map(t=>'<tr class="'+(t.status==='failed'?'row-failed':'')+'"><td>'+(t.ordinal+1)+'</td><td><strong>'+fmt(t.upper)+'°C</strong></td><td><span class="'+verdictClass(t.result?.verdict)+'">'+esc(t.result?.verdict||'—')+'</span></td><td><span class="'+verdictClass(t.result?.excursion_a)+'">'+esc(t.result?.excursion_a||'—')+'</span></td><td><span class="'+verdictClass(t.result?.ks_verdict)+'">'+esc(t.result?.ks_verdict||'—')+'</span></td><td>'+fmt(t.result?.pvalue)+'</td><td>'+fmt(t.result?.d_statistic)+'</td><td>'+fmt(t.result?.over_count_b)+'</td><td>'+fmt(t.result?.over_rate_b)+'%</td><td><span class="status '+esc(t.status)+'">'+esc(TierStatus[t.status]||t.status)+'</span></td><td>'+t.attempts+'</td></tr>').join('')+
  '</tbody></table></div></section>';
}

function briefPanel(r, briefs){
 const canConfirm=session.user.role==='admin';
 const actions = r.status==='blocked'
  ? '<button class="button" id="retry-run" '+(session.user.role==='viewer'?'disabled':'')+'>重试阻塞档位 ↻</button>'
  : (r.status==='completed'
    ? '<button class="primary" id="confirm-run" '+(canConfirm?'':'disabled')+'>确认并按 v'+r.version+' 一次性切换 ↗</button>'
    : (r.status==='switched'?'<span class="badge ok">已按 v'+r.version+' 切换</span>':'<span class="muted">计算进行中…</span>'));
 return '<section class="panel"><div class="section-title"><h2>确认与版本切换</h2><span>'+esc(SensitivityStatus[r.status]||r.status)+'</span></div>'+
  lockCard(r)+
  (r.error?'<p class="warning">'+esc(r.error)+'</p>':'')+
  '<div class="action-row">'+actions+(canConfirm?'':'<p class="hint">仅业务负责人可确认切换；分析员可创建与重试。</p>')+'</div>'+
  (briefs.length?'<div class="brief-list"><h3>已发简报（保留原版本，不可变）</h3>'+briefs.map(b=>'<div class="delivery"><div><strong>'+esc(b.filename)+'.json</strong><p>版本 v'+b.version+' · '+date(b.created_at)+'</p><small>SHA256 '+esc(String(b.checksum).slice(0,24))+'…</small></div><div><button class="text-button" data-brief="'+b.id+'" data-fmt="json">JSON ↓</button><button class="text-button" data-brief="'+b.id+'" data-fmt="csv">CSV ↓</button></div></div>').join('')+'</div>':'<p class="hint">切换后在此生成版本化简报；已发简报始终保留其原版本。</p>')+
  '</section>';
}

export async function sensitivityPage(id){
 workspace.datasets=await datasets.list();
 const runs=await sensitivity.list();
 let r = id ? await sensitivity.get(Number(id)) : (runs[0]?await sensitivity.get(runs[0].id):null);
 if(r && !runs.some(x=>x.id===r.id))runs.unshift(r);
 const briefs = r ? await sensitivity.briefs(r.id) : [];
 if(!r){
  return heading('06 / SENSITIVITY','温区上界敏感性调查','结论会随温区上界翻转。创建时锁定测温批次、上界起止值与步长，逐档计算并保留参数与结果，任一档失败即整项待处理。')+
   createForm()+'<div class="empty">尚无敏感性调查。填写上界区间后发起逐档计算。</div>';
 }
 return heading('06 / SENSITIVITY','温区上界敏感性调查','逐档复算定位结论翻转区间；负责人确认口径未漂移后按同一版本一次性切换并生成简报。',
   '<button class="button" id="refresh-sens">刷新 ↻</button>')+
 '<div class="result-selector"><label>调查项<select id="select-run">'+runs.map(x=>'<option value="'+x.id+'" '+(x.id===r.id?'selected':'')+'>'+esc(x.name)+' · '+esc(SensitivityStatus[x.status]||x.status)+'</option>').join('')+'</select></label><span class="status '+esc(r.status)+'">'+esc(SensitivityStatus[r.status]||r.status)+'</span><span>'+date(r.created_at)+'</span></div>'+
 '<div class="sens-grid"><div>'+flipPanel(r)+blockedPanel(r)+tierTable(r)+'</div>'+briefPanel(r,briefs)+createForm(r)+'</div>';
}

function createForm(active){
 return '<section class="panel method-sheet compact sens-form"><div class="section-title"><h2>'+(active?'新建调查':'发起敏感性调查')+'</h2></div><form id="sens-form">'+
  '<label>调查名称<input name="name" required minlength="2" value="上界敏感性 · 口径稳健性复核"></label>'+
  '<label>测温批次（创建时锁定指纹）<select name="dataset_id" required>'+workspace.datasets.map(d=>'<option value="'+d.id+'" '+(active&&d.id===active.dataset_id?'selected':'')+'>'+esc(d.name)+' · '+d.rows+' 条</option>').join('')+'</select></label>'+
  '<div class="parameter-grid"><label>上界起始 °C<input type="number" name="lower" value="6" step="0.1" min="2" max="15" required></label><label>上界截止 °C<input type="number" name="upper" value="9" step="0.1" min="2" max="15" required></label></div>'+
  '<div class="parameter-grid"><label>步长 °C<input type="number" name="step" value="0.5" step="0.1" min="0.1" required></label><label>显著性水平<select name="alpha"><option value="0.01">0.01</option><option value="0.05" selected>0.05</option><option value="0.1">0.10</option></select></label></div>'+
  '<button type="submit" class="primary" '+(session.user.role==='viewer'||!workspace.datasets.length?'disabled':'')+'>锁定口径并逐档计算 ↗</button>'+
  '<p class="hint">提交后锁定批次指纹与全部参数，逐档保留结果。支持上界 '+esc('2–15°C')+'，最多 200 档；中断后可恢复，重试沿用原档位且不重复计入。</p></form></section>';
}

export function bindSensitivity(refresh){
 document.querySelector('#refresh-sens')?.addEventListener('click',refresh);
 document.querySelector('#select-run')?.addEventListener('change',e=>{location.hash='#/sensitivity/'+e.target.value;});
 submit(document.querySelector('#sens-form'),async f=>{
  const payload={name:f.get('name'),dataset_id:Number(f.get('dataset_id')),lower:Number(f.get('lower')),upper:Number(f.get('upper')),step:Number(f.get('step')),alpha:Number(f.get('alpha'))};
  const r=await sensitivity.create(payload);notify('敏感性调查已提交，后台逐档计算');location.hash='#/sensitivity/'+r.id;
 });
 document.querySelector('#retry-run')?.addEventListener('click',async()=>{
  const id=Number(location.hash.split('/')[2]);
  try{await sensitivity.retry(id);notify('已按原档位重试，成功档位不重复计算');await refresh();}catch(e){notify(e.message,true);}
 });
 document.querySelector('#confirm-run')?.addEventListener('click',async()=>{
  const id=Number(location.hash.split('/')[2]);
  try{const res=await sensitivity.confirm(id);notify('已按 v'+res.run.version+' 一次性切换并生成简报');await refresh();}catch(e){notify(e.message,true);}
 });
 document.querySelectorAll('[data-brief]').forEach(b=>b.addEventListener('click',()=>{
  downloadUrl('/sensitivity/briefs/'+b.dataset.brief+'/download?format='+b.dataset.fmt,'敏感性简报-'+b.dataset.brief+'.'+b.dataset.fmt).catch(e=>notify(e.message,true));
 }));
}
