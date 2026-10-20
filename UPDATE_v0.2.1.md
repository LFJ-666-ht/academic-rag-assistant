# v0.2.1 修改汇总与替换步骤

适用：你在 2026-10-19 上传的 `rag-academic-assistant (2).zip`。
这是后端修订包，整理对话中已经确认的修复；React 前端尚未包含在本次包中。

## 1. 修改了什么

| 文件 | 修改内容 | 对应现象 |
|---|---|---|
| `backend/core.py` | Embedding 和 Reranker 均显式使用 `local_files_only=True` | 权重已下载，仍访问 Hugging Face 的可选配置并超时 |
| `backend/main.py` | 提前读取项目根目录 `.env`；离线标志默认开启；版本号 0.2.1 | `.env` 设置时机和启动环境不一致 |
| `backend/generation.py` | 默认输出上限 4096，可用 `DEEPSEEK_MAX_TOKENS` 调整 | 原来 2000 的预算被思考/答案耗尽，返回长度限制 |
| `backend/generation.py` | 当前方舟精确模型自动使用 `reasoning_effort=none`；允许环境配置覆盖 | 当前模型默认思考较长；同时避免向其他模型乱传字段 |
| `backend/generation.py` | 保留截断、空答案、非法引用编号的拦截 | 不把半截输出当正常答案 |
| `backend/generation.py` | 区分检索片段不足与全文缺失；区分表格整理与直接引文 | 回答措辞不够准确，重组表格字段被称为原文引用 |
| `run_backend.py`（新增） | 一条命令离线启动；固定在原项目目录读取 `./data`；单 worker | 每次开终端需要重新设置变量，运行目录可能不同 |
| `prepare_models.py`（新增） | 按需下载完整缓存并离线检查 | Pooling 配置或 Reranker 缓存不完整；支持正确的 `.com` 镜像地址 |
| `.env.example` | 更新配置示例，无真实密钥 | 便于新成员配置；旧用户保留自己的 `.env` |
| `tests/test_backend.py`、`docs/VALIDATION.md`、`requirements-tested.txt` | 增加针对性回归检查，记录实际验证范围 | 防止原有持久化/认证/引用行为回退 |
| `README.md`、`docs/API.md`、`docs/DEVELOPMENT.md` | 同步运行步骤和开发进度 | 避免继续照旧说明联网加载或覆盖已有配置 |

本次保持 SQLite 表结构、Qdrant 集合名、模型 ID、分块策略、向量维度、查询前缀、归一化和已有接口字段不变。没有改动你的论文数据，也没有执行任何 Git 提交或推送。

## 2. 在原项目中替换

1. 在运行后端的终端按 Ctrl+C，等进程完全停止。
2. 解压本包，把内部 `rag-academic-assistant` 目录里的代码合并到你原来的同名项目。不要把整个旧项目删掉后换成新目录，也不要再嵌套一层同名目录。
3. 更新上表列出的文件；`run_backend.py`、`prepare_models.py` 和本文放在与 `backend/` 同级的位置。其他源码保留原样。团队另有修改时，先用 PyCharm 对比再合并。
4. 保留你自己的 `.env`、`data/`、`venv/` 或 `.venv/`、`.git/`、`.idea/` 和已有前端目录。本包不包含这些目录及真实配置，不会通过解压操作替你创建密钥、清空数据库或重建仓库。
5. 已有环境跑通后，不需要为本次修订重新安装所有依赖或重新上传论文。

## 3. 现有 .env 只补齐以下设置

先保留当前已成功调用的 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`、`API_TOKENS_JSON` 和 `DATA_DIR`。不要把 `.env.example` 覆盖到已有 `.env`。

下面是你当前方舟模型的设置；同名配置已存在时修改原行，避免重复追加：

```dotenv
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
DEEPSEEK_MAX_TOKENS=4096
DEEPSEEK_REASONING_EFFORT=none
```

当前已验证的服务配置为：

```dotenv
DEEPSEEK_MODEL=deepseek-v4-1-flash-260910
DEEPSEEK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
```

保持 `.env` 中原来的模型名：

```dotenv
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
RERANKER_MODEL=BAAI/bge-reranker-base
```

离线运行继续使用这些 ID 查找缓存，不需要改成本地模型路径。旧知识库会检查 Embedding 名称，换名字可能导致“模型与现有索引不一致”。没有选择更换模型时不要清空 `data/`。

`DEEPSEEK_REASONING_EFFORT` 是供应商相关选项。如果日后改用其他模型，按其文档设置；不支持时写 `DEEPSEEK_REASONING_EFFORT=`，代码会省略该字段。没有设置该变量时，代码仅针对当前方舟地址与上述精确模型 ID 默认填入 `none`。

## 4. 以后用一条命令启动

在原项目、原虚拟环境的终端执行：

```powershell
python run_backend.py
```

然后打开 `http://127.0.0.1:8000/docs`，点击 Authorize 填原来的访问令牌（仅令牌字符串，不是 JSON，不是模型平台密钥）。检查 `/api/health` 版本为 `0.2.1`，再查看原来的文档列表。

`run_backend.py` 会强制在当前后端进程中启用 HF 离线模式，不用每次手动输入 PowerShell 环境变量命令；它不会改变 Windows 系统环境变量。配置仍从原项目的 `.env` 加载。直接运行 Uvicorn 的方式仍可用，但需要从项目根目录启动；日常推荐使用新入口。

根路径 `/` 和 `/favicon.ico` 的 404 不影响后端，当前 API 文档入口是 `/docs`。Swagger 下方列出的 422 是接口可能的校验响应，不代表本次请求实际失败；看 Server response 的状态码。

## 5. 仅在模型缓存缺失时使用准备脚本

你的电脑已经完成两个模型的实际问答，可直接使用已有缓存。需要排查时：

```powershell
python prepare_models.py --check-only
```

预期显示 `Embedding OK: (1, 512)` 和 `Reranker OK: ...`。重排输出是相关性分数，不是概率。

首次安装或缺少配置时，停止后端后执行：

```powershell
python prepare_models.py --endpoint https://hf-mirror.com
```

脚本仅在下载进程中开启联网，包含 BGE 官方 `1_Pooling/config.json`，下载完成后由新进程按离线方式验证。默认模型采用 safetensors，不重复拉取 `.bin`。它不会修改 `.env` 中的模型 ID 或现有 `data/`。

如果提示代理连接失败，且你的网络不需要代理，可执行：

```powershell
python prepare_models.py --endpoint https://hf-mirror.com --no-proxy
```

镜像可能受网络环境影响，`.net` 不是本次成功联调使用的地址。不要关闭 TLS 校验，也不要用修改池化方式来掩盖配置缺失。

## 6. 验证与提交

在项目根目录执行轻量回归：

```powershell
python -m pip install -r requirements-test.txt
python -m pytest -q tests
```

回归不下载模型、不调用真实模型 API，详细结果见 `docs/VALIDATION.md`。提示词优化不等于自动证明答案正确，引用仍需依据原文核对。

可如实将上表修改作为一次修复提交，例如 `fix: stabilize offline model loading and cited answers`。提交前查看差异；保持 `.env`、论文数据、模型缓存、虚拟环境和 IDE 本机文件不入库。保留现有 Git 历史。

## 7. 后续开发范围

当前已经有真实单篇问答、重启保留数据、两份不同文档共存及指定第二份文档问答的样例。当前返回的 PDF URL 页码与元数据对应，但前端的鉴权取 PDF、页码跳转仍需实际联调。

下一阶段继续 React + TypeScript：文档列表、上传、选择文档范围、问答、引用卡和 PDF 预览。分块去重/参考文献过滤、表格版面恢复、OCR、语义级引文核验和完整评测集尚未实现，本次没有把这些计划混入已验证功能。
