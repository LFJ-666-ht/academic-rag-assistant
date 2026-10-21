# RAG 使用说明：首次安装、换电脑和日常运行

适用版本：后端 v0.2.1。更新日期：2026-10-21。命令按 Windows、PyCharm Terminal、PowerShell 编写。

首次使用按第 1～7 节操作；换电脑先看第 2 节的迁移说明；已配置成功后的日常运行看第 8 节。某一步出现报错时，先处理该步骤，不要继续执行后面的命令。

## 1. 准备软件与项目代码

- 安装 Python 3.12（64 位）、Git 和 PyCharm。这里用 Python 3.12 创建环境。
- 如果需要运行团队已经开发的 React 前端，还需安装与前端项目要求匹配的 Node.js；仅运行后端不用安装 Node.js。
- 在旧电脑确认最新代码已经 Commit 并 Push。只有 Commit、没有 Push，新电脑克隆不到这些修改。
- 新电脑在 PyCharm 选择“从版本控制获取 / Clone Repository”，粘贴自己的 GitHub 仓库地址并克隆。已有克隆时，先检查和保存本地修改，再拉取更新，不必重复克隆或新建仓库。
- 如果使用代码压缩包，先解压，再打开内部的项目目录。需要继续 Git 协作时优先使用原仓库克隆；压缩包不含 Git 历史。

项目根目录就是能同时看到 `backend/`、`.env.example`、`requirements.txt`、`prepare_models.py` 和 `run_backend.py` 的目录。后面的命令均在这个目录的 Terminal 中执行。

可先检查：

```powershell
Get-Location
Test-Path requirements.txt
Test-Path .env.example
Test-Path run_backend.py
```

后三条应输出 `True`。否则先进入正确的目录，不要在 `backend/`、`frontend/` 或 `.venv/` 中执行安装命令。

## 2. 换电脑时，哪些内容需要另外迁移

GitHub 同步源码；本项目的 `.gitignore` 排除了真实配置、论文数据和虚拟环境。新电脑只有 `.env.example`、没有 `.env` 是正常情况。

| 内容 | 是否需要迁移 | 操作 |
|---|---|---|
| 项目源码 | 需要 | 克隆原仓库，并确认包含最新修改 |
| 原项目 `.env` | 保留原配置时需要 | 安全复制到新项目根目录，不上传 GitHub |
| 完整 `data/` 或 `DATA_DIR` 指向的目录 | 想保留旧论文时需要 | 先停止旧后端，再整体复制 PDF、SQLite 和 Qdrant 数据 |
| BGE 模型缓存 | 可选 | 复制完整缓存，或在第 5 节重新下载 |
| `.venv/`、`venv/` | 不迁移 | 在新电脑重建，见第 3 节 |
| `.idea/`、`node_modules/` | 不迁移 | 新电脑配置解释器，前端依赖重新安装 |

迁移 `data/` 前，在旧电脑终端按 `Ctrl+C`，等待服务完全退出。新电脑也应先停止后端，再放入数据。不要把两套已经分别使用过的知识库目录混合覆盖；应先备份并选择要使用的完整数据副本。

保留 `.env` 中 `API_TOKENS_JSON` 对应的用户标识，例如 `demo-user`。论文按用户标识隔离：换令牌但保留同一标识仍对应同一用户；改成另一个标识会看到另一份文档列表。

如果原来 `DATA_DIR` 是旧电脑的绝对路径，且现在将数据放到新项目的 `data/` 下，把它改为：

```dotenv
DATA_DIR=./data
```

保持 `EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5` 等原模型配置不变，不要为了离线加载改成另一个模型名或路径。已有索引会核对模型标识。

拿不到旧配置时，按第 4 节创建新 `.env`；没有旧数据时，新电脑会建立空知识库，需要重新上传 PDF。两台电脑以后新增的论文不会通过 Git 自动同步。

## 3. 创建新电脑的 Python 环境

新电脑首次安装执行：

```powershell
py -3.12 --version
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

先确认第一条显示 Python 3.12。若 `py` 不存在或没有 3.12，先安装对应 Python 并重新打开 Terminal。如果已有 Python 3.12、但没有 `py` 启动器，可以确认 `python --version` 为 3.12 后用 `python -m venv .venv` 创建。

如果当前电脑的环境已经运行成功，跳过重建环境和重复安装。复制来的旧电脑虚拟环境不能当成新电脑环境使用；先将旧 `.venv` 改名备份，再创建新的 `.venv`。

在 PyCharm 的 Python 解释器设置中，选择新项目的 `.venv\Scripts\python.exe`。本文始终直接使用这个解释器运行，无需执行 `Activate.ps1`，也无需修改 PowerShell 执行策略。

`requirements-tested.txt` 只记录轻量测试环境的部分直接依赖，不包含完整 BGE 运行环境，不能代替 `requirements.txt`。

## 4. 创建并填写 `.env`（首次启动前必须完成）

### 4.1 找到模板并创建真实配置

在 PyCharm 左侧项目根目录找到 `.env.example`。如果刚从 GitHub 下载，通常只有这个模板。

在项目根目录 Terminal 执行：

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

这条命令只在 `.env` 不存在时复制模板；如果已迁移旧 `.env`，保留原文件。

`.env` 必须与 `run_backend.py` 同级，不能放在 `backend/` 或 `.venv/` 里，也不能叫 `.env.txt`。修改 `.env.example` 不会替代填写 `.env`。

### 4.2 设置项目访问令牌

如果旧 `.env` 中已有可用配置，保留即可。新建配置时执行：

```powershell
.\.venv\Scripts\python.exe -c "import secrets,json; print('API_TOKENS_JSON=' + json.dumps({secrets.token_urlsafe(32): 'demo-user'}))"
```

终端会打印一整行 `API_TOKENS_JSON=...`。这条命令只打印配置，不会自动写入文件。

打开 `.env`，将输出的整行复制进去，替换原来的 `API_TOKENS_JSON` 行，保存。不要重复追加两行同名配置。若迁移旧数据，将 `demo-user` 保持为旧配置中的真实用户标识。

该值必须是非空 JSON 对象：键是至少 16 个字符的随机令牌，值是非空用户标识。使用英文双引号；不要留下模板中的占位令牌，不要把 JSON 键和值写反。

### 4.3 设置模型服务

在同一个 `.env` 中填写以下配置：

| 配置 | 填写内容 |
|---|---|
| `DEEPSEEK_API_KEY` | 自己模型服务平台签发的真实密钥，只在本机填写 |
| `DEEPSEEK_MODEL` | 同一平台为自己账号开通的模型 ID，优先沿用旧电脑成功配置 |
| `DEEPSEEK_BASE_URL` | 同一平台的 API 地址，优先沿用旧电脑成功配置 |
| `DEEPSEEK_MAX_TOKENS` | 本项目目前设为 `4096` |
| `DEEPSEEK_REASONING_EFFORT` | 原方舟模型配置使用 `none`；换模型或平台需按其文档设置，不支持时留空 |
| `DATA_DIR` | 默认 `./data`，或新电脑实际存放知识库的位置 |

`.env.example` 里的方舟地址和模型 ID 是原项目联调配置示例，不表示每个账号都已开通。密钥、模型 ID、地址必须来自同一平台。不要只替换密钥却混用其他平台的地址。

本地模型设置保持为：

```dotenv
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
RERANKER_MODEL=BAAI/bge-reranker-base
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

两类凭据用途不同：

| 凭据 | 用途 | 使用位置 |
|---|---|---|
| `API_TOKENS_JSON` 中生成的随机令牌 | 访问自己的 RAG 后端 | Swagger 的 Authorize；只填令牌字符串 |
| `DEEPSEEK_API_KEY` | 后端调用模型平台 | 只放在后端 `.env`，不填到 Swagger，不写入前端源码 |

### 4.4 检查配置，不输出密钥

保存 `.env` 后，在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -c "import json; from pathlib import Path; from dotenv import dotenv_values; p=Path('.env'); d=dotenv_values(p); t=json.loads(d.get('API_TOKENS_JSON') or '{}'); print('env_exists:',p.is_file()); print('token_config_valid:',isinstance(t,dict) and bool(t) and all(isinstance(k,str) and len(k)>=16 and isinstance(v,str) and bool(v.strip()) for k,v in t.items())); print('model_key_set:',bool((d.get('DEEPSEEK_API_KEY') or '').strip()))"
```

三项应为 `True`。这只检查文件、令牌结构和模型密钥是否非空，不会请求模型平台，也不能证明平台密钥或模型 ID 有效。若出现 JSON 解析错误，重新生成并替换令牌配置行。

## 5. 在启动前准备 BGE 模型

新后端只从本地缓存加载模型。新电脑即使代码和依赖齐全，仍需要复制或下载两个 BGE 模型。

### 没有缓存：下载并检查

```powershell
.\.venv\Scripts\python.exe prepare_models.py --endpoint https://hf-mirror.com
```

等待命令完整结束。脚本下载配置、tokenizer 和权重，再启动离线检查，最后应看到：

```text
Embedding OK: (1, 512)
Reranker OK: ...
```

重排分数的具体数值可以不同。这一步不会调用 DeepSeek，也不会自动上传论文。虽然 `.env` 中设置了离线模式，准备脚本会在自己的下载进程中临时启用联网，无需手动反复改配置。

若要使用 Hugging Face 官方下载地址，可显式指定：

```powershell
.\.venv\Scripts\python.exe prepare_models.py --endpoint https://huggingface.co
```

### 已迁移缓存：只检查

默认缓存位置通常为 `C:\Users\你的用户名\.cache\huggingface\hub`；如果设置过 `HF_HOME`、`HF_HUB_CACHE` 或 `SENTENCE_TRANSFORMERS_HOME`，以实际配置为准。将两个模型的完整仓库缓存目录复制到新电脑对应位置，保留 `refs`、`snapshots`、`blobs` 等已有结构及引用关系，不要只复制一个权重文件。

默认两个目录名为 `models--BAAI--bge-small-zh-v1.5` 和 `models--BAAI--bge-reranker-base`。复制后执行：

```powershell
.\.venv\Scripts\python.exe prepare_models.py --check-only
```

如果复制不完整或缺少配置，用下载命令补齐缓存，不要修改池化方式来绕过错误。

## 6. 启动后端

```powershell
.\.venv\Scripts\python.exe run_backend.py
```

看到下面两类日志后，保持终端运行：

```text
Application startup complete.
Uvicorn running on http://127.0.0.1:8000
```

浏览器打开 [接口页面](http://127.0.0.1:8000/docs)。也可以打开 [健康检查](http://127.0.0.1:8000/api/health)，本版应返回 `status: ok` 和 `version: 0.2.1`。

健康检查成功只表示服务已启动，不代表模型缓存、上传和大模型问答均已验证，继续第 7 节。

同一个知识库目录只启动一个后端进程，不额外设置多个 workers，不同时运行另一个使用同一目录的索引脚本。关闭终端或按 `Ctrl+C` 会停止服务。

## 7. 第一次上传和问答

1. 在 `/docs` 右上角点击 **Authorize**，填 `API_TOKENS_JSON` 中的随机令牌，点击确认。只填令牌本身，不带引号、`Bearer`、JSON 大括号或变量名。
2. 展开 **GET `/api/documents`**，点击 **Try it out → Execute**。迁移成功时应看到原论文；新知识库列表为空属于正常情况。
3. 展开 **POST `/api/documents`**，点击 **Try it out**，在 `file` 处选择一篇有可提取文字的 PDF，点击 **Execute**。等待解析和建库完成；每次请求传一篇，可以连续上传多篇。
4. 再执行文档列表，确认新文件状态为 `ready`。上传相同文件返回 `duplicate: true` 表示去重命中，无需反复上传。
5. 展开 **POST `/api/search`**，使用下方 JSON 检查召回片段；该接口不调用大模型。
6. 展开 **POST `/api/chat`**，使用同样的 JSON 测试答案和引用。这一步需要模型平台网络和有效密钥。

```json
{
  "question": "这篇论文主要研究了什么问题？",
  "top_k": 5
}
```

未传 `document_ids` 时检索当前用户的全部论文；不要传空数组 `[]` 来表示全部。需要限定某一篇时，加入从文档列表复制的真实文档 UUID：

```json
{
  "question": "这篇论文采用了什么研究方法？",
  "top_k": 5,
  "document_ids": ["替换为文档列表中的真实UUID"]
}
```

第二段中的 UUID 是占位符，必须替换后再提交。实际状态看 **Server response**；Swagger 页面预列的 422 示例不是本次请求已经报错。

查看返回的 `answer`、`citations` 和 `evidence`，按文件名与 PDF 页码核对引用。要取原 PDF，可在已授权的 Swagger 中调用 **GET `/api/documents/{document_id}/file`**；直接打开返回链接不会自动携带访问令牌。页码为从 1 开始的 PDF 物理页码。

扫描版无可提取文字的 PDF 当前需要先做 OCR。答案引用编号合法只说明引用指向返回片段，不能替代内容核对。

## 8. 以后每天怎么用

已完成配置、依赖安装和模型准备后，只需在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe run_backend.py
```

再打开 `/docs` 或团队已经完成的前端。无需每次重新生成令牌、安装依赖、下载模型或上传已有论文。修改 `.env` 后需要重启后端。

如果团队已有前端，且 `frontend/` 中有 `package.json`，新电脑首次使用在另一个终端执行：

```powershell
cd frontend
if (Test-Path package-lock.json) { npm ci } else { npm install }
npm run dev
```

之后只需进入 `frontend/` 运行 `npm run dev`。打开终端显示的实际地址，后端终端保持运行。若前端端口与 `.env` 的 `CORS_ORIGINS` 不一致，调整为实际来源并重启后端。此后端修订包本身没有包含 React 前端；没有 `package.json` 时跳过这一段，继续使用 `/docs`。

## 9. 常见问题

### 报错：`API_TOKENS_JSON 必须是 {至少16位随机令牌: 用户标识} 的非空映射`

回到第 4 节：先确认存在 `.env`，再确认已经将生成的整行配置替换到 `.env` 并保存。只改 `.env.example` 不生效。令牌不能少于 16 个字符，用户标识不能为空。

如果第 4.4 节检查正常，但启动仍提示相同问题，当前终端可能存在覆盖文件配置的旧环境变量。执行：

```powershell
Remove-Item Env:API_TOKENS_JSON -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe run_backend.py
```

该命令只移除当前终端进程中的同名变量，不改写 `.env` 或 Windows 持久环境设置。若重新开终端后又发生，检查终端/IDE/系统中的同名配置。

### 报错：找不到 `.venv\Scripts\python.exe` 或 `requirements.txt`

先执行第 1 节的目录检查。前者通常需要在新电脑按第 3 节创建环境；后者通常是终端没有位于项目根目录。不要安装到一个解释器、又用另一个解释器启动。

### 报错：`ModuleNotFoundError`

使用同一虚拟环境重新安装项目依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 模型找不到、离线缓存缺失或缺少 Pooling 配置

停止后端，执行第 5 节的模型准备命令，直到两项检查完成。不要把 `HF_HUB_OFFLINE=1` 当成已经下载了模型；它只是规定运行时从缓存读取。

### 下载一直等待或 `ProxyError`

先检查下载终端是否还有进度或明确错误。模型下载地址在此说明中使用 `https://hf-mirror.com`。如果提示代理连接失败，且当前网络不需要代理，可以执行：

```powershell
.\.venv\Scripts\python.exe prepare_models.py --endpoint https://hf-mirror.com --no-proxy
```

该参数只影响此脚本及其子进程。需要代理才能联网的环境不要使用它；不要关闭 TLS 证书验证。依赖安装失败时先处理 pip 的实际错误，再继续模型准备。

### 401：没有有效的 `X-API-Key`

在 Swagger 重新 Authorize，填 `.env` 中令牌字符串；不要填整行 JSON 或 `DEEPSEEK_API_KEY`。重新生成令牌后，前端或 Swagger 中旧令牌也需要更新。

### 列表为空，但旧电脑有论文

检查完整数据是否已复制、`DATA_DIR` 是否指向新电脑的正确位置、访问令牌映射的用户标识是否保持一致。不要为“修复”空列表而删除原数据。

### Qdrant 提示目录已被其他进程占用，或端口 8000 已被占用

先找到自己已经启动的后端终端，按 `Ctrl+C` 正常退出，再启动一次。不要盲目删除 Qdrant 锁文件或终止不明进程。

### `/` 或 `/favicon.ico` 返回 404

本版后端入口是 `/docs`，尚未在根路径提供网页。打开 `http://127.0.0.1:8000/docs`。

### 上传成功，但问答提示模型调用失败

检查 `.env` 中平台密钥、模型 ID、API 地址是否匹配，账号是否可用、额度是否足够，以及新电脑能否访问该平台。Embedding/Reranker 的本地离线模式不代表 DeepSeek 问答可以断网运行。模型思考参数应符合自己平台和模型的接口约定。

### 仍然报错时怎么提供信息

复制终端错误文字，说明执行了哪条命令，以及前一步是否成功。只报告第 4.4 节的布尔检查结果，不发送真实密钥、令牌或完整 `.env`。

## 10. 提交与备份

本次使用说明更新只涉及根目录 `README.md` 和 `docs/USAGE.md`；可提交为 `docs: 补充首次运行、换电脑迁移与配置排错说明`。

可以提交源码、文档、依赖清单和不含秘密的 `.env.example`。不要提交 `.env`、`data/`、模型缓存、`.venv/`、`.idea/`、`__pycache__/`、`node_modules/` 和构建产物。完整备份知识库时，先停服务，再另行复制整个数据目录。

## 参考与验证范围

- [Python 3.12 虚拟环境说明](https://docs.python.org/3.12/library/venv.html)
- [Hugging Face 缓存目录与结构](https://huggingface.co/docs/huggingface_hub/guides/manage-cache)
- [Hugging Face 环境变量](https://huggingface.co/docs/huggingface_hub/en/package_reference/environment_variables)

命令和配置按当前包的 `run_backend.py`、`prepare_models.py`、`backend/main.py`、`.env.example` 核对。此文档更新没有在你的新电脑代执行安装或模型 API 调用；真实环境以各步输出为准。已有自动测试范围见 [VALIDATION.md](VALIDATION.md)。
