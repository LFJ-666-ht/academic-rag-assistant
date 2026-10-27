"""Deterministic model doubles and small PDF files shared by integration tests."""

import hashlib

import pymupdf


class FakeModels:
    def encode(self, texts, query=False):
        return [[float(v + 1) for v in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]

    def score(self, question, texts):
        return [float(question.lower() in t.lower()) for t in texts]


def pdf(text="Solar blind UV detector has low dark current."):
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50, 50), text)
        doc.new_page().insert_text((50, 50), "Responsivity is measured at 254 nm.")
        return doc.tobytes()
