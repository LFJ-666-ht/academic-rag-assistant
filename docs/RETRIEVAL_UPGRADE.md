# 检索升级、模型对比与数据迁移（v0.8.0）

本版保留现有阅读、问答、翻译和配图工作流，改进检索与入库。源码更新不会自动改写 `.env`、更换 Embedding 或重新生成历史问答。

## 1. 更新后直接生效的内容

| 内容 | 对已有论文的影响 |
| --- | --- |
| FTS5 关键词索引 | 首次启动为原分块一次性补建；之后不再每次全文分词 |
| 五维研究卡片批量查询编码 | 保留各维度召回，跨所选论文复用问题向量 |
| 同页上下文补充与重叠去重 | 可用于已有分块，不跨物理页，不合并不同论文 |
| 旧长片段分窗重排 | 处理全部窗口，降低尾部被截断的风险；可能增加重排工作量 |
| 上传锁拆分 | 编码不占用文献/阅读存储锁；模型推理仍可能排队 |
| 连续追问处理 | 独立问题不再附带前两轮问题；指代追问带有限上下文 |
| 新版 PDF 解析分块 | 仅新上传生效；旧论文需要下述重建 |

首次启动补建关键词索引可能需要时间。后端需保持单进程，等待 `Application startup complete` 再使用。不要重复启动后端。

## 2. 先沿用原模型，重建旧论文分块

先等待任务结束，在后端终端按 Ctrl+C 停止服务。不要删除数据。项目根目录 PowerShell：

```powershell
cd "$env:USERPROFILE\Desktop\rag-academic-assistant"
.\.venv\Scripts\python.exe scripts/reindex.py --output-data-dir .\data-v080
if ($LASTEXITCODE -ne 0) { throw "重建未完成，保持原 .env，不要切换目录。" }
```

默认从 `.env` 中的 DATA_DIR 读取已有知识库，用当前 Embedding 和新分块规则重建。需要显式指定原数据时添加 `--source-data-dir "D:\实际旧知识库"`。输出目录必须尚不存在；重复运行请选择新目录名，不要删除唯一副本来腾位置。

脚本会复制原 PDF、SQLite 数据库、图片等，排除旧向量目录，利用 SQLite backup API 保留已提交的 WAL 数据。在临时目录中重建，验证完成后才发布 `data-v080`。PDF 哈希或页数变化、模型加载失败、磁盘不足等情况会停止，原知识库保留。需要额外磁盘空间容纳文献、研究记录和新向量。

成功后脚本打印应设置的 DATA_DIR 和 EMBEDDING_MODEL。原模型不变时，可执行以下命令仅切换数据目录：

```powershell
if (-not (Test-Path .\data-v080\reindex-report.json)) { throw "没有成功重建的记录，请勿切换。" }
if (-not (Test-Path .\.env)) { throw "当前目录缺少原 .env，请检查项目位置。" }
Copy-Item .env ".env.before-v080-$(Get-Date -Format yyyyMMdd-HHmmss)"
.\.venv\Scripts\python.exe -c "from dotenv import set_key; set_key('.env', 'DATA_DIR', './data-v080')"
if ($LASTEXITCODE -ne 0) { throw "配置写入未完成，请检查 .env。" }
.\.venv\Scripts\python.exe run_backend.py
```

若 PyCharm Run Configuration 或当前终端也设置了 DATA_DIR，它会优先于 `.env`，需同步更正或移除重复配置。上面的切换命令不打印密钥。备份 `.env.before-*` 被 `.gitignore` 排除，不要提交。

在浏览器核对：文献数、原 PDF、已读页码、笔记、书签、历史答案引用、译文与卡片。新检索与旧历史答案可能使用不同 chunk ID；旧答案保留原文快照、论文 UUID 和物理页码，不被新答案覆盖。

## 3. 可选：对比多语言 BGE-M3

BGE-M3 是中文提问、英文论文场景的候选模型，不预设一定更准或更快。先在当前机器比较检索效果、响应时间和内存占用。机器内存紧张时可将 `.env` 的 `EMBEDDING_BATCH_SIZE` 调低，例如 4；批量变小不能消除模型权重自身的内存需求。

不要先修改 `.env` 的 EMBEDDING_MODEL。先准备目标模型：

```powershell
cd "$env:USERPROFILE\Desktop\rag-academic-assistant"
.\.venv\Scripts\python.exe prepare_models.py --embedding-model BAAI/bge-m3
if ($LASTEXITCODE -ne 0) { throw "模型准备未完成，先保留原配置。" }
```

该命令下载本地权重，不调用收费文本模型。网络受限时使用你已验证可用的下载端点或完整本地模型目录；不要通过关闭证书校验来解决下载错误。脚本不会修改已有 `.env`。自定义 `SENTENCE_TRANSFORMERS_HOME` 时，准备、重建和运行应使用同一缓存路径。

停止后端后，重建另一个目录：

```powershell
.\.venv\Scripts\python.exe scripts/reindex.py --embedding-model BAAI/bge-m3 --output-data-dir .\data-v080-m3
if ($LASTEXITCODE -ne 0) { throw "重建失败，保持原 .env。" }
```

成功后才同时切换两项：

```powershell
if (-not (Test-Path .\data-v080-m3\reindex-report.json)) { throw "尚未完成 M3 重建。" }
Copy-Item .env ".env.before-m3-$(Get-Date -Format yyyyMMdd-HHmmss)"
.\.venv\Scripts\python.exe -c "from dotenv import set_key; set_key('.env', 'DATA_DIR', './data-v080-m3'); set_key('.env', 'EMBEDDING_MODEL', 'BAAI/bge-m3'); set_key('.env', 'EMBEDDING_QUERY_INSTRUCTION', '')"
if ($LASTEXITCODE -ne 0) { throw "配置写入未完成，请核对 .env。" }
.\.venv\Scripts\python.exe run_backend.py
```

只有迁移模型与 .env 中名称完全一致时才允许启动。使用自定义本地模型目录时，以脚本成功后打印的值为准。本项目的 M3 适配使用 dense embedding；没有自动启用 M3 的 sparse/multi-vector 模式。

## 4. 评测与可选设置

先填写 `evaluation/cases.local.json`，保持同一组论文 UUID、问题、页码和 top-k，分别保存结果：

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_rag.py --list-documents
# 首次使用才复制模板；不要覆盖已人工标注的文件
if (-not (Test-Path evaluation/cases.local.json)) { Copy-Item evaluation/cases.template.json evaluation/cases.local.json }
# 手工完成真实标注后运行，文件名区分不同配置
.\.venv\Scripts\python.exe scripts/evaluate_rag.py --cases evaluation/cases.local.json --output evaluation-results/v080-current-model.json
```

切换 M3 后将输出名称改为 `evaluation-results/v080-m3.json`。默认仅本地检索，不消耗 DeepSeek/Seedream 额度。`--generate` 会调用文本模型；如果开了问题改写，部分追问还会多一次调用。没有真实标注与真实模型结果前，不宣称检索提升百分比。

| 设置 | 默认值 | 含义 |
| --- | --- | --- |
| EMBEDDING_QUERY_INSTRUCTION | auto | 自动适配已识别 BGE 型号；M3 不加中文前缀，可显式留空 |
| EMBEDDING_BATCH_SIZE | 16 | 入库和批量问题编码批大小 |
| CHUNK_MAX_TOKENS | 320 | 文段 token 预算，兼顾两种 tokenizer 和模型窗口 |
| CHUNK_MAX_CHARS | 700 | 字符上限，与 token 上限同时生效 |
| CHUNK_OVERLAP_CHARS | 100 | 长段切分最多重叠，短块自动缩小 |
| RETRIEVAL_CONTEXT_NEIGHBORS | 1 | 同页同章节左右邻块范围，0 可关闭 |
| RETRIEVAL_CONTEXT_CHARS | 1800 | 扩展证据的字符预算 |
| RERANK_MIN_SCORE | 留空 | 校准后才启用最低分过滤；不是正确概率阈值 |
| RAG_QUERY_REWRITE | off | on 时指代追问可多调用一次文本模型，失败回退 |

更改解析参数仅影响新入库或重建后的分块。更改分数阈值、上下文范围与改写设置后重启后端即可，仍需重新评测。

## 5. 回退与边界

- 重建工具不自动切换配置。重建失败时保持原配置即可使用旧库。
- 已切换新目录后，停止后端，恢复迁移前 DATA_DIR 和 EMBEDDING_MODEL（以及查询前缀），可重新打开原库。原库是迁移时的快照；之后在新库新增的笔记、论文等不会自动合并回原库，先导出需要保留的内容。
- 要降级源码到旧版本，应同时恢复安装器保存的完整数据快照；不要只替换一个 Python 文件后混用新旧数据库。
- 文本块排序是启发式，复杂双栏/跨栏、公式、表格和扫描件没有完整解决；没有自动 OCR，没有句级语义蕴含校验，没有建立论文作者/DOI 知识图谱。
- 当前只做同页上下文与重叠去重，未实现完整父子索引或基于向量的 MMR。连续追问识别仍需人工验收。
- Qdrant Local 保持单进程；未通过添加普通 payload 索引宣称加速。数据增大后是否迁移服务端，应由真实负载决定。
