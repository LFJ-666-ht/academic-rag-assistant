import { useEffect, useState } from 'react';
import type { RagApi } from './api';
import type { Paper } from './types';
import type { Task } from './research';
import { taskLabels } from './research';
import { useResearchClient } from './useResearchClient';
import Modal from './Modal';
import './research.css';
export default function UploadHistory({ api, onClose, onUnauthorized }: { api: RagApi; onClose: () => void; onUnauthorized: () => void }) {
  const { client, alive, error, report } = useResearchClient(api, onUnauthorized);
  const [items, setItems] = useState<Task<Partial<Paper>>[]>([]);
  useEffect(() => {
    let stopped = false; let timer: ReturnType<typeof setTimeout>;
    async function load() { try { const r = await client.uploads(); if (!stopped && alive.current) setItems(r.jobs); } catch (e) { if (!stopped) report(e); } finally { if (!stopped) timer = setTimeout(load, 1500); } }
    void load(); return () => { stopped = true; clearTimeout(timer); };
  }, [client]);
  return <Modal className="research-dialog compact" labelId="uploads-title" onClose={onClose}><header className="research-header"><div><h2 id="uploads-title">入库记录</h2><p>显示实际处理阶段，窗口关闭后已接收的任务仍会继续</p></div><button className="secondary" onClick={onClose}>关闭</button></header>{error && <div className="error-box" role="alert">{error}</div>}{!items.length && <p>还没有新版本的入库记录。</p>}{items.map(i => <article className="research-note" key={i.id}><strong>{i.params.filename}</strong><p>{taskLabels[i.status]} · {i.stage}</p>{i.error && <p role="alert">{i.error}</p>}</article>)}</Modal>;
}
