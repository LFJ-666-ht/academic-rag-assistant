# RAG 学术文献助手 · 后端修订版 v0.2.1

基于现有 PyMuPDF、BGE Embedding、Qdrant、中文关键词检索、RRF、BGE Reranker 脚本重构。支持持续上传多篇 PDF、持久化个人知识库、混合检索及 DeepSeek 引用问答。

当前交付是可供 React + TypeScript 对接的后端阶段。尚未实现正式登录页面、React 界面、流式输出、OCR 和公网部署。本项目包没有连接或修改你们的 GitHub 仓库，也没有编造提交历史。

**首次安装或换电脑：先看 [完整使用说明](docs/USAGE.md)，按步骤创建 `.env`、准备模型再启动。原电脑已有项目升级：先看 [UPDATE_v0.2.1.md](UPDATE_v0.2.1.md)，保留原来的配置、数据和已跑通环境。换电脑时需要重建虚拟环境。**

本次整理了联调中确认的修复：本地模型离线加载、固定启动目录、方舟输出长度/思考参数、检索证据不足的措辞，以及表格整理与逐字引文的区分。

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
run_backend.py      固定项目目录、离线模型、单进程启动
prepare_models.py   单独准备模型缓存或离线检查
UPDATE_v0.2.1.md    旧项目替换步骤和改动清单
tests/test_backend.py
docs/
  USAGE.md         首次运行、换电脑迁移、日常使用和报错处理
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

## 3. 首次运行与换电脑（Windows PowerShell）

完整步骤、旧论文迁移和常见报错见 [docs/USAGE.md](docs/USAGE.md)。下面是首次运行的最短顺序。已配置成功的原电脑跳到第 5 步即可。

### 第 1 步：获取代码并创建环境

旧电脑的最新修改需先 Push，再在新电脑用 PyCharm 克隆原仓库。下面命令在项目根目录执行，即能看到 `requirements.txt` 和 `run_backend.py` 的目录。

新电脑安装 Python 3.12 后执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

在 PyCharm 解释器设置中选择新项目的 `.venv\Scripts\python.exe`。不要复制旧电脑的虚拟环境；旧电脑已跑通的环境也不用重建。这里直接指定解释器，不需要激活脚本。

### 第 2 步：创建 `.env`，不能只修改模板

新电脑从 GitHub 下载后没有 `.env` 是正常情况，它包含真实配置，被 Git 忽略。如果可以获取旧 `.env`，先安全复制到新项目根目录。否则执行：

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

新建的 `.env` 必须与 `run_backend.py` 同级，不能叫 `.env.txt`。创建文件后还必须填写第 3 步，复制模板本身不代表配置已完成。

### 第 3 步：填写访问令牌和模型平台配置

已有有效访问令牌时保留原配置。新建令牌时执行：

```powershell
.\.venv\Scripts\python.exe -c "import secrets,json; print('API_TOKENS_JSON=' + json.dumps({secrets.token_urlsafe(32): 'demo-user'}))"
```

**将终端输出的整行 `API_TOKENS_JSON=...`，复制到 `.env`，替换原来的同名行并保存。命令只打印配置，不会自动写入。** 令牌至少 16 个字符，用户标识不能为空；不要保留模板占位符或重复配置行。

再填写 `.env` 的 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`。三者必须匹配同一服务平台，优先沿用旧电脑成功运行的配置。模板里的方舟模型是原项目示例，不能代替自己账号的开通情况。

`API_TOKENS_JSON` 中的随机令牌用于访问本项目；`DEEPSEEK_API_KEY` 用于后端调用模型平台。真实 `.env` 不上传 GitHub，平台密钥不写入 React 源码。`DEEPSEEK_MAX_TOKENS` 目前设为 4096；`DEEPSEEK_REASONING_EFFORT=none` 用于原方舟模型，换供应商或模型后按其接口设置，不支持时留空。

迁移旧论文时，先停止旧后端，将整个 `data/`（或实际 `DATA_DIR`）复制到新项目；保留原用户标识如 `demo-user`，以及原 Embedding 模型 ID。新位置为项目的 `data/` 时使用 `DATA_DIR=./data`。不要只复制 PDF 或 Qdrant，不要直接混合两份已有知识库。完整操作见 [使用说明第 2 节](docs/USAGE.md)。

### 第 4 步：准备本地模型缓存

新电脑没有模型缓存时执行：

```powershell
.\.venv\Scripts\python.exe prepare_models.py --endpoint https://hf-mirror.com
```

等待下载和离线验证完成，看到 `Embedding OK: (1, 512)` 与 `Reranker OK: ...`。已有完整缓存的电脑只需按需检查：

```powershell
.\.venv\Scripts\python.exe prepare_models.py --check-only
```

两个 BGE 模型运行时只读本地缓存，首次启动不会自动下载。脚本包含官方 Pooling 配置，不要通过修改池化方式绕过缺失文件。下载时临时联网，运行时保持离线；这些标志不影响模型平台的 HTTP 请求。

### 第 5 步：启动与使用

```powershell
.\.venv\Scripts\python.exe run_backend.py
```

看到 `Application startup complete.` 后，保持终端运行，打开 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。点击 **Authorize**，填第 3 步的随机访问令牌：只填令牌字符串，不填整行 JSON，也不填模型平台密钥。

依次测试文档列表、上传、检索、问答，具体点击步骤见 [完整使用说明](docs/USAGE.md)。模型采用延迟加载，因此健康检查成功不能代替实际上传与问答验证。以后日常只需执行本步骤，无需重复安装、生成令牌或下载模型。

只启动一个后端进程。同一个本地 Qdrant 数据目录不能由多个进程同时使用，不额外设置 workers 或同时运行索引脚本。需要停止时按 `Ctrl+C`。

### 如果出现访问令牌配置报错

`API_TOKENS_JSON 必须是 {至少16位随机令牌: 用户标识} 的非空映射` 表示配置未读到或格式无效。先检查第 2、3 步：文件是否为根目录的 `.env`，生成的整行配置是否已替换并保存。

若文件检查正常但错误仍在，可以移除当前 PowerShell 的旧同名环境变量后重新启动：

```powershell
Remove-Item Env:API_TOKENS_JSON -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe run_backend.py
```

此命令不修改 `.env` 或系统持久设置。更多检查方法、模型缓存/代理/401/404/数据迁移问题，以及已有前端的启动方法，都在 [docs/USAGE.md](docs/USAGE.md)。

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

修订版测试结果及执行环境见 `docs/VALIDATION.md`。测试不下载 BGE，也不消耗 DeepSeek 额度；包含已有知识库回归，以及输出长度、思考参数、离线加载和启动目录的修复检查。对话中的用户本机真实联调记录与本次自动测试分开记录。

`requirements-tested.txt` 记录本次轻量集成测试的直接依赖版本，不是完整生产锁文件；不要据此重装已跑通的本机环境。本次自动测试未安装 BGE 权重。团队可在保留原环境的基础上记录真实模型依赖及 Python/设备版本。

## 7. 团队信息（提交前填写）

- 团队名称：待填写
- 成员及职责：待填写（后端/检索、React 前端、测试与文档）
- 学校与专业：待填写
- 仓库地址：使用你们已有仓库
- 项目版本/演示 commit：完成验收后填写

参考接口依据：
- https://github.com/qdrant/qdrant-client （磁盘模式及 query_points）
- https://api-docs.deepseek.com/ （Chat Completions 兼容接口与模型配置）
