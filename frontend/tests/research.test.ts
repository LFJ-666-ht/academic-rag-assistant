import test from 'node:test';
import assert from 'node:assert/strict';
import { RagApi } from '../src/api.ts';
import { ResearchApi, parseGlossary, requestId, turnsFromHistory } from '../src/research.ts';
import type { Conversation } from '../src/research.ts';
import type { Answer } from '../src/types.ts';

test('问答历史只把已确认的结果恢复为答案；中断草稿保留且不伪装成功', () => {
  const answer = { answer: '回答 [1]', citations: [], evidence: [], citation_status: 'ids_valid' } as Answer;
  const history: Conversation = { id: 'conversation', title: '测试', document_ids: ['doc'], filenames: ['a.pdf'], updated_at: '', turns: [
    { id: 'one', question: 'q', status: 'completed', answer, preview: '', error: null },
    { id: 'two', question: 'follow up', status: 'interrupted', answer: null, preview: 'draft [99]', error: 'interrupted' },
    { id: 'three', question: 'running', status: 'running', answer: null, preview: 'partial', error: null },
  ] };
  const result = turnsFromHistory(history);
  assert.equal(result[0].answer, answer); assert.equal(result[0].preview, undefined);
  assert.equal(result[1].answer, undefined); assert.equal(result[1].preview, 'draft [99]'); assert.equal(result[1].error, 'interrupted');
  assert.match(result[2].progress!, /后端/); assert.equal(result[2].id, 'three');
  assert.equal(history.turns![1].answer, null);
});

test('术语表解析保留等号后的完整译法并拒绝空白、重复及过长术语', () => {
  assert.deepEqual(parseGlossary(' UV = 紫外 \n\n value=a=b '), [{ source: 'UV', target: '紫外' }, { source: 'value', target: 'a=b' }]);
  for (const input of ['missing separator', 'UV=', '=target', 'UV=紫外\nuv=光', `${'x'.repeat(121)}=a`]) assert.throws(() => parseGlossary(input));
  assert.deepEqual(parseGlossary(''), []);
});

test('请求编号是独立 UUID，支持没有 randomUUID 的浏览器', () => {
  const a = requestId(), b = requestId();
  assert.match(a, /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/); assert.notEqual(a, b);
});

test('笔记请求携带乐观版本、真实页码和认证；导出不暴露令牌', async t => {
  const calls: string[] = [], note = { id: 'n', document_id: 'd', page: 3, kind: 'bookmark' as const, text: '', quote: '', tags: ['method'], revision: 2 };
  t.mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    calls.push(url); assert.equal(new Headers(init.headers).get('X-API-Key'), 'private-token'); assert.ok(!url.includes('private-token'));
    if (init.method === 'POST') assert.deepEqual(JSON.parse(String(init.body)), note);
    return url.includes('/export') ? new Response('notes') : Response.json({});
  });
  const client = new ResearchApi(new RagApi('private-token'));
  await client.saveNote(note); await client.notes('d/one'); await client.deleteNote('n/one');
  assert.equal(await (await client.notesFile('d/one')).text(), 'notes');
  assert.deepEqual(calls, ['/api/notes', '/api/notes?document_id=d%2Fone', '/api/notes/n%2Fone', '/api/notes/export?document_id=d%2Fone']);
});

test('连续追问发送固定会话和请求编号，不把前端历史或平台密钥放进正文', async t => {
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    calls++; assert.equal(url, '/api/conversations/c%2F1/stream');
    assert.deepEqual(JSON.parse(String(init.body)), { question: '它的局限？', top_k: 6, request_id: 'r1' });
    return new Response(JSON.stringify({ type: 'done', answer: { answer: '结果', citations: [], evidence: [] } }) + '\n');
  });
  const answer = await new RagApi('private').chatStream({ question: '它的局限？', top_k: 6, document_ids: ['irrelevant-to-existing-scope'] }, () => undefined, 1000, { id: 'c/1', requestId: 'r1' });
  assert.equal(calls, 1); assert.equal(answer.answer, '结果');
});

test('分析确认复用相同编号且人工编辑单独携带版本', async t => {
  const bodies: unknown[] = [];
  t.mock.method(globalThis, 'fetch', async (_url: string, init: RequestInit) => { bodies.push(JSON.parse(String(init.body))); return Response.json({}); });
  const api = new RagApi('private'), client = new ResearchApi(api);
  api.pendingAnalysisSubmission = { request_id: 'same', document_ids: ['a', 'b'] };
  for (let i = 0; i < 2; i++) await client.analyze(api.pendingAnalysisSubmission.request_id, api.pendingAnalysisSubmission.document_ids);
  await client.editCard('task', 'a', 'method', 'my note', 3);
  assert.deepEqual(bodies[0], bodies[1]);
  assert.deepEqual(bodies[2], { document_id: 'a', field: 'method', text: 'my note', revision: 3 });
});

test('翻译提交锁定术语快照，确认重试不改编号和版本', async t => {
  const bodies: unknown[] = [];
  t.mock.method(globalThis, 'fetch', async (_url: string, init: RequestInit) => { bodies.push(JSON.parse(String(init.body))); return Response.json({}); });
  const api = new RagApi('private');
  api.pendingTranslationSubmission = { request_id: 'same', document_id: 'd', start_page: 1, end_page: 1, target_language: 'zh', glossary_version: 'snapshot' };
  await api.translate(api.pendingTranslationSubmission); await api.translate(api.pendingTranslationSubmission);
  assert.deepEqual(bodies[0], bodies[1]); assert.equal((bodies[0] as {glossary_version: string}).glossary_version, 'snapshot');
});

test('入库上传保留 multipart boundary，并读取任务状态取得最终文献', async t => {
  let count = 0; const stages: string[] = [];
  t.mock.method(globalThis, 'fetch', async (url: string, init: RequestInit) => {
    count++;
    if (url === '/api/uploads') {
      assert.ok(init.body instanceof FormData); assert.equal(new Headers(init.headers).has('Content-Type'), false);
      assert.match(String(init.body.get('request_id')), /^[a-f0-9-]{36}$/);
      return Response.json({ id: 'job', status: 'running', stage: '计算文献向量', result: {} });
    }
    assert.equal(url, '/api/uploads/job');
    return Response.json({ id: 'job', status: 'succeeded', result: { document_id: 'paper', filename: 'a.pdf' } });
  });
  const client = new ResearchApi(new RagApi('private'));
  const paper = await client.upload(new File(['pdf'], 'a.pdf'), s => stages.push(s), () => true);
  assert.equal(paper.document_id, 'paper'); assert.deepEqual(stages, ['计算文献向量']); assert.equal(count, 2);
});
