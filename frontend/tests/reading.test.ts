import test from "node:test";
import assert from "node:assert/strict";
import { ReadingSaver, filterReading, readingOf } from "../src/reading.ts";
import { RagApi } from "../src/api.ts";
import type { Paper } from "../src/types.ts";

const paper: Paper = { document_id: "paper", filename: "UV.pdf", pages: 10, chunk_count: 20, status: "ready", created_at: "2026-11-12" };

test("旧列表默认待读；筛选同时匹配阅读状态和文件名且不修改原列表", () => {
  assert.equal(readingOf(paper).status, "unread");
  const reading: Paper = { ...paper, document_id: "reading", filename: "Radar.pdf", reading: { ...readingOf(paper), status: "reading", last_page: 3, furthest_page: 6, progress_percent: 60 } };
  const done: Paper = { ...paper, document_id: "done", reading: { ...readingOf(paper), status: "completed" } };
  const list = [paper, reading, done];
  assert.deepEqual(filterReading(list, " UV ", "unread"), [paper]);
  assert.deepEqual(filterReading(list, "rad", "reading"), [reading]);
  assert.deepEqual(filterReading(list, "rad", "completed"), []);
  assert.deepEqual(filterReading(list, "", "all"), list);
  assert.equal(list.length, 3);
});

test("快速翻页顺序保存，只保留最新待保存页，关闭等待最终保存完成", async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const writes: number[] = [];
  const saver = new ReadingSaver(async (page) => { writes.push(page); if (page === 1) await gate; });
  const first = saver.record(1);
  saver.record(2);
  saver.record(3);
  const flush = saver.flush();
  assert.deepEqual(writes, [1]);
  release();
  await Promise.all([first, flush]);
  assert.deepEqual(writes, [1, 3]);
  await saver.record(3); // Zoom/redraw of the same page is not a new visit.
  assert.deepEqual(writes, [1, 3]);
  await saver.record(1); // Going backward updates the resume page.
  assert.deepEqual(writes, [1, 3, 1]);
});

test("保存失败保留页码，显式重试后再确认成功", async () => {
  const writes: number[] = [];
  const saver = new ReadingSaver(async (page) => { writes.push(page); if (writes.length === 1) throw Error("offline"); });
  await assert.rejects(saver.record(4), /offline/);
  assert.deepEqual(writes, [4]);
  await saver.flush();
  assert.deepEqual(writes, [4, 4]);
});

test("旧页保存失败后重试最新页，不把旧页覆盖到最新位置", async () => {
  let reject!: (error: Error) => void;
  const gate = new Promise<void>((_, fail) => { reject = fail; });
  const writes: number[] = [];
  const saver = new ReadingSaver(async (page) => { writes.push(page); if (writes.length === 1) await gate; });
  const result = saver.record(1);
  saver.record(5).catch(() => undefined);
  reject(Error("offline"));
  await assert.rejects(result, /offline/);
  await saver.flush();
  assert.deepEqual(writes, [1, 5]);
});

test("阅读请求带认证，用 PATCH 保存页码或状态，令牌不进入地址", async (t) => {
  const bodies: unknown[] = [];
  t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
    assert.equal(url, "/api/documents/paper%2Fone/reading");
    assert.equal(new Headers(init.headers).get("X-API-Key"), "reading-token");
    if (init.method) {
      assert.equal(init.method, "PATCH");
      assert.equal(new Headers(init.headers).get("Content-Type"), "application/json");
      bodies.push(JSON.parse(String(init.body)));
    }
    return Response.json(readingOf(paper));
  });
  const api = new RagApi("reading-token");
  await api.reading("paper/one");
  await api.saveReading("paper/one", { page: 3 });
  await api.saveReading("paper/one", { status: "completed" });
  assert.deepEqual(bodies, [{ page: 3 }, { status: "completed" }]);
});

test("保存完成与下一次翻页相邻时，flush 仍等待最后一页", async () => {
  const writes: number[] = [];
  const saver = new ReadingSaver(async (page) => { writes.push(page); });
  const first = saver.record(1);
  await Promise.resolve();
  const next = saver.record(2);
  await Promise.all([first, next]);
  assert.deepEqual(writes, [1, 2]);
});
