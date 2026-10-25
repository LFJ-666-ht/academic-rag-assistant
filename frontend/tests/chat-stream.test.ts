import test from "node:test";
import assert from "node:assert/strict";
import { RagApi, ApiError, makeQuestion } from "../src/api.ts";
import type { ChatEvent } from "../src/types.ts";
const body = makeQuestion("研究方法是什么？", 30, "all", []);
const answer = { answer: "结论 [1]", citations: [], evidence: [], citation_status: "missing_citations", incomplete: true, finish_reason: "length" };
const encoder = new TextEncoder();
function response(events: unknown[], lastNewline = true) {
  const bytes = encoder.encode(events.map(x => JSON.stringify(x)).join("\n") + (lastNewline ? "\n" : ""));
  return new Response(new ReadableStream({ start(c) {
    for (let i = 0; i < bytes.length; i += 5) c.enqueue(bytes.slice(i, i + 5));
    c.close();
  } }));
}
test("流式鉴权、范围、中文分片与截断状态正确；只发送一次", async (t) => {
  let calls = 0;
  const received: ChatEvent[] = [];
  t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
    calls++;
    assert.equal(url, "/api/chat/stream");
    assert.equal(new Headers(init.headers).get("X-API-Key"), "project-token");
    assert.deepEqual(JSON.parse(String(init.body)), body);
    return response([{ type: "progress", message: "深度思考" }, { type: "delta", text: "结论 [1]" }, { type: "done", answer }]);
  });
  assert.deepEqual(await new RagApi("project-token").chatStream(body, e => received.push(e)), answer);
  assert.deepEqual(received, [{ type: "progress", message: "深度思考" }, { type: "delta", text: "结论 [1]" }]);
  assert.equal(calls, 1);
});
test("最终事件没有换行仍能识别", async (t) => {
  t.mock.method(globalThis, "fetch", async () => response([{ type: "done", answer }], false));
  assert.deepEqual(await new RagApi("token").chatStream(body, () => {}), answer);
});
test("非法引用作为错误，不能冒充完成回答", async (t) => {
  t.mock.method(globalThis, "fetch", async () => response([{ type: "delta", text: "bad [999]" }, { type: "error", message: "不存在的引用编号", status: 502 }]));
  await assert.rejects(new RagApi("token").chatStream(body, () => {}), (e: unknown) => e instanceof ApiError && e.status === 502);
});
test("提前关闭时保留通知过的草稿，不当作成功", async (t) => {
  const events: ChatEvent[] = [];
  t.mock.method(globalThis, "fetch", async () => response([{ type: "delta", text: "部分正文" }]));
  await assert.rejects(new RagApi("token").chatStream(body, e => events.push(e)), /未收到完成确认/);
  assert.deepEqual(events, [{ type: "delta", text: "部分正文" }]);
});
test("401 不自动重试", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; return Response.json({ detail: "令牌无效" }, { status: 401 }); });
  await assert.rejects(new RagApi("token").chatStream(body, () => {}), (e: unknown) => e instanceof ApiError && e.status === 401);
  assert.equal(calls, 1);
});
test("空闲超时与断开会话取消请求", async (t) => {
  t.mock.method(globalThis, "fetch", async (_: string, init: RequestInit) => new Response(new ReadableStream({
    start(c) { init.signal?.addEventListener("abort", () => c.error(new Error("aborted")), { once: true }); },
  })));
  const api = new RagApi("token");
  await assert.rejects(api.chatStream(body, () => {}, 15), /长时间未收到/);
  const pending = api.chatStream(body, () => {});
  api.dispose();
  await assert.rejects(pending, /结束本次等待/);
  await assert.rejects(api.chatStream(body, () => {}), /连接已关闭/);
});
