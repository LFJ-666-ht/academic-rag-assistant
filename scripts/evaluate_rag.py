"""Local, annotated RAG evaluation. Default: retrieval only, no paid generation.

Run from the project root. Outputs contain paper text; keep them out of Git.
Page recall is a retrieval metric, not the factual accuracy of an answer.
"""

import argparse
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
from dotenv import load_dotenv


def validate_cases(value):
    cases = value.get("cases") if isinstance(value, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases 必须包含至少一条已标注的问题")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or case["id"] in seen:
            raise ValueError("每条用例必须有唯一的字符串 id")
        seen.add(case["id"])
        question, ids, gold = (
            case.get("question"),
            case.get("document_ids"),
            case.get("expected_pages"),
        )
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
            raise ValueError(f"{case['id']}：问题无效")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100:
            raise ValueError(f"{case['id']}：请选择真实论文 UUID")
        for did in ids:
            if not isinstance(did, str):
                raise ValueError("论文 UUID 必须为字符串")
            UUID(did)
        if not isinstance(gold, list) or not gold:
            raise ValueError(f"{case['id']}：请人工标注至少一处预期来源页码")
        for ref in gold:
            if (
                not isinstance(ref, dict)
                or ref.get("document_id") not in ids
                or type(ref.get("page")) is not int
                or ref["page"] < 1
            ):
                raise ValueError(f"{case['id']}：预期来源必须属于所选论文，且页码为正整数")
    return cases


def page_recall(expected, retrieved):
    gold = {(r["document_id"], r["page"]) for r in expected}
    found = {(r["document_id"], r["page"]) for r in retrieved}
    return len(gold & found) / len(gold)


def main():
    parser = argparse.ArgumentParser(description="PaperTrail 检索评测；可选真实模型生成")
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--list-documents", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--top-k", type=int, choices=range(1, 31), default=10)
    parser.add_argument(
        "--generate", action="store_true", help="每个问题调用文本模型，可能产生费用；不自动重试"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    token = os.getenv("PAPERTRAIL_EVAL_TOKEN", "").strip()
    if not token:
        mapping = json.loads(os.getenv("API_TOKENS_JSON", "{}"))
        if isinstance(mapping, dict) and len(mapping) == 1:
            token = next(iter(mapping))
    if not token:
        parser.error(
            "请通过 PAPERTRAIL_EVAL_TOKEN 指定已有项目访问令牌；多用户配置不自动选择。不要填写模型密钥。"
        )
    if not args.list_documents and not args.cases:
        parser.error("先 --list-documents 获取论文 UUID，再填写 --cases 指定的标注文件")
    cases = (
        validate_cases(json.loads(args.cases.read_text(encoding="utf-8-sig"))) if args.cases else []
    )
    output = args.output or Path("evaluation-results") / (
        datetime.now().strftime("%Y%m%d-%H%M%S") + ".json"
    )
    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        headers={"X-API-Key": token},
        trust_env=False,
        timeout=httpx.Timeout(7200, connect=15),
    ) as client:
        response = client.get("/api/documents")
        response.raise_for_status()
        docs = response.json()["documents"]
        if args.list_documents:
            for doc in docs:
                print(f"{doc['document_id']} | {doc['pages']} 页 | {doc['filename']}")
            return
        available = {d["document_id"]: d["pages"] for d in docs}
        for case in cases:
            if any(d not in available for d in case["document_ids"]) or any(
                r["page"] > available[r["document_id"]] for r in case["expected_pages"]
            ):
                raise ValueError(f"{case['id']}：论文不存在或人工标注的页码越界；尚未开始模型调用")
        rows = []
        output.parent.mkdir(parents=True, exist_ok=True)
        for case in cases:
            started = time.monotonic()
            row = {
                **case,
                "top_k": args.top_k,
                "human_answer_supported": None,
                "human_citations_supported": None,
            }
            try:
                response = client.post(
                    "/api/chat" if args.generate else "/api/search",
                    json={
                        "question": case["question"],
                        "document_ids": case["document_ids"],
                        "top_k": args.top_k,
                    },
                )
                response.raise_for_status()
                result = response.json()
                evidence = result["evidence"] if args.generate else result["chunks"]
                row.update(
                    result=result,
                    page_recall=page_recall(case["expected_pages"], evidence),
                    status="ok",
                )
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                row.update(
                    status="error",
                    error_type=type(exc).__name__,
                    http_status=getattr(getattr(exc, "response", None), "status_code", None),
                )
            row["seconds"] = round(time.monotonic() - started, 3)
            rows.append(row)
            success = [r for r in rows if r["status"] == "ok"]
            report = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "mode": "generation" if args.generate else "retrieval",
                "completed_cases": len(rows),
                "errors": len(rows) - len(success),
                "mean_page_recall_successful_cases": statistics.mean(
                    r["page_recall"] for r in success
                )
                if success
                else None,
                "answer_accuracy": "未自动评定，请人工逐项核验",
                "cases": rows,
            }
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{case['id']}: {row['status']} ({row['seconds']}s)")
    print(f"评测记录：{output}。这是实测检索记录，不等于回答准确率。")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, httpx.HTTPError) as exc:
        raise SystemExit(
            f"评测未完成：{type(exc).__name__}。请检查配置、标注文件和后端；没有自动重试。"
        ) from None
