import test from "node:test";
import assert from "node:assert/strict";
import { RagApi, ApiError, emptyPages, makeQuestion } from "../src/api.ts";

test("全部论文发 null；指定范围发送真实选择；空选择不发送全库请求", () => {
  assert.equal(makeQuestion("  问题  ", 5, "all", []).document_ids, null);
  assert.deepEqual(
    makeQuestion("问题", 5, "selected", ["doc-1"]).document_ids,
    ["doc-1"],
  );
  assert.throws(() => makeQuestion("问题", 5, "selected", []), /至少选择/);
  assert.throws(() => makeQuestion(" ", 5, "all", []));
  assert.throws(() => makeQuestion("x".repeat(2001), 5, "all", []));
  assert.throws(
    () => makeQuestion("问题", 5, "selected", Array(101).fill("id")),
    /100/,
  );
});

test("问答请求使用 X-API-Key，令牌不进入 URL 或 JSON", async (t) => {
  const token = "test-only-access-token";
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
    calls++;
    assert.equal(url, "/api/chat");
    assert.equal(new Headers(init.headers).get("X-API-Key"), token);
    assert.equal(
      new Headers(init.headers).get("Content-Type"),
      "application/json",
    );
    assert.deepEqual(JSON.parse(String(init.body)), {
      question: "方法是什么？",
      top_k: 5,
      document_ids: null,
    });
    assert.ok(!String(init.body).includes(token));
    return Response.json({
      answer: "测试",
      citations: [],
      evidence: [],
      citation_status: "no_evidence",
    });
  });
  await new RagApi(token).chat(makeQuestion("方法是什么？", 5, "all", []));
  assert.equal(calls, 1);
});

test("上传由浏览器设置 multipart boundary；鉴权头保留", async (t) => {
  const file = new File(["%PDF-test"], "论文.pdf", { type: "application/pdf" });
  t.mock.method(globalThis, "fetch", async (_: string, init: RequestInit) => {
    assert.equal(init.method, "POST");
    assert.ok(init.body instanceof FormData);
    assert.equal((init.body.get("file") as File).name, file.name);
    assert.equal(new Headers(init.headers).has("Content-Type"), false);
    assert.equal(new Headers(init.headers).get("X-API-Key"), "test-token");
    return Response.json({ duplicate: true });
  });
  const result = await new RagApi("test-token").upload(file);
  assert.equal(result.duplicate, true);
});

test("PDF 原文通过鉴权 fetch 获取 Blob", async (t) => {
  t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
    assert.equal(url, "/api/documents/uuid/file");
    assert.equal(new Headers(init.headers).get("X-API-Key"), "token");
    return new Response("%PDF-test", {
      headers: { "Content-Type": "application/pdf" },
    });
  });
  const blob = await new RagApi("token").pdf("uuid");
  assert.equal(await blob.text(), "%PDF-test");
});

test("FastAPI 数组校验错误转成可读消息", async (t) => {
  t.mock.method(globalThis, "fetch", async () =>
    Response.json({ detail: [{ msg: "输入必须是 UUID" }] }, { status: 422 }),
  );
  await assert.rejects(
    new RagApi("token").documents(),
    (e: unknown) =>
      e instanceof ApiError &&
      e.status === 422 &&
      e.message === "输入必须是 UUID",
  );
});

test("401 可识别，非 JSON 网关错误不向页面输出 HTML", async (t) => {
  t.mock.method(globalThis, "fetch", async () =>
    Response.json({ detail: "令牌无效" }, { status: 401 }),
  );
  await assert.rejects(
    new RagApi("token").documents(),
    (e: unknown) => e instanceof ApiError && e.status === 401,
  );
  t.mock.method(
    globalThis,
    "fetch",
    async () => new Response("<html>proxy</html>", { status: 502 }),
  );
  await assert.rejects(
    new RagApi("token").documents(),
    (e: unknown) => e instanceof ApiError && !e.message.includes("<html>"),
  );
});

test("超时终止等待并提示后端可能继续运行", async (t) => {
  t.mock.method(
    globalThis,
    "fetch",
    (_: string, init: RequestInit) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener(
          "abort",
          () => reject(new Error("aborted")),
          { once: true },
        );
      }),
  );
  await assert.rejects(
    new RagApi("token").request("/test", {}, 15),
    /后端可能仍在处理/,
  );
});

test("断开会话中止在途请求，禁止旧连接再次发送请求", async (t) => {
  t.mock.method(
    globalThis,
    "fetch",
    (_: string, init: RequestInit) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener(
          "abort",
          () => reject(new Error("aborted")),
          { once: true },
        );
      }),
  );
  const api = new RagApi("token");
  const request = api.documents();
  api.dispose();
  await assert.rejects(request, /结束本次等待/);
  await assert.rejects(api.documents(), /连接已关闭/);
});

test("网络不可达给出启动后端提示", async (t) => {
  t.mock.method(globalThis, "fetch", async () => {
    throw new TypeError("Failed to fetch");
  });
  await assert.rejects(new RagApi("token").documents(), /run_backend.py/);
});

test("空文本页兼容后端 JSON 字符串和数组，不因异常格式崩溃", () => {
  const paper = {
    document_id: "id",
    filename: "x.pdf",
    pages: 3,
    chunk_count: 2,
    status: "ready",
    created_at: "",
  };
  assert.deepEqual(emptyPages({ ...paper, empty_pages: "[2,3]" }), [2, 3]);
  assert.deepEqual(emptyPages({ ...paper, empty_pages: [1] }), [1]);
  assert.deepEqual(emptyPages({ ...paper, empty_pages: "invalid" }), []);
});

test("配图任务携带固定请求编号，确认提交时复用编号，不发送平台密钥", async (t) => {
  const body = { request_id: "request-id", prompt: "概念插画", draft_id: "draft-id" };
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
    calls++;
    assert.equal(url, "/api/images");
    assert.equal(init.method, "POST");
    assert.deepEqual(JSON.parse(String(init.body)), body);
    assert.equal(new Headers(init.headers).get("X-API-Key"), "project-token");
    assert.equal(new Headers(init.headers).has("Authorization"), false);
    return Response.json({ id: "job-id", status: "queued", ...body });
  });
  const api = new RagApi("project-token");
  await api.generateImage(body);
  await api.generateImage(body);
  assert.equal(calls, 2);
});

test("配图提示词传递论文范围，已生成图片通过鉴权 Blob 读取", async (t) => {
  t.mock.method(globalThis, "fetch", async (url: string, init: RequestInit) => {
    assert.equal(new Headers(init.headers).get("X-API-Key"), "token");
    if (url === "/api/images/prompt") {
      assert.deepEqual(JSON.parse(String(init.body)).document_ids, ["paper-id"]);
      return Response.json({ draft_id: "draft", prompt: "prompt", evidence: [] });
    }
    assert.equal(url, "/api/images/job-id/file");
    return new Response("image-bytes", { headers: { "Content-Type": "image/png" } });
  });
  const api = new RagApi("token");
  assert.equal((await api.imagePrompt(makeQuestion("概念", 5, "selected", ["paper-id"]))).draft_id, "draft");
  const blob = await api.imageFile("job-id");
  assert.equal(blob.type, "image/png");
  assert.equal(await blob.text(), "image-bytes");
});
