# API 接口

本地服务地址为 `http://127.0.0.1:8000`。除健康检查外，以下业务接口均通过 `X-API-Key` 认证；后端根据令牌确定用户，不接受客户端自行声明 user_id。当前使用项目访问令牌，不包含账号注册或密码登录。模型平台密钥由后端管理，不通过 `VITE_*` 配置或下发客户端。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /api/health | 存活检查，不表示模型或 DeepSeek 已连通 |
| POST | /api/documents | multipart/form-data，字段 file，单篇 PDF |
| GET | /api/documents | 返回当前用户 documents 列表 |
| DELETE | /api/documents/{uuid} | 删除原件、片段、向量、阅读进度及翻译记录 |
| GET | /api/documents/{uuid}/reading | 读取当前用户论文的阅读进度 |
| PATCH | /api/documents/{uuid}/reading | 保存翻阅页码或手动改变状态 |
| GET | /api/documents/{uuid}/file | 返回原始 PDF，需要认证 |
| POST | /api/search | 仅检索，排查召回与重排效果 |
| POST | /api/chat | 兼容原 JSON 问答，返回完整/不完整状态 |
| POST | /api/chat/stream | 带鉴权的 NDJSON 流式问答 |
| GET | /api/documents/{uuid}/pages/{page} | 鉴权读取一页原文；物理页码从 1 开始 |
| GET | /api/translations/config | 翻译启用状态、模型名、单次页数上限 |
| POST | /api/translations | 创建翻译任务，202 返回任务详情 |
| GET | /api/translations | 当前用户最近 50 条翻译任务摘要 |
| GET | /api/translations/{uuid} | 任务详情，含各页原文、译文与状态 |
| POST | /api/translations/{uuid}/cancel | 停止该用户任务的后续翻译 |
| GET | /api/translations/{uuid}/file | 下载 UTF-8 BOM 编码的双语 TXT |
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


## 论文翻译（v0.5.0）

所有接口要求 `X-API-Key`。`GET /api/translations/config` 返回 `{enabled, model, max_pages}`；页面原文接口返回 `{page, original}`。开始翻译的请求如下：

```json
{
  "request_id": "客户端生成并保留的 UUID",
  "document_id": "当前用户论文的 UUID",
  "start_page": 1,
  "end_page": 3,
  "target_language": "zh"
}
```

`target_language` 支持 `zh`（简体中文，默认）和 `en`（英语）。页码必须为 1 到 PDF 总页数之间的整数，起始页不能大于结束页，单次至多 50 页。单页 2 万字符、单任务 20 万字符；超限返回 422，未截断原文。所选范围全部为空文本时返回 422；混合范围中空文本页标记为 `skipped`。

同一用户、相同 `request_id` 和参数返回同一任务；改参数复用编号返回 409。网络丢失确认后应复用原编号，不生成新编号自动重试。每个用户最多 1 个未完成任务，全服务最多 4 个，超过返回 429。HTTP 202 只表示已受理。

列表形状为 `{translations: [...]}`。摘要字段包含 `id, request_id, document_id, filename, start_page, end_page, target_language, model, status, error, total_pages, completed_pages, skipped_pages, reused_pages, created_at, updated_at`。详情额外包含 `pages`，每页为 `{page, original, translation, status, reused}`；`reused` 为 0 或 1。时间为 UTC ISO 格式。

任务状态为 `queued / running / succeeded / failed / cancelled`；页状态为 `pending / running / completed / skipped`。只有所有段落正常完成的页面才会保存译文。`succeeded` 可以包含跳过的无文本页，因此应同时显示 `skipped_pages`。`failed/cancelled` 的 `pending` 页尚未完成，不能伪装成成功。

取消接口只停止后续翻译，已发送的模型请求可能仍会计费；取消不会删除已有完整译文。已结束任务再次取消不改变其状态。导出仅允许任务结束且至少有 1 页完整译文，否则返回 409。导出标注部分失败、跳过页和未完成页。下载使用鉴权 fetch 获取 Blob，令牌不放进 URL。

文献、任务、原文和导出均校验用户所有权，不可见资源返回 404。删除文献会清理翻译任务、页面和缓存；运行中的任务不能恢复已删除记录。重新启动后未完成任务标记失败且不会自动发起模型调用。请先核对平台记录，再手动新建任务；已完成页可复用。


## 阅读进度（v0.6.0）

文献上传、重复上传和文献列表响应新增 `reading` 对象；原有文献 `status=ready` 仍表示索引状态，不能用于阅读状态。无历史记录时返回待读与零页码，不写入虚构的阅读时间。

```json
{
  "status": "reading",
  "last_page": 3,
  "furthest_page": 7,
  "progress_percent": 35,
  "updated_at": "2026-11-12T12:00:00+00:00",
  "completed_at": null
}
```

示例表示一篇 20 页论文，上次在第 3 页，最远翻到第 7 页；进度为位置比例，不能据此判断每页都读过。

`GET /api/documents/{uuid}/reading` 返回上述对象。`PATCH` 每次只允许一项：

- `{ "page": 3 }`：保存上次页码，更新最远页码；待读转为在读，已读保持已读。页码必须是整数且在 1 到 PDF 总页数之间。
- `{ "status": "completed" }`：手动标记已读，将页码置为最后一页，记录完成时间。
- `{ "status": "reading" }`：标记在读，保留页码并清除完成时间，不虚构翻页记录。
- `{ "status": "unread" }`：明确重置状态，清除页码与完成时间。

空对象、同时提交 page/status、非法状态或越界页码返回 422；未认证返回 401；他人或已删除文献返回 404。GET/PATCH 都通过 `X-API-Key` 校验所有权，服务端不接受客户端用户标识。PATCH 成功后返回保存的 reading 对象。

前端按顺序提交翻页记录，快速翻页合并为最新待保存页；失败保留待保存位置供明确重试。正常关闭阅读器等待队列完成；强制关闭浏览器不能保证尚在途的请求完成。不同设备/窗口同时修改同一论文时，以服务端最后成功写入的请求为准。


## v0.7.0 研究工作台接口

旧单轮接口仍兼容；工作台使用新的持久化会话接口。下列接口均使用同一个 `X-API-Key`，UUID 由客户端生成，重复确认必须使用原 UUID。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET / POST | `/api/notes` | 列表 / 创建或按 revision 更新；GET 可带 document_id |
| GET | `/api/notes/export` | TXT 导出，可带 document_id |
| DELETE | `/api/notes/{id}` | 删除当前用户的笔记或书签 |
| GET / POST | `/api/conversations` | 会话列表 / 创建固定文献范围的会话 |
| GET / PATCH / DELETE | `/api/conversations/{id}` | 读取历史 / 重命名 / 删除（运行中拒绝删除） |
| POST | `/api/conversations/{id}/stream` | 有界上下文问答，NDJSON，后台持续保存 |
| GET / POST | `/api/glossaries/{zh或en}` | 读取 / 保存术语表与 revision |
| GET / POST | `/api/analyses` | 最近 50 条分析 / 提交 1–5 篇分析 |
| GET / PATCH | `/api/analyses/{id}` | 任务详情 / 人工字段笔记 |
| POST | `/api/analyses/{id}/cancel` | 停止后续分析，已发出模型请求可能仍计费 |
| GET | `/api/analyses/{id}/file` | 导出卡片及证据 TXT |
| POST | `/api/uploads` | multipart：file、request_id；202 返回后台入库任务 |
| GET | `/api/uploads`、`/api/uploads/{id}` | 最近 50 条入库任务 / 详情与实际阶段 |

笔记 POST 字段为 `id, document_id, page, kind, text, quote, tags, revision`；kind 是 `note/bookmark`，首次 revision=0。最多 10000 字正文、6000 字摘录、10 个不超过 30 字的标签。更新冲突返回 409；不存在或他人资源返回 404。重复的相同版本请求可返回已保存结果。

会话 POST：`{id: UUID, document_ids: UUID[] | null}`，null 在创建时快照当前全部文献，不会将未来上传的论文自动加入。PATCH：`{title: string}`。详情包含 `turns` 与 `missing_document_ids`。每轮 POST：`{request_id: UUID, question: string, top_k: 1..30}`，文献范围来自会话，不接受客户端覆盖。turn 状态为 `running/completed/failed/interrupted`；preview 为未验证草稿，answer 仅在完成格式与引用编号检查后保存。

分析 POST：`{request_id: UUID, document_ids: UUID[]}`。任务状态为 `queued/running/succeeded/failed/cancelled/interrupted`，阶段文本为 stage，结果为 `result.cards[]`。每张卡片含 `document_id,filename,fields,evidence,edits`；fields 键固定为 `question,method,data,conclusion,limitations`，每项含 `label,text,source_ids,missing`。source_ids 只对应同一张卡片的 evidence。人工 PATCH：`{document_id,field,text,revision}`；text=null 移除人工笔记，不覆盖生成字段。

术语 POST：`{entries:[{source,target}],revision}`，最多 100 项；返回内容 version 哈希与递增 revision。翻译 POST 新增可选 `glossary_version`，新客户端应发送当前已保存版本；版本落后返回 409。已接受请求的同 UUID 确认使用原快照。翻译详情增加 `glossary_revision,glossary_count,glossary_changed,glossary_version`，不返回其他用户术语。

删除论文同时清理笔记、翻译，以及所有包含它的整组分析任务；问答历史保留并标记缺失文献。最近 50 条是任务列表显示窗口，不是自动清理策略。完整参数约束也可通过运行服务的 `/docs` 查看。
