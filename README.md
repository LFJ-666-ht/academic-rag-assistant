# 研迹 PaperTrail · RAG 学术文献助手

研迹 PaperTrail 面向学术论文阅读与资料整理，将持续积累的 PDF 文献转化为可检索、可问答、可回溯原文的个人知识库。系统结合混合检索与大语言模型生成答案，通过文件名、PDF 页码和原文片段连接回答与证据，并提供基于论文内容的概念配图功能。

## 核心功能

| 功能 | 实现方式 |
|---|---|
| 持久化文献库 | 支持连续上传多篇 PDF、重复文件识别、文献选择与删除，保存原始文件、文本片段及向量索引 |
| 混合检索 | BGE 向量检索与 BM25 关键词检索结合，经 RRF 融合与 BGE Reranker 重排后筛选证据 |
| 引用问答 | DeepSeek 基于检索片段生成回答，支持流式输出、引用编号校验和不完整回答状态提示 |
| 原文回溯 | 展示引用片段、文件名和 PDF 物理页码，支持在文献预览中核对证据 |
| 学术概念配图 | 支持直接输入描述，或由 DeepSeek 根据论文生成可编辑草稿，经确认后调用 Seedream 生成图片 |
| 用户数据隔离 | 根据项目访问令牌确定用户，隔离论文、草稿、图片任务和文件读取权限 |

## 使用流程

1. 连接知识库，上传包含可提取文字的 PDF，完成解析与索引。
2. 选择单篇、多篇或全部文献，提出研究方法、结果比较或内容理解问题。
3. 阅读流式答案，展开引用并查看对应 PDF 页码，核对论述依据。
4. 在 AI 配图窗口中输入描述，或选择论文生成草稿；编辑确认后生成概念图片。

文献问答与图像生成使用独立的模型配置。未配置图片模型时，文献检索与问答仍可使用。

## 技术架构

| 层次 | 技术与职责 |
|---|---|
| 前端 | React、TypeScript、Vite；文献工作台、流式问答、PDF 预览与配图交互 |
| 服务接口 | FastAPI；访问令牌认证、参数校验、文件接口与图片任务管理 |
| 文献处理 | PyMuPDF；逐页提取文本、分块并保留物理页码 |
| 检索 | BGE Embedding、BM25、RRF、BGE Reranker |
| 持久化 | SQLite、本地 Qdrant 与文件系统；保存文献、片段、索引、图片任务及生成文件 |
| 模型服务 | DeepSeek 提供文本生成；Seedream 提供图像生成 |

具体模块与数据流见 [技术架构与实现](docs/DEVELOPMENT.md)。

## 安装与运行

参考环境为 Python 3.12（64 位）、Node.js 24+ 和 Git。以下为 Windows PowerShell 命令，完整配置、迁移和排错说明见 [安装与使用](docs/USAGE.md)。

首次安装：

```powershell
git clone https://github.com/LFJ-666-ht/academic-rag-assistant.git rag-academic-assistant
cd rag-academic-assistant
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -c "import secrets,json; print('API_TOKENS_JSON=' + json.dumps({secrets.token_urlsafe(32): 'demo-user'}))"
```

将最后一条命令打印的整行写入 `.env`，替换 `API_TOKENS_JSON={}`，再填写 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 和同一平台的 `DEEPSEEK_BASE_URL`。可选配图使用独立的 `SEEDREAM_*` 配置。密钥只保存在后端；网页连接时使用生成的项目访问令牌。修改配置后重启后端。

准备本地检索模型：

```powershell
.\.venv\Scripts\python.exe prepare_models.py --endpoint https://huggingface.co
```

配置完成后，在项目根目录启动后端：

```powershell
.\.venv\Scripts\python.exe run_backend.py
```

在另一个终端启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

本地访问地址为 <http://127.0.0.1:5173/>；API 文档为 <http://127.0.0.1:8000/docs>。网页使用项目访问令牌连接后端，模型平台密钥由后端管理。

`npm ci` 用于首次安装或依赖变更。运行时只启动一个后端进程；本地 Qdrant 数据目录不能被多个实例同时打开。

## 项目结构

| 路径 | 内容 |
|---|---|
| `backend/` | 文献解析、持久化检索、模型适配、图片任务与 API |
| `frontend/` | React 应用、PDF 预览和 AI 配图组件 |
| `tests/`、`frontend/tests/` | 后端与前端自动化测试 |
| `docs/` | 安装使用、接口、架构与测试文档 |
| `run_backend.py` | 后端启动入口 |
| `prepare_models.py` | BGE 模型下载与离线加载检查 |
| `.env.example` | 配置模板 |
| `requirements.txt` | 后端运行依赖 |
| `requirements-test.txt` | 轻量测试依赖 |
| `requirements-dev.txt`、`pyproject.toml` | 开发检查依赖、Python 格式与检查规则 |
| `.editorconfig`、`.gitattributes` | 编码、缩进和换行规范 |
| `.github/workflows/ci.yml` | Python 检查、前端测试与构建工作流 |

## 代码质量与复现

Python 使用 Ruff 检查导入、未使用符号与基础错误，并统一格式；TypeScript 启用严格类型检查。测试使用临时存储和模拟模型，不调用真实收费模型。具体命令见 [代码规范与质量检查](docs/DEVELOPMENT.md#代码规范与质量检查)，执行记录与边界见 [测试与验证](docs/VALIDATION.md)。

GitHub Actions 工作流在 `main` 的推送、拉取请求或手动触发时运行。实际执行状态以仓库 [Actions](https://github.com/LFJ-666-ht/academic-rag-assistant/actions) 记录为准。

## 团队与维护

| 项目 | 信息 |
|---|---|
| 仓库维护账号 | [LFJ-666-ht](https://github.com/LFJ-666-ht) |
| 开发协作 | PyCharm、Git 与 GitHub；功能代码、测试和文档在同一仓库维护 |
| 贡献记录 | [完整提交历史](https://github.com/LFJ-666-ht/academic-rag-assistant/commits/main/) |
| 问题反馈 | [GitHub Issues](https://github.com/LFJ-666-ht/academic-rag-assistant/issues) |

## 文档索引

- [安装与使用](docs/USAGE.md)：环境配置、启动、操作、迁移与排错。
- [API 接口](docs/API.md)：认证、请求、响应、流式事件与错误处理。
- [技术架构与实现](docs/DEVELOPMENT.md)：模块职责、检索流程、引用与图片任务机制。
- [测试与验证](docs/VALIDATION.md)：测试环境、记录、覆盖范围与验证边界。

## 适用范围

当前版本为 v0.4.1，采用单后端进程与本地持久化存储，适合个人或小规模文献阅读场景。问答基于检索片段，引用编号校验用于检查来源对应关系，不等同于结论正确性验证。

当前 PDF 处理依赖可提取文本；纯扫描件需要预先进行 OCR。系统尚未集成 PDF 图片理解、全文长上下文阅读或完整多轮对话上下文。生成图片用于概念表达，不能替代实验数据图或精确科学绘图。

上述地址为本地运行入口，仓库当前未提供已验证的公网演示地址。现有访问令牌机制不包含账号注册与密码登录。

代码仓库：[LFJ-666-ht/academic-rag-assistant](https://github.com/LFJ-666-ht/academic-rag-assistant)。许可信息见 [LICENSE](LICENSE)。
