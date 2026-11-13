import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { RagApi } from './api';
import type { Paper } from './types';
import type { PdfTarget } from './PdfViewer';
import Modal from './Modal';
import { requestId, saveBlob } from './research';
import type { Note } from './research';
import { useResearchClient } from './useResearchClient';
import './research.css';

export default function NotesPanel({ api: session, papers, initial, onClose, onOpen, onUnauthorized }: {
  api: RagApi; papers: Paper[]; initial?: PdfTarget; onClose: () => void; onOpen: (target: PdfTarget) => void; onUnauthorized: () => void;
}) {
  const { api, client, error, setError, alive, report } = useResearchClient(session, onUnauthorized);
  const [notes, setNotes] = useState<Note[]>([]), [filter, setFilter] = useState(''), [search, setSearch] = useState('');
  const [did, setDid] = useState(initial?.id ?? papers[0]?.document_id ?? ''), [page, setPage] = useState(initial?.page ?? 1);
  const [kind, setKind] = useState<'note' | 'bookmark'>('note'), [text, setText] = useState(''), [quote, setQuote] = useState(''), [tags, setTags] = useState('');
  const [editing, setEditing] = useState<Note | null>(null), [busy, setBusy] = useState(false), [notice, setNotice] = useState(''), [source, setSource] = useState('');
  const id = useRef(requestId());
  const paper = papers.find(p => p.document_id === did);
  const dirty = Boolean(text || quote || tags || editing);
  async function load() { try { const r = await client.notes(); if (alive.current) setNotes(r.notes); } catch (e) { report(e); } }
  useEffect(() => { void load(); }, [client]);
  useEffect(() => { setSource(''); }, [did, page]);
  function reset() { setEditing(null); setText(''); setQuote(''); setTags(''); setKind('note'); id.current = requestId(); }
  function leave() { if (!dirty || window.confirm('放弃未保存的笔记内容？')) onClose(); }
  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    try {
      await client.saveNote({ id: editing?.id ?? id.current, document_id: did, page, kind, text, quote, tags: tags.split(/[,，]/).map(t => t.trim()).filter(Boolean), revision: editing?.revision ?? 0 });
      if (alive.current) { reset(); setNotice('已保存到阅读笔记'); await load(); }
    } catch (e) { report(e); } finally { if (alive.current) setBusy(false); }
  }
  async function readSource() {
    setBusy(true); try { const p = await api.sourcePage(did, page); if (alive.current) setSource(p.original || '本页没有可提取文本'); } catch (e) { report(e); } finally { if (alive.current) setBusy(false); }
  }
  async function remove(note: Note) {
    if (!window.confirm('删除这条笔记或书签？')) return;
    setBusy(true); try { await client.deleteNote(note.id); if (editing?.id === note.id) reset(); await load(); } catch (e) { report(e); } finally { if (alive.current) setBusy(false); }
  }
  const shown = notes.filter(n => (!filter || n.document_id === filter) && `${n.filename} ${n.text} ${n.quote} ${n.tags.join(' ')}`.toLowerCase().includes(search.toLowerCase()));
  return <Modal className="research-dialog" labelId="notes-title" onClose={leave} busy={busy}>
    <header className="research-header"><div><h2 id="notes-title">阅读笔记与书签</h2><p>记录想法，回到对应原文</p></div><button className="secondary" disabled={busy} onClick={leave}>关闭</button></header>
    {error && <div className="error-box" role="alert">{error}<button className="text-button" onClick={() => void load()}>刷新记录</button></div>}
    {notice && <p role="status">{notice}</p>}
    <div className="research-columns"><section>
      <form className="research-form" onSubmit={save}>
        <h3>{editing ? '编辑记录' : '添加记录'}</h3>
        <label>论文<select value={did} disabled={busy || !!editing} onChange={e => { setDid(e.target.value); setPage(1); }}>{papers.map(p => <option key={p.document_id} value={p.document_id}>{p.filename}</option>)}</select></label>
        <div className="research-row"><label>物理页码<input type="number" min={1} max={paper?.pages ?? 1} value={page} required disabled={busy} onChange={e => setPage(Number(e.target.value))} /></label><label>类型<select value={kind} disabled={busy} onChange={e => setKind(e.target.value as 'note' | 'bookmark')}><option value="note">阅读笔记</option><option value="bookmark">页码书签</option></select></label></div>
        <label>我的笔记<textarea rows={4} maxLength={10000} value={text} required={kind === 'note'} disabled={busy} onChange={e => setText(e.target.value)} placeholder="这个方法为什么有用？有哪些疑问？" /></label>
        <label>原文摘录（可选）<textarea rows={3} maxLength={6000} value={quote} disabled={busy} onChange={e => setQuote(e.target.value)} placeholder="粘贴对应页的原文，方便复核" /></label>
        <button className="text-button" type="button" disabled={!paper || busy} onClick={() => void readSource()}>查看本页可复制原文</button>
        {source && <details open><summary>第 {page} 页文本</summary><pre className="source-preview">{source}</pre></details>}
        <label>标签（用逗号分隔，最多 10 个）<input value={tags} disabled={busy} onChange={e => setTags(e.target.value)} placeholder="研究方法, 待读, 灵感" /></label>
        <div className="research-row"><button className="primary" disabled={busy || !paper}>{busy ? '正在保存…' : '保存记录'}</button><button type="button" className="secondary" disabled={busy} onClick={() => { if (!dirty || window.confirm('放弃当前编辑？')) reset(); }}>新建记录</button></div>
      </form>
    </section><section>
      <div className="research-row"><h3>已保存 {notes.length} 条</h3><button className="secondary small" onClick={() => void client.notesFile(filter).then(b => saveBlob(b, 'PaperTrail-阅读笔记.txt')).catch(report)}>导出 TXT</button></div>
      <div className="research-form"><label>筛选论文<select value={filter} onChange={e => setFilter(e.target.value)}><option value="">全部论文</option>{papers.map(p => <option key={p.document_id} value={p.document_id}>{p.filename}</option>)}</select></label><input aria-label="搜索笔记与标签" value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索笔记、摘录或标签" /></div>
      {!shown.length && <p className="research-muted">还没有符合条件的记录。读到重要页面时，记下第一条想法。</p>}
      {shown.map(note => <article key={note.id} className="research-note"><strong>{note.filename} · 第 {note.page} 页 · {note.kind === 'bookmark' ? '书签' : '笔记'}</strong><p className="research-prewrap">{note.text}</p>{note.quote && <blockquote>{note.quote}</blockquote>}<p className="research-muted">{note.tags.join(' · ')}</p><div className="research-row"><button className="text-button" disabled={busy} onClick={() => { if (!dirty || window.confirm('放弃当前未保存内容并打开原文？')) onOpen({ id: note.document_id, filename: note.filename, page: note.page }); }}>查看原文</button><button className="text-button" disabled={busy} onClick={() => { if (dirty && !window.confirm('放弃当前编辑？')) return; setEditing(note); setDid(note.document_id); setPage(note.page); setText(note.text); setQuote(note.quote); setTags(note.tags.join(', ')); setKind(note.kind); }}>编辑</button><button className="text-button" disabled={busy} onClick={() => void remove(note)}>删除</button></div></article>)}
    </section></div>
  </Modal>;
}
