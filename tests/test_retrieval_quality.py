"""Retrieval invariants with real PDF/SQLite/Qdrant and deterministic model doubles."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pymupdf
import pytest
from fastapi.testclient import TestClient
from helpers import FakeModels, pdf

from backend import lexical
from backend.core import KnowledgeBase, Settings
from backend.generation import answer_question
from backend.local_models import LocalModels, query_instruction
from backend.main import create_app
from backend.pdf_text import parse_pdf, reading_order, split_long_text
from backend.retrieval_query import retrieval_query


def test_token_budget_and_long_tail_are_preserved(tmp_path):
    text = "First sentence. " * 20 + "DISTINCTIVE_TAIL"
    pieces = list(split_long_text(text, 100, 40, len, 8))
    assert all(len(piece) <= 40 for piece in pieces)
    assert pieces[-1].endswith("DISTINCTIVE_TAIL")
    settings = Settings(tmp_path, chunk_size=90, chunk_overlap=15, chunk_tokens=40)
    chunks, pages, _ = parse_pdf(pdf(), settings, len)
    assert pages == 2 and {c["page"] for c in chunks} == {1, 2}
    assert all(len(c["text"]) <= 40 for c in chunks)
    assert [c["ordinal"] for c in chunks] == list(range(len(chunks)))


def test_column_order_keeps_full_width_heading_between_bands():
    title = (40, 10, 560, 25, "Title", 0, 0)
    left = (40, 40, 280, 80, "left 1", 1, 0)
    right = (320, 40, 560, 70, "right 1", 2, 0)
    left2 = (40, 90, 280, 120, "left 2", 3, 0)
    wide = (40, 150, 560, 175, "Results", 4, 0)
    lower = (320, 200, 560, 220, "lower right", 5, 0)
    ordered = reading_order([right, wide, lower, left2, title, left], 600)
    assert [b[4] for b in ordered] == [
        "Title",
        "left 1",
        "left 2",
        "right 1",
        "Results",
        "lower right",
    ]


def test_repeated_margins_removed_without_losing_repeated_body(tmp_path):
    with pymupdf.open() as doc:
        for n in range(3):
            page = doc.new_page()
            page.insert_text((50, 35), "Journal of Testing 2026")
            page.insert_text((50, 120), "This repeated body sentence is important.")
            page.insert_text((50, 200), f"Unique experiment {n}.")
            page.insert_text((50, 820), str(n + 1))
        chunks, pages, empty = parse_pdf(doc.tobytes(), Settings(tmp_path))
    assert pages == 3 and not empty
    assert all("Journal of Testing" not in c["text"] for c in chunks)
    assert sum("repeated body" in c["text"] for c in chunks) == 3
    assert {c["page"] for c in chunks} == {1, 2, 3}


def test_model_query_prefix_is_not_forced_on_multilingual_model():
    assert query_instruction("BAAI/bge-small-zh-v1.5").startswith("为这个")
    assert query_instruction("BAAI/bge-m3") == ""
    assert query_instruction("D:/models/bge-m3") == ""
    assert query_instruction("BAAI/bge-small-zh-v1.5", "") == ""
    assert query_instruction("custom", "Find: ") == "Find: "


def test_legacy_long_document_tail_is_reranked_without_pair_overflow(tmp_path):
    class Tokenizer:
        def encode(self, text, **kwargs):
            return list(text)

        def decode(self, ids, **kwargs):
            return "".join(ids)

        def num_special_tokens_to_add(self, **kwargs):
            return 4

    class Scores(list):
        def reshape(self, *args):
            return self

        def tolist(self):
            return list(self)

    class Ranker:
        tokenizer = Tokenizer()
        pairs = []

        def predict(self, pairs, **kwargs):
            self.pairs = pairs
            return Scores(float("TAIL" in text) for _, text in pairs)

    settings = Settings(tmp_path, reranker_max_tokens=80, query_max_tokens=20)
    model = LocalModels(settings)
    model.reranker = Ranker()
    assert model.score("q" * 100, ["a" * 200 + "TAIL"]) == [1.0]
    assert all(len(q) + len(t) + 4 <= 80 for q, t in model.reranker.pairs)


def test_keyword_index_is_persistent_scoped_and_deletes_cleanly(kb, monkeypatch):
    alice = kb.ingest("alice", "a.pdf", pdf("A unique ALPHA123 detector."))["document_id"]
    bob = kb.ingest("bob", "b.pdf", pdf("ALPHA123 ALPHA123 ALPHA123 secret."))["document_id"]
    other = kb.ingest("alice", "c.pdf", pdf("A different paper."))["document_id"]
    assert {r["document_id"] for r in lexical.search(kb.db, "alice", "ALPHA123", [alice], 5)} == {
        alice
    }
    assert lexical.search(kb.db, "alice", "ALPHA123", [bob], 5) == []
    assert lexical.search(kb.db, "alice", "ALPHA123", [other], 5) == []
    queries = []
    original = lexical.tokens
    monkeypatch.setattr(lexical, "tokens", lambda text: queries.append(text) or original(text))
    kb.retrieve("alice", "ALPHA123", document_ids=[alice])
    assert queries == ["ALPHA123"]  # document text is not re-segmented on every query
    kb.delete("alice", alice)
    assert (
        kb.db.execute("SELECT count(*) FROM chunk_terms WHERE document_id=?", (alice,)).fetchone()[
            0
        ]
        == 0
    )
    assert lexical.search(kb.db, "bob", "ALPHA123", [bob], 5)


def test_batch_queries_are_encoded_once_and_reused_between_papers(kb):
    class Counting(FakeModels):
        query_batches = []

        def encode(self, texts, query=False):
            if query:
                self.query_batches.append(list(texts))
            return super().encode(texts, query)

    kb.models = Counting()
    ids = [kb.ingest("alice", str(n), pdf(f"Paper method {n}"))["document_id"] for n in range(2)]
    questions = ["method", "data", "limitations"]
    for did in ids:
        results = kb.retrieve_many("alice", questions, 3, [did])
        assert len(results) == 3 and all(results)
    assert kb.models.query_batches == [questions]


def test_upload_encoding_does_not_block_reading_progress(kb):
    did = kb.ingest("alice", "old.pdf", pdf())["document_id"]
    started, release = threading.Event(), threading.Event()

    class Slow(FakeModels):
        def encode(self, texts, query=False):
            if not query:
                started.set()
                assert release.wait(5)
            return super().encode(texts, query)

    kb.models = Slow()
    with ThreadPoolExecutor(max_workers=2) as pool:
        upload = pool.submit(kb.ingest, "alice", "new.pdf", pdf("New experiment"))
        try:
            assert started.wait(2)
            read = pool.submit(kb.update_reading, "alice", did, page=2)
            assert read.result(timeout=2)["last_page"] == 2
            assert len(kb.documents("alice")) == 1
        finally:
            release.set()
        assert upload.result(timeout=3)["document_id"] != did


def test_delete_during_reranking_cannot_return_deleted_text(kb):
    did = kb.ingest("alice", "old.pdf", pdf())["document_id"]
    started, release = threading.Event(), threading.Event()

    class Slow(FakeModels):
        def score(self, question, texts):
            started.set()
            assert release.wait(5)
            return super().score(question, texts)

    kb.models = Slow()
    with ThreadPoolExecutor(max_workers=2) as pool:
        search = pool.submit(kb.retrieve, "alice", "detector")
        try:
            assert started.wait(2)
            pool.submit(kb.delete, "alice", did).result(timeout=2)
        finally:
            release.set()
        assert search.result(timeout=2) == []


def test_context_stays_on_same_physical_page_and_preserves_other_papers(tmp_path):
    settings = Settings(tmp_path, chunk_size=50, chunk_overlap=8, chunk_tokens=40)
    kb = KnowledgeBase(settings, FakeModels())
    try:
        a = kb.ingest(
            "alice",
            "a.pdf",
            pdf("The detector detects light with low dark current and high response."),
        )["document_id"]
        b = kb.ingest(
            "alice", "b.pdf", pdf("The detector detects light. Another experimental sample.")
        )["document_id"]
        results = kb.retrieve("alice", "detector", 30)
        assert {r["document_id"] for r in results} == {a, b}
        assert any(len(r["context_chunk_ids"]) > 1 for r in results)
        for item in results:
            for cid in item["context_chunk_ids"]:
                row = kb.db.execute(
                    "SELECT page,document_id FROM chunks WHERE chunk_id=?", (cid,)
                ).fetchone()
                assert (row[0], row[1]) == (item["page"], item["document_id"])
    finally:
        kb.close()


def test_optional_threshold_abstains_without_calling_provider(kb):
    kb.ingest("alice", "a.pdf", pdf())
    kb.settings.rerank_min_score = 0.5  # Only a deliberately calibrated fake-model threshold.

    def never(*args):
        raise AssertionError("no evidence must not call the model")

    answer = answer_question(kb, never, "alice", "nonexistent topic")
    assert answer["citation_status"] == "no_evidence"
    assert "不代表论文中一定没有答案" in answer["answer"]
    assert kb.retrieve("alice", "detector")


def test_old_index_upgrade_keeps_chunk_ids_and_reading(tmp_path):
    kb = KnowledgeBase(Settings(tmp_path), FakeModels())
    did = kb.ingest("alice", "a.pdf", pdf())["document_id"]
    kb.update_reading("alice", did, page=2)
    ids = [r[0] for r in kb.db.execute("SELECT chunk_id FROM chunks ORDER BY rowid")]
    kb.close()
    with sqlite3.connect(tmp_path / "metadata.sqlite3") as db:
        db.executescript(
            "DROP TRIGGER chunk_terms_delete; DROP TABLE chunk_terms; DROP TABLE chunk_details; DELETE FROM config WHERE key='lexical_version';"
        )
    reopened = KnowledgeBase(Settings(tmp_path), FakeModels())
    try:
        assert [
            r[0] for r in reopened.db.execute("SELECT chunk_id FROM chunks ORDER BY rowid")
        ] == ids
        assert reopened.document("alice", did)["reading"]["last_page"] == 2
        assert lexical.search(reopened.db, "alice", "detector", [did], 5)
    finally:
        reopened.close()


def test_followups_rewrite_conditionally_and_fall_back(monkeypatch):
    history = [{"question": "Paper A 的方法是什么？", "answer": "原方法"}]
    assert retrieval_query("BERT 的训练数据是什么？", history) == "BERT 的训练数据是什么？"
    assert "Paper A" in retrieval_query("它有什么局限？", history)
    assert retrieval_query("换个话题：这个模型的费用？", history) == "换个话题：这个模型的费用？"

    class Rewriter:
        calls = 0

        def rewrite_query(self, question, previous):
            self.calls += 1
            return "Paper A 在 2020 年的结果？"

    generator = Rewriter()
    monkeypatch.setenv("RAG_QUERY_REWRITE", "on")
    assert (
        retrieval_query("它在 2020 年的结果？", history, generator, True)
        == "Paper A 在 2020 年的结果？"
    )
    assert "2021" in retrieval_query("它在 2021 年的结果？", history, generator, True)
    retrieval_query("它在 2020 年的结果？", history, generator, False)
    assert generator.calls == 2  # search-only path never starts a paid rewrite


def test_search_configuration_is_authenticated_and_history_is_bounded(kb):
    app = create_app(kb, api_tokens={"a" * 20: "alice"})
    with TestClient(app) as client:
        headers = {"X-API-Key": "a" * 20}
        assert client.get("/api/retrieval/config").status_code == 401
        assert (
            client.get("/api/retrieval/config", headers=headers).json()["rerank_min_score"] is None
        )
        assert (
            client.post(
                "/api/search",
                headers=headers,
                json={"question": "它呢？", "history": [{"question": "q"}] * 5},
            ).status_code
            == 422
        )


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_invalid_threshold_cannot_silently_disable_filter(monkeypatch, value):
    monkeypatch.setenv("RERANK_MIN_SCORE", value)
    with pytest.raises(ValueError):
        Settings.from_env()
