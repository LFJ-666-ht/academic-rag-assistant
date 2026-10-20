# React + TypeScript 接口约定

开发地址 `http://127.0.0.1:8000`。除健康检查外所有请求带 `X-API-Key`；后端从令牌确定用户，不接受前端自行声明 user_id。正式登录系统尚未实现。不要在 `VITE_*` 中配置 DeepSeek 密钥。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /api/health | 存活检查，不表示模型或 DeepSeek 已连通 |
| POST | /api/documents | multipart/form-data，字段 file，单篇 PDF |
| GET | /api/documents | 返回当前用户 documents 列表 |
| DELETE | /api/documents/{uuid} | 删除原件、片段及向量 |
| GET | /api/documents/{uuid}/file | 返回原始 PDF，需要认证 |
| POST | /api/search | 仅检索，排查召回与重排效果 |
| POST | /api/chat | 检索并生成答案 |

搜索和问答请求：

```json
{"question":"论文提出的方法及其局限是什么？","top_k":5,"document_ids":null}
```

`document_ids: null` 或省略表示全部自己的论文；空数组表示不选任何论文；UUID 数组表示只搜所选文献。不存在或属于他人的 UUID 返回 404。

问答响应类型：

```typescript
export interface Evidence {
  id: number;
  document_id: string;
  chunk_id: string;
  filename: string;
  page: number; // PDF 物理页码，从 1 开始
  quote: string;
  pdf_url: string; // 相对 API 地址，含 #page=N
}
export interface ChatResponse {
  answer: string;
  citations: Evidence[];
  evidence: Evidence[];
  citation_status: 'ids_valid' | 'missing_citations' | 'no_evidence';
}
```

建议前端先做三栏：左侧论文列表与上传，中间问答，右侧原文证据；支持多选论文、删除确认、错误提示、等待状态。以普通文本渲染原文，Markdown 答案如启用 HTML 必须净化。引用卡展示文件名、页码、原文，不把 rerank_score 当成概率或可信度。

上传示例：

```typescript
const form = new FormData();
form.append('file', file);
const response = await fetch(`${apiBase}/api/documents`, {
  method: 'POST',
  headers: {'X-API-Key': accessToken},
  body: form,
}); // 不要手动设置 multipart Content-Type，让浏览器生成 boundary
if (!response.ok) throw new Error(await response.text());
const document = await response.json();
```

原文链接不能直接用 `<a href>` 或 iframe 访问，因为不会自动附加 X-API-Key。先带认证 fetch 获取 Blob，再展示 Blob URL：

```typescript
const response = await fetch(`${apiBase}/api/documents/${citation.document_id}/file`, {
  headers: {'X-API-Key': accessToken},
});
if (!response.ok) throw new Error('原文读取失败');
const objectUrl = URL.createObjectURL(await response.blob());
// 将 `${objectUrl}#page=${citation.page}` 放入 PDF 预览 iframe 的 src。
// 页面关闭/切换文件时调用 URL.revokeObjectURL(objectUrl)。
```

不要把访问令牌放到 URL 查询参数。演示阶段允许用户手动输入令牌，暂存内存；登录模块完成后再换成正式会话管理。

错误码：401 令牌无效；404 文件不可见；413 文件超限；415 非 PDF 扩展名；422 参数/PDF 不合法；502 DeepSeek 失败或引用编号非法；500 内部异常。FastAPI 的校验 detail 可能为数组，前端需兼容字符串和数组两种形式。

论文返回字段还包括 `pages`、`chunk_count`、`status`、`created_at`（UTC）、`duplicate`（仅上传响应）、`empty_pages`（JSON 字符串格式的空文本页码列表）。将空页提醒展示为“这些页尚未进行 OCR”，不要误报整篇解析完成。
