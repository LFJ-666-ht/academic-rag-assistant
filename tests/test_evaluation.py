"""Page-recall semantics and annotation validation, not model-quality assertions."""

from uuid import uuid4

import pytest

from scripts.evaluate_rag import page_recall, validate_cases


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
