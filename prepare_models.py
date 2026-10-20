"""Download complete BGE caches, then verify them in a fresh offline process.

Existing installations: python prepare_models.py --check-only
Download via mirror: python prepare_models.py --endpoint https://hf-mirror.com
"""
"""v0.2.1"""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def configure_environment(args):
    from dotenv import load_dotenv

    root = Path(__file__).resolve().parent
    os.chdir(root)
    load_dotenv(root / ".env")
    # All settings must precede imports of huggingface_hub/transformers.
    mode = "1" if args.check_only else "0"
    os.environ["HF_HUB_OFFLINE"] = mode
    os.environ["TRANSFORMERS_OFFLINE"] = mode
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    if args.endpoint:
        os.environ["HF_ENDPOINT"] = args.endpoint.rstrip("/")
    if args.no_proxy:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            os.environ.pop(key, None)
        os.environ["NO_PROXY"] = "*"
        os.environ["no_proxy"] = "*"


def check_models(embedding, reranker):
    from sentence_transformers import CrossEncoder, SentenceTransformer

    model = SentenceTransformer(embedding, local_files_only=True, token=False)
    vectors = model.encode(["这是一条模型加载测试。"], normalize_embeddings=True)
    print("Embedding OK:", vectors.shape, flush=True)
    # Do not force a fallback Pooling mode: it could invalidate existing vectors.
    if embedding == "BAAI/bge-small-zh-v1.5" and vectors.shape != (1, 512):
        raise RuntimeError("BGE small zh 输出维度异常，预期为 (1, 512)")
    del model
    ranker = CrossEncoder(reranker, max_length=512, local_files_only=True, token=False)
    scores = ranker.predict([("什么是 RAG？", "RAG 结合检索与文本生成。")])
    print("Reranker OK:", scores, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="只检查现有缓存，不下载")
    parser.add_argument("--endpoint", help="可选，例如 https://hf-mirror.com")
    parser.add_argument("--no-proxy", action="store_true", help="仅在当前脚本及子进程中禁用代理")
    args = parser.parse_args()
    configure_environment(args)
    embedding = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    reranker = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-base")
    if args.check_only:
        check_models(embedding, reranker)
        return 0

    from huggingface_hub import snapshot_download

    supported = {"BAAI/bge-small-zh-v1.5", "BAAI/bge-reranker-base"}
    for model_id in dict.fromkeys([embedding, reranker]):
        if Path(model_id).is_dir():
            print("使用已有模型目录:", model_id, flush=True)
            continue
        if model_id not in supported:
            raise ValueError("自动下载脚本仅支持默认的两个 BGE 模型；其他模型请按其文档准备完整缓存")
        print("准备模型:", model_id, flush=True)
        snapshot_download(
            repo_id=model_id,
            # Includes 1_Pooling/config.json; safetensors avoids duplicate .bin
            # weights and leaves ONNX/OpenVINO exports out of this download.
            allow_patterns=["*.json", "*.safetensors", "vocab.txt", "sentencepiece.bpe.model"],
            cache_dir=os.getenv("SENTENCE_TRANSFORMERS_HOME") or None,
            token=False, max_workers=2,
        )
    # A fresh process is necessary because HF reads the offline flags on import.
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--check-only"], check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
