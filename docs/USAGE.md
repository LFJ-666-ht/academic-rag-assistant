# 安装与使用

v0.4.1 · Windows / PowerShell

本指南说明环境配置、模型准备、文献问答、概念配图、数据迁移与故障处理。首次运行按第 1–4 节配置；功能操作见第 5–6 节；数据迁移、排错与测试见第 7–9 节。

## 1. 环境与代码

参考环境为 Python 3.12（64 位）、Git 和 Node.js 24+，可选用 PyCharm 作为开发环境。以下命令适用于 Windows PowerShell。

首次获取代码时，在选定的工作目录执行：

```powershell
git clone https://github.com/LFJ-666-ht/academic-rag-assistant.git rag-academic-assistant
cd rag-academic-assistant
```

已有代码时直接进入项目根目录，即包含 `run_backend.py` 和 `requirements.txt` 的目录。创建 Python 环境并安装运行依赖：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

PyCharm 解释器选择本项目的 `.venv\Scripts\python.exe`。已经跑通的电脑不重建环境；旧电脑的 `.venv` 不能直接复制到新电脑使用。`requirements-test.txt` 供轻量测试，`requirements-tested.txt` 是历史版本记录，二者都不能代替运行依赖。

## 2. 创建配置与项目访问令牌

仓库提供 `.env.example` 配置模板，真实密钥不随源码分发。在项目根目录创建本地配置：

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

只有不存在时才复制；已有 `.env` 直接保留。文件与 `run_backend.py` 同级，不能叫 `.env.txt`。只修改 `.env.example` 不会生效。

没有可用令牌时生成：

```powershell
.\.venv\Scripts\python.exe -c "import secrets,json; print('API_TOKENS_JSON=' + json.dumps({secrets.token_urlsafe(32): 'demo-user'}))"
```

**把打印出的整行复制到 `.env`，替换同名行并保存。命令只打印，不会自动写文件。** 冒号左边是至少 16 位随机令牌，右边是用户标识；使用英文双引号，不重复添加配置行。迁移旧数据时保留原用户标识。

## 3. 模型服务配置

DeepSeek 负责文献问答与配图描述，Seedream 负责图片生成。两组配置相互独立；已有可用文本服务时，可保留其配置并按需启用配图。

| 配置项 | 填写内容 |
|---|---|
| `DEEPSEEK_API_KEY` | 文本模型服务平台的真实密钥 |
| `DEEPSEEK_MODEL` | 同一平台为账号开通的完整模型 ID，不要带多余的 `L=` |
| `DEEPSEEK_BASE_URL` | 文本服务地址；保留原来已经跑通的值 |
| `SEEDREAM_API_KEY` | 有 Seedream 权限的火山方舟 API Key |
| `SEEDREAM_MODEL` | `doubao-seedream-4-0-20260415` |
| `SEEDREAM_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3` |

Seedream 示例使用已完成单次接口验证的型号。使用前需确认模型对当前平台账号及密钥可用，在根目录 `.env` 配置：

```dotenv
SEEDREAM_API_KEY=替换为具备模型权限的API密钥
SEEDREAM_MODEL=doubao-seedream-4-0-20260415
SEEDREAM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
```

把第一行的中文占位值完全替换掉。地址只写到 `/api/v3`，不要再追加 `/images/generations`。同名配置已有就改原行，不重复追加。DeepSeek 若也来自同一方舟项目且密钥有两个模型权限，可以两处填写同一密钥；来自不同平台时各填各的密钥和地址。

### 3.1 DeepSeek V4.1 Flash 261017 长回答配置

以下展示项目对方舟 `deepseek-v4-1-flash-261017` 的配置预设；适用性取决于实际平台和账号。保留已有 API 密钥，在根目录 `.env` 修改同名项：

```dotenv
DEEPSEEK_MODEL=deepseek-v4-1-flash-261017
DEEPSEEK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
DEEPSEEK_MAX_TOKENS=393216
DEEPSEEK_TOKEN_PARAMETER=max_tokens
DEEPSEEK_REASONING_EFFORT=max
DEEPSEEK_THINKING=enabled
DEEPSEEK_TIMEOUT_SECONDS=300
DEEPSEEK_TOTAL_TIMEOUT_SECONDS=7200
DEEPSEEK_IMAGE_PROMPT_MAX_TOKENS=4096
```

`393216 = 384 × 1024` 是当前代码的请求预算预设，不代表已经验证平台能够完成该长度的输出。预算不是目标回答长度，token 也不等于字数；实际支持范围及推理选项由所用平台决定。较大的预算和推理强度可能增加响应时间与用量，应结合平台限制和任务需求设置。配置变化后需重启后端。

代码默认只在明确识别到方舟域名及型号时应用对应预设：261017 为 384K / max，260910 为 128K / max。显式配置优先。其他平台、其他型号或 `ep-...` 推理接入点不猜测能力，`auto` 使用平台默认；应按该接入点实际模型填写参数。`max_tokens` 和 `max_completion_tokens` 不会同时发送。若平台返回参数错误，应核对控制台当前的接口限制，程序不会自动重试或更换模型。

上游使用流式请求，网页显示检索、思考状态和正文草稿；正文完成后再校验引用编号。不会向前端返回模型内部思考文本。默认单次上游读取空闲超时 300 秒，前端空闲等待 360 秒，持续收到内容会刷新空闲计时。后端总生成期限默认 7200 秒，在收到新块时检查；阻塞读取仍受空闲超时约束。公网部署的网关也需关闭响应缓冲，并配置足够的读取超时。

达到输出/上下文上限或上游断线且已有正文时，返回未完成标记和已校验的引用；没有正文时返回具体错误。浏览器到后端断线时只能保留未校验草稿，不能当成完整答案。不会自动重新发起收费请求。关闭页面或中止等待不保证平台立即停止计费，重新提交前应检查平台记录。

配图描述独立使用 4096 token 预算；已识别的 V4.1 模型关闭该短任务的深度思考。Seedream 参数不受文本回答预算影响。

**能力范围：** 问答检索默认 10 条、可选最多 30 条片段。更大输出预算不会自动把整篇论文或全部论文送入模型；当前也未接入 PDF 图片理解、完整多轮上下文或百万 token 全文分析。

配置实现见 `backend/generation.py`，测试覆盖范围见 [测试与验证](VALIDATION.md)。自动化测试验证请求字段构造，不验证真实模型的最大输出能力。

网页“连接知识库”填写的是 **`API_TOKENS_JSON` 中冒号左侧的随机令牌**，不带引号。两个模型密钥只放后端 `.env`，不写进 React，不创建前端密钥文件。没有配置 Seedream 时问答仍可用。

不打印密钥的检查命令：

```powershell
.\.venv\Scripts\python.exe -c "import json; from dotenv import dotenv_values; d=dotenv_values('.env'); t=json.loads(d.get('API_TOKENS_JSON') or '{}'); print('token_valid:',isinstance(t,dict) and bool(t) and all(isinstance(k,str) and len(k)>=16 and isinstance(v,str) and bool(v.strip()) for k,v in t.items())); print('text_key_set:',bool(d.get('DEEPSEEK_API_KEY'))); print('image_key_set:',bool(d.get('SEEDREAM_API_KEY')))"
```

这只检查配置是否存在，不证明密钥有效或账号有权限。配置修改后重启后端。

## 4. 准备本地模型并启动

默认 Embedding 为 `BAAI/bge-small-zh-v1.5`，Reranker 为 `BAAI/bge-reranker-base`。新电脑没有缓存时，在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe prepare_models.py --endpoint https://huggingface.co
```

如需使用镜像，可在确认来源与网络可用性后将地址替换为 `https://hf-mirror.com`。等待脚本完成下载和检查，出现 `Embedding OK: (1, 512)` 与 `Reranker OK: ...`。已有缓存时只需：

```powershell
.\.venv\Scripts\python.exe prepare_models.py --check-only
```

下载不完整时补齐完整模型缓存，不要随意改 Pooling 或 Embedding 模型来绕过错误。`run_backend.py` 设置本进程的 HF 离线模式，避免运行时重复联网下载；此设置不影响 DeepSeek/Seedream 的 HTTP 请求。

**终端一：后端**

```powershell
.\.venv\Scripts\python.exe run_backend.py
```

看到 `Application startup complete.` 后保持运行。健康检查 <http://127.0.0.1:8000/api/health> 应显示 `version: 0.4.1`，但不代表模型调用已成功。后端启动入口为根目录的 `run_backend.py`。

**终端二：前端**

```powershell
cd frontend
npm ci
npm run dev
```

`npm ci` 在首次安装或依赖变更时运行。平时只运行 `npm run dev`。打开 <http://127.0.0.1:5173/>，连接知识库。前端默认将 `/api` 转发到 8000 端口，不需要额外前端 `.env`。

只有一个后端进程可以使用同一份本地 Qdrant 和图片任务目录，不设置多 worker，不同时运行两个后端。

## 5. 上传论文和引用问答

连续上传多篇有可提取文字的 PDF，等待解析和索引完成。单篇上限 30 MiB、500 页、10000 分块；同用户相同文件会去重。扫描版全无文字时需要先 OCR。

选择全部或指定论文后提问。回答中的引用可查看原文片段与 PDF 页码；页码是 PDF 物理页码，不一定等于论文印刷页码。引用编号通过检查不代表每个结论都正确，需结合原文核对。

也可通过 <http://127.0.0.1:8000/docs> 调试。先 Authorize 填项目令牌；按 `GET /api/documents`、`POST /api/documents`、`POST /api/search`、`POST /api/chat` 的顺序验证。搜索/聊天请求示例：

```json
{"question":"论文采用了什么方法？","top_k":5,"document_ids":null}
```

`null` 是全部自己的论文，空数组 `[]` 表示不选择论文；指定范围填真实文档 UUID。

## 6. DeepSeek + Seedream 配图

- **直接描述：** 右上角“AI 配图”→输入画面描述→Seedream 生成一张 2K 图片。
- **根据论文：** “AI 配图”→“根据论文”→选择论文和主题→DeepSeek 整理描述→检查原文、修改草稿→Seedream 出图。
- **从回答开始：** 点击回答下方“生成配图”，带入问题与相关论文范围，再整理草稿。

DeepSeek 的草稿基于重新检索到的最多 5 个片段，不是发送全文。点击 Seedream 生成按钮前不会调用图片模型；文本草稿和图片分别消耗对应平台额度。DeepSeek 剩余 token 不代表还有图片额度。

生成状态包括排队中、生成中、成功、失败。关闭窗口后，已受理任务继续；同一用户重新打开可查看最近 30 条、预览和下载。每个用户最多同时处理一张，服务器队列上限 8 个。轮询只查本地状态，不会反复调用 Seedream。

网络中断后若出现“提交结果尚未确认”，使用“确认提交状态”复用同一任务编号。平台调用超时或后端停止时不要连续新建任务，先查看平台调用记录；程序不会自动重试图片生成。

图片适合作概念配图和报告插画，不代替精确数据图或实验结果。文字、结构和学术含义需人工检查。当前版本没有图生图、组图或图片删除界面；最近 30 条只是显示窗口，文件不会自动过期。论文删除也不会自动删除已保存图片的来源片段快照。

## 7. 数据迁移与备份

1. 在新电脑获取与原环境匹配的代码版本，重新创建 `.venv` 并安装前后端依赖。
2. 安全复制根目录 `.env`，保留用户标识和 Embedding 模型设置；不要通过公开 Git 传密钥。
3. 停止旧后端，将整个 `DATA_DIR`（默认 `data/`）复制过去，包括 PDFs、SQLite、Qdrant 和 `images/`。不要只复制 PDF、只复制向量或把两份现有库直接混合。
4. 重新下载 BGE，或复制完整 Hugging Face 缓存（保留 refs/snapshots/blobs 等结构），再运行 `--check-only`。
5. 用同一用户标识启动检查列表、检索、引用和图片历史。两台机器各自新增的数据不会通过 Git 自动同步。

## 8. 常见问题

| 问题 | 处理 |
|---|---|
| `API_TOKENS_JSON` 非空映射报错 | 检查是否真的创建根目录 `.env`、是否粘贴了完整配置行、是否英文双引号和令牌至少 16 位 |
| 配置正确但仍用旧值 | 查看 PyCharm/当前终端是否有同名环境变量覆盖；可移除当前 PowerShell 的 `Env:API_TOKENS_JSON` 后重启，勿打印密钥 |
| 模型下载卡住/离线加载失败 | 单独运行 `prepare_models.py --check-only` 查看缺失；确认网络或完整缓存，不重复启动多个后端 |
| 模型不存在、HTTP 404 | 核对对应平台的地址、模型 ID、开通状态；检查 ID 前是否误加 `L=` |
| 401/403/配图无权限 | 区分项目令牌与平台密钥；平台权限错误到服务商核对 |
| 配图接口不存在 | 检查运行版本与 API 路径，确认从项目根目录通过 `run_backend.py` 启动 |
| 配图尚未配置 | 填写 SEEDREAM 三项并重启；普通问答仍可用 |
| 配额或频率受限 | 核对图片模型额度和限流，不能用文本 token 余额判断 |
| 自动刷新暂停 | 检查后端/网络，点击刷新；不要反复重新生成图片 |
| 图片完成但无法预览 | 点击重新读取，检查 `data/images/` 文件是否仍存在 |
| 看不到旧论文/图片 | 检查 DATA_DIR 是否正确，以及令牌映射的用户标识是否与原来一致 |
| Qdrant 被占用 | 停止另一个使用相同数据目录的后端实例，等待退出后再启动；保留数据库与锁文件 |

## 9. 测试与构建

根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm test
npm run build
```

这些测试使用模拟模型，不调用真实收费接口。执行结果用于验证程序逻辑，不等同于实际模型输出质量或浏览器完整流程验收。具体测试记录与范围见 [测试与验证](VALIDATION.md)。上述启动方式仅监听本机地址，未包含公网网关、HTTPS 或生产进程管理配置。
