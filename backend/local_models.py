"""Lazy local inference; model work never needs the knowledge-base storage lock."""

import os
import threading

from backend.pdf_text import split_long_text


def query_instruction(model, configured="auto"):
    if configured != "auto":
        return configured
    name = model.replace("\\", "/").lower()
    if "bge-m3" in name:
        return ""
    if "bge" in name and "-zh" in name:
        return "为这个句子生成表示以用于检索相关文章："
    if "bge" in name and "-en" in name:
        return "Represent this sentence for searching relevant passages: "
    return ""


class LocalModels:
    def __init__(self, settings):
        self.settings = settings
        self.embedder = self.reranker = self.rank_tokenizer = None
        # Serialize local inference to bound CPU/GPU peaks without blocking DB reads.
        self.lock = threading.RLock()

    def _embedding(self):
        if self.embedder is None:
            from sentence_transformers import SentenceTransformer

            self.embedder = SentenceTransformer(self.settings.embedding_model)
        return self.embedder

    def _rank_tokenizer(self):
        if self.rank_tokenizer is None:
            from transformers import AutoTokenizer

            self.rank_tokenizer = AutoTokenizer.from_pretrained(
                self.settings.reranker_model,
                cache_dir=os.getenv("SENTENCE_TRANSFORMERS_HOME") or None,
            )
        return self.rank_tokenizer

    def count_tokens(self, text):
        with self.lock:
            embedding = self._embedding()
            encoder = embedding.tokenizer
            ranker = self._rank_tokenizer()
            # Scaling the count also honors an encoder with a smaller context window.
            encoder_budget = max(1, embedding.max_seq_length - encoder.num_special_tokens_to_add())
            rank_budget = max(
                1,
                self.settings.reranker_max_tokens
                - self.settings.query_max_tokens
                - ranker.num_special_tokens_to_add(pair=True),
            )
            return max(
                len(encoder.encode(text, add_special_tokens=False))
                * self.settings.chunk_tokens
                / min(self.settings.chunk_tokens, encoder_budget),
                len(ranker.encode(text, add_special_tokens=False))
                * self.settings.chunk_tokens
                / min(self.settings.chunk_tokens, rank_budget),
            )

    def encode(self, texts, query=False):
        with self.lock:
            model = self._embedding()
            if query:
                prefix = query_instruction(
                    self.settings.embedding_model, self.settings.query_instruction
                )
                texts = [prefix + t for t in texts]
            return model.encode(
                texts,
                batch_size=self.settings.embedding_batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).tolist()

    def score(self, question, texts):
        with self.lock:
            if self.reranker is None:
                from sentence_transformers import CrossEncoder

                self.reranker = CrossEncoder(
                    self.settings.reranker_model, max_length=self.settings.reranker_max_tokens
                )
            tokenizer = self.reranker.tokenizer
            query_ids = tokenizer.encode(question, add_special_tokens=False)[
                : self.settings.query_max_tokens
            ]
            bounded_query = tokenizer.decode(query_ids, skip_special_tokens=True)
            budget = (
                self.settings.reranker_max_tokens
                - len(tokenizer.encode(bounded_query, add_special_tokens=False))
                - tokenizer.num_special_tokens_to_add(pair=True)
            )
            pairs, spans = [], []
            for text in texts:
                start = len(pairs)
                # Legacy chunks can be larger than the new token budget. Score all
                # bounded windows, rather than silently losing the paragraph tail.
                windows = split_long_text(
                    text,
                    max(1, len(text)),
                    budget,
                    lambda s: len(tokenizer.encode(s, add_special_tokens=False)),
                )
                pairs.extend((bounded_query, window) for window in windows)
                spans.append((start, len(pairs)))
            if not pairs:
                return [0.0] * len(texts)
            scores = (
                self.reranker.predict(pairs, batch_size=16, show_progress_bar=False)
                .reshape(-1)
                .tolist()
            )
            return [max(scores[a:b]) if b > a else 0.0 for a, b in spans]
