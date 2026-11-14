# RAG 质量评测

自动化单元测试使用模拟模型，能验证接口、数据隔离、引用编号及恢复逻辑，不能证明真实模型的学术回答准确率。本目录配套 20 条待标注问题模板与 `scripts/evaluate_rag.py`，用于在真实论文上复现实测。

## 准备标注

1. 启动后端，上传有可提取文字的真实论文。
2. 在项目根目录执行 `python scripts/evaluate_rag.py --list-documents` 获取论文 UUID。脚本优先读取 `PAPERTRAIL_EVAL_TOKEN`；如果 `.env` 中 `API_TOKENS_JSON` 只有一个用户，会使用该项目令牌，不输出令牌。多用户时需在当前终端设置明确的项目访问令牌。
3. 把 `evaluation/cases.template.json` 复制为 `evaluation/cases.local.json`。每题替换 `REPLACE_WITH_DOCUMENT_UUID`，由人工阅读原文后填写 `expected_pages`。页码是 PDF 物理页码；模板中的第 1 页不是实际标注。
4. 只保留确实适用于论文且有证据的问题，避免用模型答案反过来当标准答案。多篇题目的预期来源应包含每篇需要的论文与页码。

模板未运行，不包含任何实测准确率。无答案题设 `answerable=false`、`expected_pages=[]`；模板含两个示例，必须核对全文后才可采用。数字准确性、译文术语一致性及图表理解仍需人工核验，不能仅凭页码命中判定通过。

## 执行

Windows PowerShell，项目根目录：

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_rag.py --list-documents
Copy-Item evaluation/cases.template.json evaluation/cases.local.json
# 先编辑 cases.local.json，填写真实 UUID 和人工核对的页码
.\.venv\Scripts\python.exe scripts/evaluate_rag.py --cases evaluation/cases.local.json --top-k 10
```

默认只调用本地 `/api/search`，不会调用收费文本模型。它仍会运行本地 BGE 检索。需要评测回答时，明确添加 `--generate`：

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_rag.py --cases evaluation/cases.local.json --top-k 10 --generate
```

该命令通常每题调用一次文本模型；若开启 `RAG_QUERY_REWRITE=on`，带指代的历史追问可能额外调用一次改写模型。可能产生费用，不自动重试。开始前先完整验证用例和文献范围；每完成一题保存一次结果到 `evaluation-results/`。评测调用兼容的单轮接口，不向用户的会话历史添加测试问题。

## 解释结果

- `page_recall`：该题检索结果覆盖的人工标注页数，除以全部标注页数。相同论文页码去重，不使用 chunk 数替代页数。
- `mean_page_recall_successful_cases`：成功请求的页码召回率平均值；同时必须报告 `errors`，不能隐去失败用例。
- `seconds`：该题客户端观察到的请求耗时，包含本地检索或模型生成；不是模型推理速度。
- `human_answer_supported`、`human_citations_supported`：留空，需人工结合原文逐项填写。脚本不把“引用编号有效”自动换算为事实准确率。
- `result`：实际检索片段或模型响应。检查回答是否遗漏限制条件、误读数值、混淆论文、将检索缺失表述成论文没有。

可固定同一组论文和问题，对比 `--top-k 5/10/20`。这只是检索数量的实验，不是 BM25、向量检索和混合检索消融；尚未实现该消融时不得据此宣称混合检索提升了多少。

评测结果和本地标注默认由 `.gitignore` 排除，可能包含私人论文原文。对外展示时仅选择经核对、获准展示的记录，并保留失败样本与测量条件。

## v0.8.0 评测口径

模板仍为 20 题，包含中文问英文论文、普通单篇、多篇对照、指代追问、切换话题和无答案示例。`category` 是人工分类；`history` 可填最多 4 条人工核对的先前问答。不要将模型猜测当金标准，未核对的“无答案”题不得用于阈值校准。

- 页码 Recall：与旧版本一致；无答案题返回 null，不参与 Recall/MRR 均值。
- `reciprocal_rank`：按检索顺序对 `(document_id,page)` 去重，首个正确页在第 r 位时为 1/r；有答案但没找到为 0。MRR 是这项的均值，统计单位是页而非 chunk。
- `*_successful_cases` 只统计成功请求；`*_errors_as_zero` 将有答案题的请求错误按 0 计入，必须同时展示错误数。
- `latency_p50_seconds`、`latency_p95_seconds` 使用成功请求耗时的线性插值分位数。20 题的 P95 只是小样本观察，不能当作生产容量承诺。脚本不预热（`warmup_requests=0`），请注明模型是否已加载，分别记录冷启动和热运行。
- `unanswerable_empty_retrieval_rate` 只表示已标注无答案题返回空证据的比例；模型读到证据后正确拒答需要人工填 `human_abstention_correct`，不能把空检索率改名为拒答准确率。
- `configuration` 记录本次后端检索配置；旧后端没有配置接口时标注不可用。不得在报告中写模型密钥或访问令牌。

推荐保留同一份标注、论文 UUID 和 top-k，依次运行：现有分块/模型 → 新分块/原模型 → 新分块/BGE-M3。更换 DATA_DIR 时按迁移说明保留相同论文标识。记录耗时与内存，逐条审查召回页、引用原文和答案；没有真实结果前不要写提升百分比。当前没有自动完成真实语料标注或检索消融实验。

最低分阈值 `RERANK_MIN_SCORE` 默认禁用。先在有答案与无答案样本上查看 `rerank_score` 分布，选择候选阈值后重新评测，检查有效答案是否被误拒；不同模型、分块、查询改写或评分窗口变更后需重新校准。不要照搬示例阈值 0.5。

本地向量库和模型内存也需要实测。Windows 可在模型加载后查看任务管理器，或执行 `Get-Process python | Select-Object Id,CPU,WorkingSet64`，注意区分实际后端进程。此脚本没有自动测量服务器峰值内存。
