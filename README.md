# RAG 学术文献助手 · 后端增量版本 v0.2

基于现有 PyMuPDF、BGE Embedding、Qdrant、中文关键词检索、RRF、BGE Reranker 脚本重构。支持持续上传多篇 PDF、持久化个人知识库、混合检索及 DeepSeek 引用问答。

当前交付是可供 React + TypeScript 对接的后端阶段。尚未实现正式登录页面、React 界面、流式输出、OCR 和公网部署。本项目包没有连接或修改你们的 GitHub 仓库，也没有编造提交历史。

## 1. 与旧脚本的区别

| 旧问题 | 本次实现 |
|---|---|
| Qdrant 内存模式，重启清空 | `data/qdrant` 磁盘存储 |
| 每次从 0 编号导致覆盖 | 文档 UUID + 分块 UUID，不同文件互不覆盖 |
| 固定读取一篇 PDF | 上传 API，支持连续上传不同论文 |
| 全局 `all_chunks` 只在主程序定义 | SQLite 保存论文与片段，API 可独立调用 |
| 所谓 BM25 实为命中词计数 | 实现 TF、IDF、长度归一化的 BM25 |
| RRF 排序文本与候选列表 zip 错配 | 按 chunk_id 汇总，再按 id 取回完整片段 |
| 模型导入时立即加载 | 第一次实际使用时加载，并保持复用 |
| 仅打印片段 | DeepSeek 生成回答，返回引用、页码及原文 |
| 无文件范围、用户隔离 | 后端令牌映射用户，检索/原文/删除均限制所有者 |

## 2. 目录

```text
backend/
  core.py          PDF、BM25、RRF、模型适配、SQLite、Qdrant
  generation.py    DeepSeek HTTP 调用和引用编号校验
  main.py          FastAPI 接口与认证
  __init__.py
tests/test_backend.py
docs/
  API.md           前端对接约定
  DEVELOPMENT.md   团队协作与后续任务
  COMPETITION.md   技术文档章节与 5–8 分钟演示脚本
  VALIDATION.md    本次验证范围、版本与待验证项
.env.example
requirements.txt
requirements-test.txt
requirements-tested.txt
.gitignore
```

## 3. 在现有 PyCharm 项目运行（Windows PowerShell）

不需要重新创建 GitHub 仓库。将这个包里的 `backend/`、`tests/`、`docs/` 和配置文件合并到你们的仓库根目录；已有 README、requirements 和 .gitignore 请比较合并，不要直接覆盖团队内容。旧 `rag_pipeline.py` 可以暂时保留作对照；新服务入口是 `backend.main:app`。

在 PyCharm Terminal 进入项目根目录，使用已有虚拟环境（下面的 `python` 必须指向该环境）：

```powershell
python --version
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

建议 Python 3.11 或 3.12。将生成的令牌填入 `.env` 的 `API_TOKENS_JSON` 键位置；值可以是 `demo-user`。多个用户用不同令牌和值，例如：

```dotenv
API_TOKENS_JSON={"第一个至少16位随机令牌":"alice","第二个至少16位随机令牌":"bob"}
DEEPSEEK_API_KEY=填你的服务端密钥
DEEPSEEK_MODEL=填控制台实际可用的模型ID
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

这里只是结构示例；令牌请使用上面命令生成的随机值。DeepSeek 密钥和这些服务端配置都不能提交 Git，也不能写进 React 源码。用户访问令牌与 DeepSeek 密钥是两种不同的凭据。当前令牌机制适合本地团队联调，正式用户系统后续改为登录认证。

模型名没有写死：DeepSeek 官方文档会更新，按你的控制台可用名称填写。检查时官方首页示例为 `deepseek-flash`；如果使用其他兼容供应商，还需修改 base URL。

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

打开 http://127.0.0.1:8000/docs ，点击 **Authorize**，填入你生成的访问令牌（不要填 DeepSeek 密钥）。

首次上传会下载/加载 BGE Embedding，首次搜索会下载/加载 BGE Reranker，耗时取决于网络与电脑性能。若下载失败，可提前下载完整模型，并将 `.env` 的 `EMBEDDING_MODEL`、`RERANKER_MODEL` 改为本地目录。不要把模型加载失败误认为 PDF 解析失败。可选镜像配置保留为注释，不再强制使用镜像。

**只启动一个后端进程。** 本地 Qdrant 文件目录不能被多个进程同时打开；不要设置多个 workers，也不要在同一数据目录同时运行另一个索引脚本。此阶段推荐不加 `--reload`。

## 4. 按顺序验收

1. `GET /api/health` 返回 ok。
2. `POST /api/documents` 上传论文 A，再上传论文 B；每次请求一篇。
3. 再上传同一份 A，确认返回 `duplicate: true`，且论文数量不增加。
4. `GET /api/documents` 确认两篇均在列表。
5. `POST /api/search` 输入 `{"question":"这两篇论文采用了什么研究方法？","top_k":5}`，先单独确认检索片段质量；此接口不调用 DeepSeek。
6. `POST /api/chat` 传同样 JSON，确认答案及 `citations`。
7. 用引用的 document_id 请求 `/api/documents/{document_id}/file`，核对页码与原文；这里的页码是从 1 开始的 PDF 物理页码，不是论文印刷页码。
8. Ctrl+C 停止服务，重新启动，确认列表和检索仍可用。
9. 用另一访问令牌检查隔离；删除 A 后，确认它不再被检索到。

上传接口同步等待解析与嵌入完成，当前没有假进度或后台队列；前端需显示“正在解析与索引”。一篇文件上限 30 MiB、500 页、10000 分块，可在 `Settings` 中调整。HTTP multipart 在框架解析时可能先写临时文件；公网部署还需网关请求体上限，不能只依赖路由中的大小检查。

## 5. 数据与检索设计

PDF 原件、SQLite、Qdrant 均位于 `DATA_DIR`；不删除该目录即可跨重启保存。迁移/备份需停服务后整体复制数据目录，不能只复制向量库。Embedding 模型改变需要使用新的数据目录并重新上传，避免同维度不同语义空间混用。

每页使用 700 字符窗口、100 字符重叠，不跨页；长段落不会成为超长片段。按文件字节 SHA-256 做同用户去重；内容相同但重新导出的 PDF 可能得到不同哈希，这是预期行为。

检索先按所有者和可选论文范围筛选，再各取 20 条向量/BM25 候选，用 RRF(k=60) 融合为最多 30 条，最后 BGE Reranker 排序返回 top_k。BGE 编码批量处理并归一化，向量维数从实际输出获取。关键词索引当前每次从 SQLite 片段构造，适合小型个人知识库；大规模时需持久化倒排索引/缓存。

入库状态先记录为 indexing，全部成功后变为 ready；失败删除相关数据。删除先标记 deleting。启动时清理未完成记录，避免半成品出现在检索中。进程内锁串行化知识库操作，DeepSeek 生成在锁外进行；这是单机演示设计，并非高并发架构。

`citations` 是模型实际使用的编号对应片段；`evidence` 是给模型的全部参考片段。编号越界时拦截回答；无引用时返回 `missing_citations`。**引用编号合法不代表结论被原文支持。** 当前没有语义级证据验证或经过评测校准的相关性阈值，仍需人工核对，后续评测再改进。

无可检索文本时直接返回提示，不调用模型。扫描版全空文本会拒绝，部分无文本页在 `empty_pages` 中提示；尚无 OCR、多栏版面精细恢复、图片/表格理解、公式重建。外文内容可上传，但当前中文 BGE 的英文检索效果需用真实论文评测，不能预设与中文同等表现。

## 6. 测试

```powershell
python -m pip install -r requirements-test.txt
python -m pytest -q tests
```

本次结果：12 passed；详细边界见 `docs/VALIDATION.md`。测试不下载 BGE，也不消耗 DeepSeek 额度。真正的模型加载、网络和问答质量需要按第 4 节在你们的电脑验证。

`requirements-tested.txt` 记录本次轻量集成测试的直接依赖版本，不是完整生产锁文件。模型依赖尚未实测。团队完成真实环境联调后，在干净虚拟环境导出锁定依赖并记录 Python/设备版本。

## 7. 团队信息（提交前填写）

- 团队名称：待填写
- 成员及职责：待填写（后端/检索、React 前端、测试与文档）
- 学校与专业：待填写
- 仓库地址：使用你们已有仓库
- 项目版本/演示 commit：完成验收后填写

参考接口依据：
- https://github.com/qdrant/qdrant-client （磁盘模式及 query_points）
- https://api-docs.deepseek.com/ （Chat Completions 兼容接口与模型配置）
