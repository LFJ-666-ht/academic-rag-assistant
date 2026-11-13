import test from "node:test";
import assert from "node:assert/strict";
import { RagApi } from "../src/api.ts";
import type { TranslationSubmission } from "../src/types.ts";

test("翻译确认复用同一请求编号，携带页码/语言与鉴权", async (t) => {
  const body: TranslationSubmission = { request_id: "request-uuid", document_id: "paper-uuid", start_page: 2, end_page: 5, target_language: "zh" };
  const calls: string[] = [];
  t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
    assert.equal(url, "/api/translations");
    assert.equal(init.method, "POST");
    assert.equal(new Headers(init.headers).get("X-API-Key"), "private-token");
    assert.equal(new Headers(init.headers).get("Content-Type"), "application/json");
    assert.deepEqual(JSON.parse(String(init.body)), body);
    calls.push(String(init.body));
    return Response.json({ id: "job-uuid", ...body });
  });
  const api = new RagApi("private-token");
  api.pendingTranslationSubmission = body;
  await api.translate(api.pendingTranslationSubmission);
  await api.translate(api.pendingTranslationSubmission);
  assert.equal(calls[0], calls[1]);
  assert.ok(!calls[0].includes("private-token"));
});

test("原文页、历史、详情、停止与导出均使用鉴权且不暴露令牌", async (t) => {
  const paths: string[] = [];
  t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
    paths.push(url);
    assert.equal(new Headers(init.headers).get("X-API-Key"), "private-token");
    assert.ok(!url.includes("private-token"));
    if (url.endsWith("/cancel")) assert.equal(init.method, "POST");
    if (url.endsWith("/file")) return new Response("【原文】source\n【译文】译文");
    return Response.json({});
  });
  const api = new RagApi("private-token");
  await api.sourcePage("paper/one", 2);
  await api.translationConfig();
  await api.translations();
  await api.translation("job/one");
  await api.cancelTranslation("job/one");
  assert.match(await (await api.translationFile("job/one")).text(), /【译文】/);
  assert.deepEqual(paths, ["/api/documents/paper%2Fone/pages/2", "/api/translations/config", "/api/translations", "/api/translations/job%2Fone", "/api/translations/job%2Fone/cancel", "/api/translations/job%2Fone/file"]);
});
