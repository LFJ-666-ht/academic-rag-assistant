# 验证记录

执行环境 Python 3.12，项目根目录执行 `python -m pytest -q tests`。
结果：12 passed（1.77 秒）；1 条 FastAPI/Starlette 测试客户端的 httpx 迁移弃用提醒，没有测试失败。

已执行：
1. 持久化重启、多篇论文、重复上传、删除后文件和检索清理。
2. 不同用户的列表、向量/关键词检索、指定文件访问和删除隔离。
3. RRF 按 ID 保留同文本不同出处，避免原 zip 错配。
4. 真正 BM25 的中英文关键词和无命中分支。
5. 空知识库不调用生成、引用 ID 合法性、缺少引用标志。
6. 模型失败导致入库回滚，可再次上传。
7. 重启后清理被中断的删除任务。
8. Embedding 模型变更拒绝复用旧索引。
9. 无效 PDF 和全空文本 PDF 拒绝。
10. FastAPI 认证、上传、问答、PDF、选择范围及删除接口。
11. 文件扩展名和大小限制。
12. DeepSeek HTTP 请求构造、模型参数与响应解析（MockTransport）。

使用真实：PyMuPDF 生成与解析 PDF、SQLite、磁盘 Qdrant、BM25/RRF、FastAPI TestClient。
使用替身：BGE Embedding、BGE Reranker、DeepSeek HTTP 服务。确定性测试向量只用在 tests 中，生产代码不会静默降级成假模型。

未验证：真实 BGE 权重下载/推理、显卡配置、DeepSeek 实际账户权限/余额/模型可用性、真实中文/英文多栏论文检索质量、Windows 安装、React 页面、公网部署、性能压力测试。没有使用用户 DeepSeek 密钥，没有宣称已完成端到端真实问答。

一次初始测试从项目外层目录启动，因 backend 不在导入路径而收集失败；切换 README 指定的项目根目录后，全部 12 项通过。
