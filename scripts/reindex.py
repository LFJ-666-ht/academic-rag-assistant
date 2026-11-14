"""Rebuild PDF chunks and embeddings into a NEW data directory, never in place."""

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(
        description="PaperTrail 离线索引重建；保留原知识库与所有研究记录"
    )
    parser.add_argument("--source-data-dir", type=Path, help="默认读取项目 .env 的 DATA_DIR")
    parser.add_argument("--output-data-dir", required=True, type=Path, help="新的、尚不存在的目录")
    parser.add_argument(
        "--embedding-model", help="默认沿用 .env；可设为 BAAI/bge-m3 或本地模型目录"
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    # HF-related environment is loaded before sentence-transformers imports.
    from backend.core import Settings
    from backend.reindexing import rebuild

    settings = Settings.from_env()
    settings = replace(
        settings,
        data_dir=(args.source_data_dir or settings.data_dir).resolve(),
        embedding_model=args.embedding_model or settings.embedding_model,
    )
    report = rebuild(settings, args.output_data_dir, progress=lambda text: print(text, flush=True))
    print(f"重建成功：{len(report['documents'])} 篇论文，{report['total_chunks']} 个片段。")
    print("原数据与 .env 均未切换。验证后在 .env 中同时设置以下两项，再启动后端：")
    print("DATA_DIR=" + str(args.output_data_dir.resolve()).replace("\\", "/"))
    print("EMBEDDING_MODEL=" + settings.embedding_model)
    print("若使用自定义名称的本地 bge-m3 目录，请将 EMBEDDING_QUERY_INSTRUCTION 留空。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"重建未完成：{exc}。原知识库保留，不要修改 .env 切换目录。") from None
