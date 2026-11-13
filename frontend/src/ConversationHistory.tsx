import { useEffect, useState } from 'react';
import type { RagApi } from './api';
import type { Conversation } from './research';
import Modal from './Modal';
import { useResearchClient } from './useResearchClient';
import './research.css';
export default function ConversationHistory({ api, onClose, onSelect, onDeleted, onUnauthorized }: { api: RagApi; onClose: () => void; onSelect: (id: string) => void; onDeleted: (id: string) => void; onUnauthorized: () => void }) {
  const { client, error, report, alive } = useResearchClient(api, onUnauthorized);
  const [items, setItems] = useState<Conversation[]>([]), [search, setSearch] = useState(''), [busy, setBusy] = useState(false);
  async function load() { try { const r = await client.conversations(); if (alive.current) setItems(r.conversations); } catch (e) { report(e); } }
  useEffect(() => { void load(); }, [client]);
  async function change(item: Conversation, remove = false) {
    const title = remove ? null : window.prompt('会话名称', item.title);
    if (remove ? !window.confirm('删除这段问答记录？论文和笔记会保留。') : !title?.trim()) return;
    setBusy(true);
    try { if (remove) { await client.deleteConversation(item.id); onDeleted(item.id); } else await client.renameConversation(item.id, title!.trim()); await load(); } catch (e) { report(e); } finally { if (alive.current) setBusy(false); }
  }
  return <Modal className="research-dialog compact" labelId="history-title" onClose={onClose} busy={busy}>
    <header className="research-header"><div><h2 id="history-title">问答记录</h2><p>会话按用户保存在当前知识库；刷新后重新连接即可查看</p></div><button className="secondary" onClick={onClose} disabled={busy}>关闭</button></header>
    {error && <div className="error-box" role="alert">{error}</div>}
    <div className="research-form"><input aria-label="搜索问答记录" placeholder="搜索问题或论文名称" value={search} onChange={e => setSearch(e.target.value)} /></div>
    {!items.length && <p className="research-muted">还没有已保存的会话。从工作台发送一个问题开始。</p>}
    {items.filter(i => (i.title + i.filenames.join(' ')).toLowerCase().includes(search.toLowerCase())).map(item => <article className="research-note" key={item.id}><button className="text-button" disabled={busy} onClick={() => onSelect(item.id)}>{item.title}</button><p className="research-muted">{item.filenames.join('、')} · {new Date(item.updated_at).toLocaleString()}</p><div className="research-row"><button className="text-button" disabled={busy} onClick={() => void change(item)}>重命名</button><button className="text-button" disabled={busy} onClick={() => void change(item, true)}>删除会话</button></div></article>)}
  </Modal>;
}
