"""Conditional follow-up resolution; topic changes do not inherit old questions."""

import os
import re


def needs_context(question):
    if re.search(
        r"换个(?:问题|话题)|另(?:一|外一)个(?:问题|话题)|new topic|another question", question, re.I
    ):
        return False
    return bool(
        re.search(
            r"它|它们|该(?:方法|模型|研究|论文)|上述|前述|刚才|继续|这(?:个|些|篇)|"
            r"其(?:方法|结果|局限|优势)|\b(?:it|its|they|their|this method|that paper|continue)\b",
            question,
            re.I,
        )
    )


def retrieval_query(question, history=None, generator=None, allow_model=False):
    history = (history or [])[-4:]
    if not history or not needs_context(question):
        return question
    latest = history[-1]
    # A bounded local fallback costs no extra provider call. Current question is
    # first so long inputs cannot truncate away the user's new constraints.
    fallback = question + "\n相关问题：" + latest["question"][:400]
    enabled = os.getenv("RAG_QUERY_REWRITE", "off").strip().lower() == "on"
    method = getattr(generator, "rewrite_query", None)
    if not (allow_model and enabled and callable(method)):
        return fallback
    try:
        rewritten = method(
            question,
            {
                "question": latest["question"][:500],
                "answer": latest.get("answer", "")[:1000],
            },
        ).strip()
        required = re.findall(r"[“\"]([^”\"]+)[”\"]", question)
        numbers = re.findall(r"\d+(?:\.\d+)?", question)
        if not 1 <= len(rewritten) <= 1000 or any(
            term not in rewritten for term in [*numbers, *[r for r in required if r]]
        ):
            return fallback
        return rewritten
    except Exception:
        # No paid retry. A failed optional rewrite must not erase the question or
        # block normal retrieval; provider details are not exposed to the client.
        return fallback
