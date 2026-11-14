"""Conservative layout extraction and page-local, token-bounded retrieval chunks.

This is a text-PDF heuristic, not an OCR or general table reconstruction engine.
Physical PDF page numbers remain the citation boundary even for two-column text.
"""

import math
import re
from collections import Counter

import pymupdf

PARSER_VERSION = "layout-v2"


def margin_key(text):
    return re.sub(r"\d+", "#", " ".join(text.casefold().split()))


def reading_order(blocks, width):
    """Read left then right within bands separated by full-width blocks.

    Only infer two columns when both sides have independent text blocks. Wide blocks
    are kept in their vertical positions, so titles do not move below both columns.
    """
    middle, tolerance = width / 2, width * 0.025
    left = [b for b in blocks if b[2] <= middle + tolerance]
    right = [b for b in blocks if b[0] >= middle - tolerance]
    if not left or not right:
        return sorted(blocks, key=lambda b: (b[1], b[0]))
    spanning = sorted([b for b in blocks if b not in left and b not in right], key=lambda b: b[1])
    remaining = [b for b in blocks if b not in spanning]
    result = []
    for separator in [*spanning, None]:
        boundary = separator[1] if separator else float("inf")
        band = [b for b in remaining if b[1] < boundary]
        result.extend(sorted(band, key=lambda b: (b[0] >= middle - tolerance, b[1], b[0])))
        remaining = [b for b in remaining if b not in band]
        if separator:
            result.append(separator)
    return result


def split_long_text(text, max_chars, max_tokens, count_tokens, overlap=0):
    """Prefer sentence/line boundaries and never exceed the measured token budget."""
    start = 0
    while start < len(text):
        upper = min(len(text), start + max_chars)
        low, high = start + 1, upper
        while low <= high:
            middle = (low + high) // 2
            if count_tokens(text[start:middle].strip()) <= max_tokens:
                low = middle + 1
            else:
                high = middle - 1
        end = high
        if end <= start:
            raise ValueError("单个字符超过分块 token 上限，请检查 tokenizer 和分块配置")
        if end < len(text):
            boundaries = list(re.finditer(r"[。！？.!?]\s*|\n+|\s+", text[start:end]))
            suitable = [m.end() for m in boundaries if m.end() >= (end - start) * 0.55]
            if suitable:
                end = start + suitable[-1]
        # Recheck after boundary selection; BPE counts need not be monotonic.
        while end > start and count_tokens(text[start:end].strip()) > max_tokens:
            end -= 1
        if end <= start:
            raise ValueError("无法在 token 预算内切分文段，请检查分块配置")
        piece = text[start:end].strip()
        if piece:
            yield piece
        if end >= len(text):
            break
        # Very small token budgets must still make forward progress.
        start = end - min(overlap, max(0, (end - start) // 4))


def heading(text):
    flat = " ".join(text.split())
    if len(flat) > 100:
        return None
    if re.fullmatch(
        r"(?:\d+(?:\.\d+)*\.?\s+)?(?:abstract|introduction|background|related work|"
        r"methods?|methodology|materials and methods|experiments?|results?(?: and discussion)?|"
        r"discussion|conclusions?|limitations|references|acknowledg\w*|"
        r"摘要|引言|背景|相关工作|研究方法|实验(?:结果)?|结果与讨论|讨论|结论|局限性|参考文献)",
        flat,
        re.IGNORECASE,
    ) or re.fullmatch(r"\d+(?:\.\d+)*\.?\s+[^.!?。！？]{2,70}", flat):
        return flat
    return None


def parse_pdf(data, settings, count_tokens=None):
    if not 0 <= settings.chunk_overlap < settings.chunk_size:
        raise ValueError("分块重叠必须小于分块大小")
    # Model doubles can omit a tokenizer; production passes both real tokenizers.
    measure = count_tokens or len
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError("无法解析 PDF，请检查文件是否损坏") from exc
    with doc:
        if doc.needs_pass:
            raise ValueError("暂不支持加密 PDF，请先解密")
        if len(doc) > settings.max_pages:
            raise ValueError(f"PDF 不能超过 {settings.max_pages} 页")
        pages, counts = [], Counter()
        extracted_chars = 0
        for page in doc:
            blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
            extracted_chars += sum(len(b[4]) for b in blocks)
            if extracted_chars > settings.max_chunks * settings.chunk_size * 2:
                raise ValueError("文档解压后文本过多，请拆分 PDF 后上传")
            margins = {
                margin_key(b[4])
                for b in blocks
                if len(b[4].strip()) <= 180
                and (b[3] < page.rect.height * 0.06 or b[1] > page.rect.height * 0.94)
            }
            counts.update(margins)
            pages.append((page.rect.width, page.rect.height, blocks))
        repeated = {key for key, n in counts.items() if n >= max(3, math.ceil(len(doc) * 0.6))}
        chunks, empty_pages, section = [], [], ""
        for number, (width, height, blocks) in enumerate(pages, 1):
            if not blocks:
                empty_pages.append(number)
                continue
            kept = []
            for block in blocks:
                text = block[4].strip()
                is_margin = block[3] < height * 0.06 or block[1] > height * 0.94
                page_counter = bool(
                    re.fullmatch(r"(?:page\s*)?\d+(?:\s*(?:/|of)\s*\d+)?", text, re.I)
                )
                if is_margin and (margin_key(text) in repeated or page_counter):
                    continue
                kept.append(block)
            pending, pending_section = "", section

            def append(text, current_section):
                chunks.append(
                    {
                        "page": number,
                        "text": text,
                        "ordinal": len(chunks),
                        "section": current_section,
                    }
                )
                if len(chunks) > settings.max_chunks:
                    raise ValueError("文档文本过多，请拆分 PDF 后上传")

            for block in reading_order(kept, width):
                text = block[4].strip()
                title = heading(text)
                if title:
                    if pending:
                        append(pending, pending_section)
                        pending = ""
                    section = title
                for piece in split_long_text(
                    text,
                    settings.chunk_size,
                    settings.chunk_tokens,
                    measure,
                    settings.chunk_overlap,
                ):
                    joined = pending + "\n\n" + piece if pending else piece
                    if pending and (
                        len(joined) > settings.chunk_size or measure(joined) > settings.chunk_tokens
                    ):
                        append(pending, pending_section)
                        pending = ""
                    if not pending:
                        pending_section = section
                    pending = pending + "\n\n" + piece if pending else piece
            if pending:
                append(pending, pending_section)
        if not chunks:
            raise ValueError("没有可提取文本；扫描版 PDF 请先进行 OCR")
        return chunks, len(doc), empty_pages
