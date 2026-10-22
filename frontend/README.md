# 研迹 PaperTrail · React 前端

完整 Windows、PyCharm、令牌填写与联调步骤见 [前端使用说明](../docs/FRONTEND.md)。

要求 Node.js 24+。先在项目根目录的另一个终端启动后端：

```powershell
.\.venv\Scripts\python.exe run_backend.py
```

再在本目录执行：

```powershell
npm ci
npm run dev
```

打开 <http://127.0.0.1:5173/>，点击“连接知识库”，填根目录 `.env` 中 `API_TOKENS_JSON` 的令牌。不填模型平台 API 密钥，不新建前端密钥配置文件。

```powershell
npm test
npm run build
```

- `src/App.tsx`：知识库、上传队列、问答、证据和连接状态。
- `src/api.ts`：鉴权请求、超时、错误转换和问答范围校验。
- `src/AnswerText.tsx`：Markdown 与引用编号；禁止原始 HTML。
- `src/PdfViewer.tsx`：PDF.js 阅读器、指定页、缩放和下载。
- `src/Modal.tsx`：原生弹窗的焦点管理。
- `src/types.ts`：后端响应类型。
- `src/style.css`：桌面三栏、窄屏布局。
- `tests/api.test.ts`：10 项接口层测试，不调用真实模型。
- `scripts/copy-pdf-assets.mjs`：从安装的 PDF.js 包准备本地阅读器资源。

开发和构建预览均通过 Vite 将 `/api` 转发到本机 8000 端口。令牌只保存在页面内存，刷新后重新输入。问答记录仅在当前页保留；论文与索引由后端持久化。前端源码不包含演示假数据。
