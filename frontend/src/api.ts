import type { Answer, Paper, Question } from "./types.ts";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function errorDetail(body: unknown, status: number): string {
  const detail =
    body && typeof body === "object" && "detail" in body
      ? body.detail
      : undefined;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((x) => (typeof x?.msg === "string" ? x.msg : "请求参数不正确"))
      .join("；");
  const messages: Record<number, string> = {
    401: "访问令牌无效，请重新连接。",
    404: "论文或接口不存在，请刷新列表后重试。",
    413: "文件太大，请选择不超过 30 MiB 的 PDF。",
    415: "请上传 PDF 文件。",
    502: "后端或模型服务暂时不可用，请检查后端终端。",
  };
  return messages[status] || `请求失败（HTTP ${status}），请查看后端日志。`;
}

export class RagApi {
  token: string;
  base: string;
  private controllers = new Set<AbortController>();
  private disposed = false;
  constructor(token: string, base = "") {
    this.token = token;
    this.base = base.replace(/\/$/, "");
  }
  dispose() {
    this.disposed = true;
    this.controllers.forEach((c) => c.abort());
    this.controllers.clear();
  }
  async request<T>(
    path: string,
    init: RequestInit = {},
    timeout = 20000,
    blob = false,
  ): Promise<T> {
    if (this.disposed) throw new ApiError("连接已关闭。");
    const controller = new AbortController();
    this.controllers.add(controller);
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeout);
    try {
      const headers = new Headers(init.headers);
      headers.set("X-API-Key", this.token);
      const response = await fetch(this.base + path, {
        ...init,
        headers,
        signal: controller.signal,
        cache: "no-store",
      });
      if (!response.ok) {
        let body: unknown;
        try {
          body = await response.json();
        } catch {
          body = null;
        }
        throw new ApiError(errorDetail(body, response.status), response.status);
      }
      return (await (blob ? response.blob() : response.json())) as T;
    } catch (err) {
      if (err instanceof ApiError) throw err;
      if (timedOut)
        throw new ApiError(
          "等待超时。后端可能仍在处理，请查看终端；上传时先刷新列表确认结果。",
        );
      if (controller.signal.aborted) throw new ApiError("已结束本次等待。");
      throw new ApiError(
        "无法连接后端。请确认 run_backend.py 正在运行，且端口为 8000。",
      );
    } finally {
      clearTimeout(timer);
      this.controllers.delete(controller);
    }
  }
  documents() {
    return this.request<{ documents: Paper[] }>("/api/documents");
  }
  health() {
    return this.request<{ status: string; version: string }>("/api/health");
  }
  upload(file: File) {
    const form = new FormData();
    form.append("file", file);
    return this.request<Paper>(
      "/api/documents",
      { method: "POST", body: form },
      600000,
    );
  }
  delete(id: string) {
    return this.request<{ deleted: boolean }>(
      `/api/documents/${encodeURIComponent(id)}`,
      { method: "DELETE" },
      120000,
    );
  }
  chat(body: Question) {
    return this.request<Answer>(
      "/api/chat",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      300000,
    );
  }
  pdf(id: string) {
    return this.request<Blob>(
      `/api/documents/${encodeURIComponent(id)}/file`,
      {},
      120000,
      true,
    );
  }
}

export function emptyPages(paper: Paper): number[] {
  try {
    const x =
      typeof paper.empty_pages === "string"
        ? JSON.parse(paper.empty_pages)
        : paper.empty_pages;
    return Array.isArray(x)
      ? x.filter((n) => Number.isInteger(n) && n > 0)
      : [];
  } catch {
    return [];
  }
}

export function makeQuestion(
  question: string,
  topK: number,
  scope: "all" | "selected",
  selected: string[],
): Question {
  if (!question.trim()) throw new ApiError("先写下你的问题。");
  if (question.trim().length > 2000)
    throw new ApiError("问题请控制在 2000 字以内。");
  if (scope === "selected" && !selected.length)
    throw new ApiError("请至少选择一篇论文，或切换到全部论文。");
  if (scope === "selected" && selected.length > 100)
    throw new ApiError("指定范围最多选择 100 篇论文。");
  return {
    question: question.trim(),
    top_k: topK,
    document_ids: scope === "all" ? null : [...selected],
  };
}
