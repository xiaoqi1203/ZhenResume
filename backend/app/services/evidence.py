"""证据链：把模型给出的引文定位回简历/JD 原文，产出精确字符区间。

这是本产品的核心差异点（aaa.md §六.1 原则 1「证据优先」）。
设计取舍：不依赖模型输出 offset（模型数不准），而是让模型只输出 quote，
由后端在这一层做字符串定位 —— 定位失败就是「无证据」，交由质检 Agent 处理。
"""

from __future__ import annotations

import re

_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """折叠空白，便于跨换行的宽松匹配。"""
    return _WS_RE.sub(" ", text or "").strip()


def _normalized_index_map(text: str) -> tuple[str, list[int]]:
    """返回 (折叠后的文本, 折叠后每个字符对应的原文下标)。"""
    out_chars: list[str] = []
    index_map: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space or not out_chars:
                prev_space = True
                continue
            out_chars.append(" ")
            index_map.append(i)
            prev_space = True
        else:
            out_chars.append(ch)
            index_map.append(i)
            prev_space = False
    if out_chars and out_chars[-1] == " ":
        out_chars.pop()
        index_map.pop()
    return "".join(out_chars), index_map


def locate_quote(raw_text: str, quote: str, *, min_prefix: int = 8) -> list[int] | None:
    """在 raw_text 中定位 quote，返回 [start, end) 字符区间；定位不到返回 None。"""
    if not raw_text or not quote:
        return None

    quote = quote.strip()
    if not quote:
        return None

    # 1) 精确匹配
    idx = raw_text.find(quote)
    if idx >= 0:
        return [idx, idx + len(quote)]

    # 2) 折叠空白后匹配（应对模型把换行写成了空格）
    norm_text, index_map = _normalized_index_map(raw_text)
    norm_quote = normalize(quote)
    if norm_quote:
        nidx = norm_text.find(norm_quote)
        if nidx >= 0:
            start = index_map[nidx]
            end = index_map[min(nidx + len(norm_quote) - 1, len(index_map) - 1)] + 1
            return [start, end]

        # 3) 退化到最长可定位前缀（模型可能截断或润色了引文尾部）
        for cut in range(len(norm_quote), min_prefix - 1, -1):
            nidx = norm_text.find(norm_quote[:cut])
            if nidx >= 0:
                start = index_map[nidx]
                end = index_map[min(nidx + cut - 1, len(index_map) - 1)] + 1
                return [start, end]

    return None


def snippet(raw_text: str, span: list[int] | None, *, pad: int = 30) -> str:
    """按区间取原文片段，用于报告里的上下文预览。"""
    if not raw_text or not span:
        return ""
    start = max(0, span[0] - pad)
    end = min(len(raw_text), span[1] + pad)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(raw_text) else ""
    return f"{prefix}{raw_text[start:end]}{suffix}"


def build_evidence(
    *,
    source: str,
    quote: str,
    field: str | None,
    resume_text: str = "",
    jd_text: str = "",
) -> dict:
    """构造契约 §5.1 的 evidence 结构。

    span 只在 quote 能被定位时给出；定位不到时 span 为 None —— 质检 Agent 会把
    这类「无原文依据」的红色风险降级或剔除。
    """
    span: list[int] | None = None
    if source == "resume":
        span = locate_quote(resume_text, quote)
    elif source == "jd":
        span = locate_quote(jd_text, quote)

    evidence: dict = {"source": source, "quote": quote, "span": span, "field": field}
    if span is None and source in ("resume", "jd"):
        evidence["unverified"] = True
    return evidence
