# RAG 质量评测

自动化单元测试使用模拟模型，能验证接口、数据隔离、引用编号及恢复逻辑，不能证明真实模型的学术回答准确率。本目录配套 20 条待标注问题模板与 `scripts/evaluate_rag.py`，用于在真实论文上复现实测。

## 准备标注

1. 启动后端，上传有可提取文字的真实论文。
2. 在项目根目录执行 `python scripts/evaluate_rag.py --list-documents` 获取论文 UUID。脚本优先读取 `PAPERTRAIL_EVAL_TOKEN`；如果 `.env` 中 `API_TOKENS_JSON` 只有一个用户，会使用该项目令牌，不输出令牌。多用户时需在当前终端设置明确的项目访问令牌。
3. 把 `evaluation/cases.template.json` 复制为 `evaluation/cases.local.json`。每题替换 `REPLACE_WITH_DOCUMENT_UUID`，由人工阅读原文后填写 `expected_pages`。页码是 PDF 物理页码；模板中的第 1 页不是实际标注。
4. 只保留确实适用于论文且有证据的问题，避免用模型答案反过来当标准答案。多篇题目的预期来源应包含每篇需要的论文与页码。

模板未运行，不包含任何实测准确率。无答案问题、数字准确性、译文术语一致性及图表理解需要另列人工核验记录，不能仅凭页码命中判定通过。

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

该命令每题最多调用一次文本模型，可能产生费用，不自动重试。开始前先完整验证用例和文献范围；每完成一题保存一次结果到 `evaluation-results/`。评测调用兼容的单轮接口，不向用户的会话历史添加测试问题。

## 解释结果

- `page_recall`：该题检索结果覆盖的人工标注页数，除以全部标注页数。相同论文页码去重，不使用 chunk 数替代页数。
- `mean_page_recall_successful_cases`：成功请求的页码召回率平均值；同时必须报告 `errors`，不能隐去失败用例。
- `seconds`：该题客户端观察到的请求耗时，包含本地检索或模型生成；不是模型推理速度。
- `human_answer_supported`、`human_citations_supported`：留空，需人工结合原文逐项填写。脚本不把“引用编号有效”自动换算为事实准确率。
- `result`：实际检索片段或模型响应。检查回答是否遗漏限制条件、误读数值、混淆论文、将检索缺失表述成论文没有。

可固定同一组论文和问题，对比 `--top-k 5/10/20`。这只是检索数量的实验，不是 BM25、向量检索和混合检索消融；尚未实现该消融时不得据此宣称混合检索提升了多少。

评测结果和本地标注默认由 `.gitignore` 排除，可能包含私人论文原文。对外展示时仅选择经核对、获准展示的记录，并保留失败样本与测量条件。
