import { useEffect, useState } from 'react';
import type { RagApi } from './api';
import { ApiError } from './api';
import type { Paper, Evidence } from './types';
import { activeTask, requestId, saveBlob, taskLabels } from './research';
import type { Analysis } from './research';
import { useResearchClient } from './useResearchClient';
import Modal from './Modal';
import './research.css';

export default function ResearchStudio({ api: session, papers, initialIds, onClose, onOpen, onUnauthorized }: { api: RagApi; papers: Paper[]; initialIds: string[]; onClose: () => void; onOpen: (e: Evidence) => void; onUnauthorized: () => void }) {
  const { client, alive, error, setError, report } = useResearchClient(session, onUnauthorized);
  const [selected, setSelected] = useState(initialIds.slice(0, 5)), [jobs, setJobs] = useState<Analysis[]>([]), [jobId, setJobId] = useState('');
  const [busy, setBusy] = useState(false), [pending, setPending] = useState(session.pendingAnalysisSubmission);
  const [edit, setEdit] = useState<{ document: string; field: string; text: string; revision: number } | null>(null);
  const job = jobs.find(j => j.id === jobId), cards = job?.result.cards ?? [];
  useEffect(() => {
    let stopped = false; let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try { const r = await client.analyses(); if (stopped) return; setJobs(r.jobs);
        const recovered = r.jobs.find(j => j.id === session.pendingAnalysisSubmission?.request_id);
        if (recovered) { session.pendingAnalysisSubmission = null; setPending(null); setJobId(recovered.id); }
      } catch (e) { if (!stopped) report(e); } finally { if (!stopped) timer = setTimeout(load, 2000); }
    }
    void load(); return () => { stopped = true; clearTimeout(timer); };
  }, [client, session]);
  function put(updated: Analysis) { if (alive.current) { setJobs(old => [updated, ...old.filter(j => j.id !== updated.id)]); setJobId(updated.id); } }
  async function submit() {
    if (busy) return;
    const body = pending ?? { request_id: requestId(), document_ids: selected };
    setPending(body); session.pendingAnalysisSubmission = body; setBusy(true); setError('');
    try { const result = await client.analyze(body.request_id, body.document_ids); if (alive.current) { put(result); setPending(null); session.pendingAnalysisSubmission = null; } }
    catch (e) { if (e instanceof ApiError && e.status > 0 && e.status < 500) { setPending(null); session.pendingAnalysisSubmission = null; } report(e); }
    finally { if (alive.current) setBusy(false); }
  }
  async function saveEdit(reset = false) {
    if (!job || !edit) return; setBusy(true);
    try { put(await client.editCard(job.id, edit.document, edit.field, reset ? null : edit.text, edit.revision)); if (alive.current) setEdit(null); } catch (e) { report(e); } finally { if (alive.current) setBusy(false); }
  }
  function leave() { if (!edit || window.confirm('放弃未保存的人工笔记？')) onClose(); }
  return <Modal className="research-dialog wide" labelId="research-title" onClose={leave} busy={busy}>
    <header className="research-header"><div><h2 id="research-title">论文阅读卡片与多篇对比</h2><p>逐篇检索、统一维度整理，保留每项原文依据</p></div><button className="secondary" disabled={busy} onClick={leave}>关闭</button></header>
    {error && <div className="error-box" role="alert">{error}</div>}
    <div className="research-toolbar"><details><summary>选择论文（已选 {selected.length} 篇）</summary><div className="research-paper-picker">{papers.map(p => <label key={p.document_id}><input type="checkbox" checked={selected.includes(p.document_id)} disabled={busy || !!pending || (!selected.includes(p.document_id) && selected.length >= 5)} onChange={e => setSelected(old => e.target.checked ? [...old, p.document_id] : old.filter(id => id !== p.document_id))} />{p.filename}</label>)}</div></details>
      <button className="primary" disabled={busy || !selected.length || jobs.some(activeTask) || !!edit} onClick={() => void submit()}>{pending ? '确认上次提交结果' : selected.length > 1 ? '生成对比表' : '生成阅读卡片'}</button>
      <span className="research-muted">1 篇：阅读卡片；2–5 篇：逐项对比。每篇调用一次模型。</span>
    </div>
    {pending && <p role="status">提交结果尚待确认，请等待任务记录更新；再次确认使用相同请求编号。</p>}
    <div className="research-form"><label>分析记录<select value={jobId} disabled={!!edit} onChange={e => setJobId(e.target.value)}><option value="">选择一条记录</option>{jobs.map(j => <option key={j.id} value={j.id}>{taskLabels[j.status]} · {j.params.filenames?.join('、')} · {new Date(j.created_at).toLocaleString()}</option>)}</select></label></div>
    {job && <><div className="research-row"><strong role="status">{job.stage}</strong>{activeTask(job) ? <button className="secondary" disabled={busy} onClick={() => { setBusy(true); void client.cancelAnalysis(job.id).then(put).catch(report).finally(() => { if (alive.current) setBusy(false); }); }}>停止后续分析</button> : <button className="secondary" disabled={!cards.length} onClick={() => void client.analysisFile(job.id).then(b => saveBlob(b, 'PaperTrail-论文对比.txt')).catch(report)}>导出卡片与原文 TXT</button>}</div>{job.error && <p className="error-box">{job.error}</p>}</>}
    {edit && <form className="research-edit research-form" onSubmit={e => { e.preventDefault(); void saveEdit(); }}><label>人工笔记（独立保存，不覆盖 AI 原始整理）<textarea value={edit.text} maxLength={6000} rows={5} onChange={e => setEdit({ ...edit, text: e.target.value })} /></label><div className="research-row"><button className="primary" disabled={busy}>保存人工笔记</button><button className="secondary" type="button" disabled={busy} onClick={() => void saveEdit(true)}>移除人工笔记</button><button className="text-button" type="button" disabled={busy} onClick={() => setEdit(null)}>取消</button></div></form>}
    {cards.length > 0 ? <div className="research-table-scroll"><table className="research-table"><caption>阅读维度对比 · 每篇论文的引用编号独立</caption><thead><tr><th>维度</th>{cards.map(c => <th key={c.document_id}>{c.filename}</th>)}</tr></thead><tbody>{Object.keys(cards[0].fields).map(key => <tr key={key}><th scope="row">{cards[0].fields[key].label}</th>{cards.map(card => { const field = card.fields[key]; return <td key={card.document_id}><p className={field.missing ? 'research-muted' : 'research-prewrap'}>{field.text}</p>{key in card.edits && <div className="research-human"><strong>人工笔记 · 未自动验证</strong><p className="research-prewrap">{card.edits[key]}</p></div>}<details><summary>原文依据（{field.source_ids.length}）</summary>{card.evidence.filter(e => field.source_ids.includes(e.id)).map(e => <div className="research-source" key={e.id}><blockquote>{e.quote}</blockquote><button className="text-button" onClick={() => { if (!edit || window.confirm('放弃未保存的人工笔记并打开原文？')) onOpen(e); }}>第 {e.page} 页 [{e.id}] · 查看 PDF</button></div>)}</details>{job && !activeTask(job) && <button className="text-button" disabled={busy || !!edit} onClick={() => setEdit({ document: card.document_id, field: key, text: card.edits[key] ?? field.text, revision: job.revision })}>编辑人工笔记</button>}</td>; })}</tr>)}</tbody></table></div> : <p className="research-empty">选择论文生成卡片，或打开已有记录。对比表按研究问题、方法、数据、结论和局限排列。</p>}
    <p className="research-muted">基于检索片段，不能替代全文阅读。“未找到相关说明”不等于论文没有该内容。系统校验引用编号，具体表述仍需核对原文。</p>
  </Modal>;
}
