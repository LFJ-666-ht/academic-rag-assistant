import type { Answer, Evidence, Paper, Turn } from './types.ts';
import { ApiError, RagApi } from './api.ts';

export const requestId = () => {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map(n => n.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};
export type Note = { id: string; document_id: string; filename: string; page: number; kind: 'note' | 'bookmark'; text: string; quote: string; tags: string[]; revision: number; created_at: string; updated_at: string };
export type SavedTurn = { id: string; question: string; status: string; answer: Answer | null; preview: string; error: string | null };
export type Conversation = { id: string; title: string; document_ids: string[]; filenames: string[]; updated_at: string; missing_document_ids?: string[]; turns?: SavedTurn[] };
export type Glossary = { language: 'zh' | 'en'; entries: { source: string; target: string }[]; revision: number; version: string };
export type CardField = { label: string; text: string; source_ids: number[]; missing: boolean };
export type ResearchCard = { document_id: string; filename: string; fields: Record<string, CardField>; evidence: Evidence[]; edits: Record<string, string> };
export type Task<R> = { id: string; kind: string; status: string; stage: string; params: { filename?: string; filenames?: string[]; document_ids?: string[] }; result: R; error: string | null; revision: number; created_at: string };
export type Analysis = Task<{ cards?: ResearchCard[] }>;
export const activeTask = (task: { status: string }) => ['queued', 'running'].includes(task.status);
export const taskLabels: Record<string, string> = { queued: '排队中', running: '进行中', succeeded: '已完成', failed: '未完成', cancelled: '已停止', interrupted: '已中断' };
export const jsonBody = (body: unknown, method = 'POST') => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const enc = encodeURIComponent;
export function turnsFromHistory(conv: Conversation): Turn[] {
  return (conv.turns ?? []).map(t => ({ id: t.id, question: t.question, scope: `会话文献 · ${conv.document_ids.length} 篇`, answer: t.answer ?? undefined, preview: t.answer ? undefined : t.preview || undefined,
    error: t.error ?? undefined, progress: t.status === 'running' ? '后端正在处理，可稍后读取记录' : undefined }));
}
export function parseGlossary(text: string): Glossary['entries'] {
  const entries = text.split('\n').filter(line => line.trim()).map((line, index) => {
    const at = line.indexOf('=');
    if (at < 1 || !line.slice(at + 1).trim()) throw new Error(`第 ${index + 1} 行请使用 原词=译法`);
    return { source: line.slice(0, at).trim(), target: line.slice(at + 1).trim() };
  });
  if (entries.length > 100 || new Set(entries.map(e => e.source.toLocaleLowerCase())).size !== entries.length) throw new Error('最多 100 个术语，原词不能重复');
  if (entries.some(e => e.source.length > 120 || e.target.length > 160)) throw new Error('原词最多 120 字，译法最多 160 字');
  return entries;
}
export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob); const a = document.createElement('a');
  a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export class ResearchApi {
  api: RagApi;
  constructor(api: RagApi) { this.api = api; }
  notes(did = '') { return this.api.request<{ notes: Note[] }>(`/api/notes${did ? '?document_id=' + enc(did) : ''}`); }
  saveNote(note: Pick<Note, 'id' | 'document_id' | 'page' | 'kind' | 'text' | 'quote' | 'tags' | 'revision'>) { return this.api.request<Note>('/api/notes', jsonBody(note)); }
  deleteNote(id: string) { return this.api.request(`/api/notes/${enc(id)}`, { method: 'DELETE' }); }
  notesFile(did = '') { return this.api.request<Blob>(`/api/notes/export${did ? '?document_id=' + enc(did) : ''}`, {}, 20000, true); }
  conversations() { return this.api.request<{ conversations: Conversation[] }>('/api/conversations'); }
  conversation(id: string) { return this.api.request<Conversation>(`/api/conversations/${enc(id)}`); }
  newConversation(id: string, ids: string[] | null) { return this.api.request<Conversation>('/api/conversations', jsonBody({ id, document_ids: ids })); }
  renameConversation(id: string, title: string) { return this.api.request(`/api/conversations/${enc(id)}`, jsonBody({ title }, 'PATCH')); }
  deleteConversation(id: string) { return this.api.request(`/api/conversations/${enc(id)}`, { method: 'DELETE' }); }
  glossary(language: 'zh' | 'en') { return this.api.request<Glossary>(`/api/glossaries/${language}`); }
  saveGlossary(language: 'zh' | 'en', entries: Glossary['entries'], revision: number) { return this.api.request<Glossary>(`/api/glossaries/${language}`, jsonBody({ entries, revision })); }
  analyses() { return this.api.request<{ jobs: Analysis[] }>('/api/analyses'); }
  analysis(id: string) { return this.api.request<Analysis>(`/api/analyses/${enc(id)}`); }
  analyze(request_id: string, document_ids: string[]) { return this.api.request<Analysis>('/api/analyses', jsonBody({ request_id, document_ids })); }
  cancelAnalysis(id: string) { return this.api.request<Analysis>(`/api/analyses/${enc(id)}/cancel`, { method: 'POST' }); }
  editCard(id: string, document_id: string, field: string, text: string | null, revision: number) { return this.api.request<Analysis>(`/api/analyses/${enc(id)}`, jsonBody({ document_id, field, text, revision }, 'PATCH')); }
  analysisFile(id: string) { return this.api.request<Blob>(`/api/analyses/${enc(id)}/file`, {}, 20000, true); }
  uploads() { return this.api.request<{ jobs: Task<Partial<Paper>>[] }>('/api/uploads'); }
  async upload(file: File, onStage: (stage: string) => void, alive: () => boolean) {
    const form = new FormData(); form.append('file', file); form.append('request_id', requestId());
    let job = await this.api.request<Task<Paper>>('/api/uploads', { method: 'POST', body: form }, 120000);
    while (activeTask(job) && alive()) {
      onStage(job.stage);
      await new Promise(resolve => setTimeout(resolve, 700));
      if (!alive()) throw new ApiError('已结束等待，后台入库继续');
      job = await this.api.request<Task<Paper>>(`/api/uploads/${enc(job.id)}`);
    }
    if (job.status !== 'succeeded') throw new ApiError(job.error || '入库未完成；请查看入库记录');
    return job.result;
  }
}
