import { useEffect, useRef, useState } from 'react';
import type { RagApi } from './api';
import { ResearchApi, parseGlossary } from './research';
import type { Glossary } from './research';
export default function GlossaryEditor({ api, language, disabled, onVersion }: { api: RagApi; language: 'zh' | 'en'; disabled: boolean; onVersion: (version: string | null) => void }) {
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const [client] = useState(() => new ResearchApi(api));
  const [current, setCurrent] = useState<Glossary | null>(null), [text, setText] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => { let stopped = false; onVersion(null); setCurrent(null); setText(''); setError('');
    client.glossary(language).then(g => { if (!stopped) { if (!alive.current) return; setCurrent(g); setText(g.entries.map(e => `${e.source}=${e.target}`).join('\n')); onVersion(g.version); } }).catch(e => { if (!stopped) setError(e.message); });
    return () => { stopped = true; };
  }, [client, language, refresh]);
  async function save() {
    if (!current) return; setBusy(true); setError('');
    try { const entries = parseGlossary(text); const g = await client.saveGlossary(language, entries, current.revision); if (!alive.current) return; setCurrent(g); setText(g.entries.map(e => `${e.source}=${e.target}`).join('\n')); onVersion(g.version); }
    catch (e) { if (alive.current) setError(e instanceof Error ? e.message : '保存失败'); }
    finally { if (alive.current) setBusy(false); }
  }
  return <details className="glossary-editor"><summary>翻译术语表 · 版本 {current?.revision ?? '—'} · {current?.entries.length ?? 0} 个</summary><p className="translation-hint">每行 原词=译法。修改后先保存；新任务按当前版本翻译，已有译文保持原版本。</p><textarea aria-label="翻译术语表" rows={5} value={text} disabled={disabled || busy || !current} placeholder={'responsivity=响应度\nphotodetector=光电探测器'} onChange={e => { setText(e.target.value); onVersion(null); }} /><div className="research-row"><button type="button" className="secondary small" disabled={disabled || busy || !current} onClick={() => void save()}>保存术语表</button><button type="button" className="text-button" disabled={busy} onClick={() => { if (window.confirm('重新载入会放弃未保存的术语修改，继续？')) setRefresh(n => n + 1); }}>重新载入</button></div>{error && <p className="error-box" role="alert">{error}</p>}</details>;
}
