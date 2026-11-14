"""Page-recall semantics and annotation validation, not model-quality assertions."""

from uuid import uuid4

import pytest

from scripts.evaluate_rag import page_recall, percentile, reciprocal_rank, summarize, validate_cases


def test_recall_deduplicates_pages_and_distinguishes_documents():
    expected = [{"document_id": "a", "page": 2}, {"document_id": "b", "page": 2}]
    retrieved = [{"document_id": "a", "page": 2}] * 5
    assert page_recall(expected, retrieved) == 0.5
    assert page_recall(expected, []) == 0
    assert page_recall(expected, retrieved + [{"document_id": "b", "page": 2}]) == 1


def test_template_cannot_run_without_real_annotations():
    with pytest.raises(ValueError):
        validate_cases(
            {
                "cases": [
                    {
                        "id": "Q1",
                        "question": "q",
                        "document_ids": ["REPLACE_WITH_DOCUMENT_UUID"],
                        "expected_pages": [
                            {"document_id": "REPLACE_WITH_DOCUMENT_UUID", "page": 1}
                        ],
                    }
                ]
            }
        )
    did = str(uuid4())
    case = {
        "id": "Q1",
        "question": "q",
        "document_ids": [did],
        "expected_pages": [{"document_id": did, "page": 1}],
    }
    assert validate_cases({"cases": [case]}) == [case]
    with pytest.raises(ValueError):
        validate_cases({"cases": [case, case]})
    with pytest.raises(ValueError):
        validate_cases({"cases": [{**case, "expected_pages": []}]})


def test_unanswerable_cases_are_explicit_and_not_scored_as_recall():
    case = {
        "id": "N1",
        "question": "Absent fact?",
        "document_ids": [str(uuid4())],
        "answerable": False,
        "expected_pages": [],
    }
    assert validate_cases({"cases": [case]}) == [case]
    assert page_recall([], []) is None and reciprocal_rank([], []) is None
    with pytest.raises(ValueError):
        validate_cases(
            {
                "cases": [
                    {
                        **case,
                        "expected_pages": [{"document_id": case["document_ids"][0], "page": 1}],
                    }
                ]
            }
        )


def test_page_mrr_deduplicates_repeated_chunks_and_errors_remain_visible():
    gold = [{"document_id": "a", "page": 2}]
    found = [{"document_id": "a", "page": 1}] * 3 + gold
    assert reciprocal_rank(gold, found) == 0.5
    assert reciprocal_rank(gold, []) == 0
    rows = [
        {"status": "ok", "page_recall": 1, "reciprocal_rank": 0.5, "seconds": 2},
        {"status": "error", "seconds": 10},
        {"status": "ok", "answerable": False, "empty_retrieval": True, "seconds": 4},
    ]
    report = summarize(rows)
    assert report["errors"] == 1
    assert report["mean_page_recall_successful_cases"] == 1
    assert report["mean_page_recall_errors_as_zero"] == 0.5
    assert report["mrr_errors_as_zero"] == 0.25
    assert report["unanswerable_empty_retrieval_rate"] == 1
    assert report["latency_p50_seconds"] == 3
    assert percentile([2, 4], 95) == pytest.approx(3.9)
    assert percentile([], 95) is None


def test_followup_eval_history_is_validated_before_requests():
    did = str(uuid4())
    case = {
        "id": "F1",
        "question": "它的结果？",
        "document_ids": [did],
        "expected_pages": [{"document_id": did, "page": 1}],
        "history": [{"question": "A方法是什么？", "answer": "已核对上下文"}],
    }
    assert validate_cases({"cases": [case]})
    with pytest.raises(ValueError):
        validate_cases({"cases": [{**case, "history": case["history"] * 5}]})
