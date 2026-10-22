"""证据链：把模型给出的引文定位回简历/JD 原文，产出精确字符区间。

这是本产品的核心差异点（aaa.md §六.1 原则 1「证据优先」）。
设计取舍：不依赖模型输出 offset（模型数不准），而是让模型只输出 quote，
由后端在这一层做字符串定位 —— 定位失败就是「无证据」，交由质检 Agent 处理。

## 四级定位（从严格到宽松，全部返回**原文真实坐标**）

1. **精确匹配** —— `raw_text.find(quote)`，绝大多数情况走这里。
2. **空白折叠匹配** —— 模型把换行写成了空格；用折叠文本 + 下标映射还原坐标。
3. **字形折叠匹配** —— 参数文本里混进了康熙部首/兼容汉字（`⽬` 与 `目` 肉眼同形，
   见 `services/textfold.py`）。折叠后再比一次，坐标仍映射回原文。
4. **最长可定位前缀** —— 模型截断或润色了引文尾部时退化为前缀匹配。

第 3 级是**必须**的：PDF 抽出的简历常常整篇带着康熙部首，而模型给的是正常汉字，
没有这一级就会出现"每条结论都标 unverified"的静默故障（实测已发生）。
"""

from __future__ import annotations

import re

from .textfold import fold_for_match

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


def _map_span(index_map: list[int], start: int, end_exclusive: int) -> list[int]:
    """把折叠坐标 [start, end) 映射回原文坐标 [start, end)。"""
    last = min(end_exclusive - 1, len(index_map) - 1)
    return [index_map[start], index_map[last] + 1]


def _longest_locate(
    hay: str, needle: str, index_map: list[int], *, min_prefix: int, exact_only: bool = False
) -> list[int] | None:
    """在折叠文本里找 needle（整体找不到时退化为最长可定位前缀）。"""
    if not needle:
        return None
    idx = hay.find(needle)
    if idx >= 0:
        return _map_span(index_map, idx, idx + len(needle))
    if exact_only:
        # 只接受**完整**命中：折叠路径下前缀回退算出来的区间往往不是引文本身
        # （折掉的字符与空白不在 index_map 里，按原长算 end 会切到别处）。
        return None

    for cut in range(len(needle), min_prefix - 1, -1):
        idx = hay.find(needle[:cut])
        if idx >= 0:
            return _map_span(index_map, idx, idx + cut)
    return None


def _span_matches(text: str, span: list[int] | None, quote: str) -> bool:
    """兜底校验：`text[span[0]:span[1]]` 是否就是引文本身。

    契约 §5.1 只要求「quote 能在原文定位」，但 `span` 被客户端用来高亮，
    一旦切出来不是引文就是**错误的定位信息**。实测踩到：
    `raw_text[285:292] == "Spring "` 而 `quote == "Spring Cloud"`（第 3 级折叠路径
    按引文原长算 end，折掉的字符不在映射里）。
    """
    if not span or len(span) != 2:
        return False
    start, end = int(span[0]), int(span[1])
    if start < 0 or end > len(text) or end <= start:
        return False
    return text[start:end].strip() == quote.strip()


def locate_quote(raw_text: str, quote: str, *, min_prefix: int = 8) -> list[int] | None:
    """在 raw_text 中定位 quote，返回 [start, end) 字符区间；定位不到返回 None。

    **不变式**：返回的区间一定满足 `raw_text[start:end].strip() == quote.strip()`。
    做不到就返回 None —— 宁缺勿错：错位的 span 会让前端的「已定位」高亮指到无关文字上。
    """
    if not raw_text or not quote:
        return None

    quote = quote.strip()
    if not quote:
        return None

    # 1) 精确匹配（最快，也最准）
    idx = raw_text.find(quote)
    if idx >= 0:
        return [idx, idx + len(quote)]

    # 2) 折叠空白后匹配（应对模型把换行写成了空格）
    norm_text, index_map = _normalized_index_map(raw_text)
    span = _longest_locate(norm_text, normalize(quote), index_map, min_prefix=min_prefix)
    if span is not None:
        return span

    # 3) 字形折叠后匹配（应对参数文本里的康熙部首/兼容汉字）
    # ⚠️ lower=False：引文定位**不能**大小写折叠，否则 `Go` 会被折成 `go` 而在
    #    `google` 里误命中，把"定位不到"伪装成"已核实"。关键词匹配另走 analyze 的小写路径。
    # ⚠️ exact_only=True：这一级只认完整命中，见 `_longest_locate` 的说明。
    fold_raw, fold_map = fold_for_match(raw_text, lower=False)
    fold_quote, _ = fold_for_match(quote, lower=False)
    span = _longest_locate(fold_raw, fold_quote, fold_map, min_prefix=max(4, min_prefix - 2), exact_only=True)
    if span is not None and _span_matches(raw_text, span, quote):
        return span

    # 4) 去掉全部空格再比一次 —— PDF 抽取会在中文之间插空格
    #    （`买 家 / 卖 家`、`技术栈： SpringBoot 、 Vue`），肉眼看不见但逐字比对会失败。
    #    放在最后一级：它最宽松，误命中风险最高，只有前面都失败时才启用。
    tight_raw, tight_map = fold_for_match(raw_text, lower=False, drop_space=True)
    tight_quote, _ = fold_for_match(quote, lower=False, drop_space=True)
    span = _longest_locate(tight_raw, tight_quote, tight_map, min_prefix=max(4, min_prefix - 2), exact_only=True)
    if span is not None and _span_matches(raw_text, span, quote):
        return span

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
