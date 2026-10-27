# API 接口

本地服务地址为 `http://127.0.0.1:8000`。除健康检查外，以下业务接口均通过 `X-API-Key` 认证；后端根据令牌确定用户，不接受客户端自行声明 user_id。当前使用项目访问令牌，不包含账号注册或密码登录。模型平台密钥由后端管理，不通过 `VITE_*` 配置或下发客户端。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /api/health | 存活检查，不表示模型或 DeepSeek 已连通 |
| POST | /api/documents | multipart/form-data，字段 file，单篇 PDF |
| GET | /api/documents | 返回当前用户 documents 列表 |
| DELETE | /api/documents/{uuid} | 删除原件、片段及向量 |
| GET | /api/documents/{uuid}/file | 返回原始 PDF，需要认证 |
| POST | /api/search | 仅检索，排查召回与重排效果 |
| POST | /api/chat | 兼容原 JSON 问答，返回完整/不完整状态 |
| POST | /api/chat/stream | 带鉴权的 NDJSON 流式问答 |
| GET | /api/images/config | 配图是否配置、模型名、2K 尺寸；不返回密钥 |
| POST | /api/images/prompt | 检索论文后由 DeepSeek 整理绘图描述；请求同 Question |
| POST | /api/images | 提交单张 Seedream 配图任务，202 返回任务对象 |
| GET | /api/images | 当前用户最近 30 条图片任务 |
| GET | /api/images/{uuid} | 查询一个图片任务 |
| GET | /api/images/{uuid}/file | 鉴权下载成功任务的图片 Blob |

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

现有前端采用三栏：左侧论文列表与上传，中间问答，右侧原文证据；支持多选论文、删除确认、错误提示、等待状态。原文以普通文本渲染，Markdown 答案禁用原始 HTML。引用卡展示文件名、页码、原文，不把 rerank_score 当成概率或可信度。

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

访问令牌通过请求头传递，不进入 URL 查询参数。当前客户端允许手动输入项目访问令牌，并暂存于内存。

错误码：401 令牌无效；404 文件不可见；413 文件超限；415 非 PDF 扩展名；422 参数/PDF 不合法；502 DeepSeek 失败或引用编号非法；500 内部异常。FastAPI 的校验 detail 可能为数组，前端需兼容字符串和数组两种形式。

论文返回字段还包括 `pages`、`chunk_count`、`status`、`created_at`（UTC）、`duplicate`（仅上传响应）、`empty_pages`（JSON 字符串格式的空文本页码列表）。`empty_pages` 表示未提取到文本的页面，不代表已经完成 OCR。

## 双模型配图

`POST /api/images/prompt` 返回 `{draft_id, prompt, evidence}`，不调用图片模型。空知识库/空选择返回 422，其他用户的论文返回 404。DeepSeek 根据检索到的最多 30 个片段整理描述，不保证覆盖全文。前端固定取 5 个片段。

`POST /api/images` 请求：

```json
{"request_id":"前端生成的 UUID","prompt":"经用户检查的绘图描述","draft_id":null}
```

`draft_id` 可填整理草稿接口返回的 UUID，必须属于当前用户；也可为 null，直接使用用户描述。描述 1–6000 字，同一用户的同一 request_id 和相同参数始终返回同一任务；不同参数复用编号返回 409。请在网络超时后保留编号，不要自动生成新编号重试。每个用户最多 1 个未完成任务，服务器总计最多 8 个，超过返回 429。只支持单进程后端。

任务字段：`id, request_id, prompt, draft_id, model, size, status, error, evidence, file_url, created_at, updated_at`。状态为 `queued / running / succeeded / failed`，只有成功时 `file_url` 非空。时间为 UTC ISO 字符串。浏览器应带 `X-API-Key` 获取 Blob，不把令牌放进 URL。

缺少 Seedream 配置时 config.enabled=false，提交返回 422；原有问答正常工作。平台调用失败在任务的 status/error 中表示，提交接口的 202 不代表已经出图。服务端不自动重试图片生成。崩溃后遗留的 queued/running 任务在重启时标记失败，先核对平台调用记录再决定是否新建任务。

图片、草稿与来源快照只对其所有者可见。论文删除不会自动删除已生成图片及其来源快照；图片历史当前无删除界面，无自动过期清理，最近 30 条是列表窗口而非存储上限。

## 长回答与流式协议（v0.4.1）

Question 中 `top_k` 范围 1–30，省略默认 10。`POST /api/chat/stream` 接收相同请求体与 X-API-Key 头，响应 `application/x-ndjson`，每行一个 JSON 对象。它是 fetch 响应流，不是浏览器 EventSource。

| type | 字段 | 含义 |
|---|---|---|
| progress | message | 检索、等待或思考状态，无内部思考正文 |
| delta | text | 正文增量；此时只是尚未校验引用的草稿 |
| done | answer | 最终 Answer 对象，只有此事件代表接收完成 |
| error | message, status | 流已经开始后的错误；前端必须检查事件，不能只看 HTTP 200 |

鉴权和参数错误在流开始前返回 401 / 422；检索时发现无权访问的文档以 error/status=404 返回。未知引用编号始终被拦截，包括截断回答；前端移除该次未校验草稿。没有证据时跳过模型调用，done 中 citation_status=no_evidence。

Answer 增加可选字段：`finish_reason`（stop/length/interrupted）、`incomplete`、`warning`、`model`、`max_tokens`、`usage`。usage 只包含平台返回的数字统计，可能有 prompt_tokens/completion_tokens/total_tokens/reasoning_tokens；未提供时为空。平台 V4.1 的 completion_tokens 包含思考与正文，不是字数。

`length` 与有正文的上游断线保留结果，明确标记 incomplete。缺失完成标记的上游 EOF 不算成功；浏览器断线且没收到 done 时不得把 delta 拼接冒充已校验 Answer。前端不会在失败后自动重新提交问答或降级发起第二次模型请求。

原 `/api/chat` 使用同一适配器和引用校验，保留兼容；外部客户端仍需自行配置足够长的超时。配图接口不变。
