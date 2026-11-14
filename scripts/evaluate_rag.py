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
        answerable = case.get("answerable", True)
        if type(answerable) is not bool:
            raise ValueError(f"{case['id']}：answerable 必须为布尔值")
        history = case.get("history", [])
        if (
            not isinstance(history, list)
            or len(history) > 4
            or any(
                not isinstance(h, dict)
                or not isinstance(h.get("question"), str)
                or not 1 <= len(h["question"].strip()) <= 2000
                or not isinstance(h.get("answer", ""), str)
                or len(h.get("answer", "")) > 2000
                for h in history
            )
        ):
            raise ValueError(f"{case['id']}：history 最多包含 4 条有限长度的历史问答")
        if not isinstance(gold, list) or (answerable and not gold) or (not answerable and gold):
            raise ValueError(
                f"{case['id']}：可回答题需标注预期页码；无答案题需 answerable=false 且 expected_pages=[]"
            )
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
    return len(gold & found) / len(gold) if gold else None


def reciprocal_rank(expected, retrieved):
    gold = {(r["document_id"], r["page"]) for r in expected}
    if not gold:
        return None
    seen = set()
    for item in retrieved:
        key = (item["document_id"], item["page"])
        if key in seen:
            continue
        seen.add(key)
        if key in gold:
            return 1 / len(seen)
    return 0.0


def percentile(values, percent):
    """Linear interpolation, with an explicit definition for small datasets."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(rows):
    successful = [r for r in rows if r["status"] == "ok"]
    answerable = [r for r in rows if r.get("answerable", True)]
    scored = [r for r in successful if r.get("answerable", True)]
    unanswerable = [r for r in successful if not r.get("answerable", True)]
    return {
        "completed_cases": len(rows),
        "errors": len(rows) - len(successful),
        "mean_page_recall_successful_cases": statistics.mean(r["page_recall"] for r in scored)
        if scored
        else None,
        "mean_page_recall_errors_as_zero": sum(r["page_recall"] for r in scored) / len(answerable)
        if answerable
        else None,
        "mrr_successful_cases": statistics.mean(r["reciprocal_rank"] for r in scored)
        if scored
        else None,
        "mrr_errors_as_zero": sum(r["reciprocal_rank"] for r in scored) / len(answerable)
        if answerable
        else None,
        "latency_p50_seconds": percentile([r["seconds"] for r in successful], 50),
        "latency_p95_seconds": percentile([r["seconds"] for r in successful], 95),
        "unanswerable_cases": len(unanswerable),
        "unanswerable_empty_retrieval_rate": statistics.mean(
            r["empty_retrieval"] for r in unanswerable
        )
        if unanswerable
        else None,
        "answer_accuracy": "未自动评定；空检索率不等于拒答正确率，需人工逐项核验",
    }


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
        config_response = client.get("/api/retrieval/config")
        config = (
            config_response.json() if config_response.status_code == 200 else {"available": False}
        )
        rows = []
        output.parent.mkdir(parents=True, exist_ok=True)
        for case in cases:
            started = time.monotonic()
            row = {
                **case,
                "top_k": args.top_k,
                "human_answer_supported": None,
                "human_citations_supported": None,
                "human_abstention_correct": None,
            }
            try:
                response = client.post(
                    "/api/chat" if args.generate else "/api/search",
                    json={
                        "question": case["question"],
                        "document_ids": case["document_ids"],
                        "top_k": args.top_k,
                        "history": case.get("history", []),
                    },
                )
                response.raise_for_status()
                result = response.json()
                evidence = result["evidence"] if args.generate else result["chunks"]
                row.update(
                    result=result,
                    page_recall=page_recall(case["expected_pages"], evidence),
                    reciprocal_rank=reciprocal_rank(case["expected_pages"], evidence),
                    empty_retrieval=not evidence,
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
            report = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "mode": "generation" if args.generate else "retrieval",
                "configuration": config,
                "top_k": args.top_k,
                "warmup_requests": 0,
                **summarize(rows),
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
