"""文本规范化：修正 PDF 抽取产生的字形伪影，并提供"只用于匹配"的折叠形式。

## 为什么需要这个模块（实测）

某份 PDF 简历被 pypdf 抽出后，`项⽬经历` 里的「目」是 **U+2F6C（KANGXI RADICAL EYE）**
而不是「目」U+76EE；`技能特⻓`、`⽀持`、`独⽴`、`⼯作年限` 同理。
这类字符肉眼完全看不出区别，但会造成两类**静默**故障：

1. **章节标题识别失败** → `项⽬经历` 整段未被归入 projects，规则解析出 `projects=[]`；
2. **证据定位失败** → LLM 输出的引文是正常汉字，`locate_quote` 在原文里逐字查找必然
   失败 → 每条结论都变成 `unverified: true`，报告里"证据来源"形同虚设。

## 两套 API，用途严格分开

- `fix_pdf_artifacts(text)`：**改写文本**。只替换"兼容字符"（康熙部首、CJK 部首补充、
  兼容汉字、异常空格），**绝不动中文标点**（`，` `。` `：` 保持全角）。
  在 `extract_text` 抽取完成时调用一次，让入库的 `raw_text` 从一开始就是干净文本，
  下游的章节切分、span 偏移、去重哈希全部天然一致。
- `fold_for_match(text)`：**只用于匹配**，返回 `(折叠文本, 下标映射)`。它做完整的 NFKC
  + 空白折叠 + ASCII 小写，用于"已经入库的历史文本"（仍是脏的）也能定位引文与关键词。
  映射保证能把折叠后的坐标还原回原文坐标，所以展示 span 不受影响。
"""

from __future__ import annotations

import re
import unicodedata

_WS_RE = re.compile(r"\s+")


def _is_compat_char(ch: str) -> bool:
    """是否是 Unicode「兼容字符」——即 NFKC 会改写的码位。

    只放行我们要修的几类，避免把 `①` `㎏` `ﬁ` 这类整篇替换掉（那会改变用户的排版意图）。
    """
    cp = ord(ch)
    return (
        0x2E80 <= cp <= 0x2EFF      # CJK Radicals Supplement
        or 0x2F00 <= cp <= 0x2FDF   # Kangxi Radicals
        or 0xF900 <= cp <= 0xFAFF   # CJK Compatibility Ideographs
        or 0x2F800 <= cp <= 0x2FA1F  # CJK Compatibility Ideographs Supplement
        or cp in (0x00A0, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005,
                  0x2006, 0x2007, 0x2008, 0x2009, 0x200A, 0x202F, 0x205F, 0x3000)
    )


# 「CJK Radicals Supplement」里 NFKC **不会**处理的简化部首。
#
# 为什么要一张手写表：康熙部首（U+2F00–U+2FDF）都有 NFKC 映射，但
# CJK Radicals Supplement（U+2E80–U+2EFF）**没有** —— 它是"部首"而不是"兼容字符"，
# 官方不保证与某个汉字等价。实测简历里的 `身⻓`（⻓ = U+2ED3）、`双⻆⾊`（⻆ = U+2EC6）
# 就属于这一类：肉眼看着就是"长/角"，但 `locate_quote` 逐字比对必然失败，
# 于是引文全部变成 unverified。
#
# 取舍：**只收有把握的、简历里高频出现的**部首，宁可漏改也不改成别的字。
_RADICAL_FALLBACK: dict[str, str] = {
    "⻓": "长", "⻆": "角", "⻅": "见", "⻋": "车", "⻔": "门", "⻚": "页",
    "⻛": "风", "⻜": "飞", "⻢": "马", "⻥": "鱼", "⻦": "鸟", "⻩": "黄",
    "⻮": "齿", "⻯": "龙", "⻰": "龙", "⻳": "龟", "⻘": "青", "⻙": "韦",
    "⻨": "麦", "⻬": "齐", "⻄": "西", "⺟": "母", "⺠": "民", "⺮": "竹",
    "⺼": "月", "⻊": "足", "⻐": "钅", "⻞": "饣", "⻁": "虎", "⺂": "乙",
    "⻅": "见", "⻆": "角", "⻤": "鬼", "⻗": "雨",
}


def fix_pdf_artifacts(text: str) -> str:
    """把 PDF 抽取产生的兼容字符换成正常汉字/空格。

    - 康熙部首与兼容汉字 → 统一汉字（`⽬`→`目`），走 NFKC；
    - CJK Radicals Supplement 的简化部首 → 对应汉字（`⻓`→`长`），走手写表；
    - 各类特殊空格（EM SPACE / IDEOGRAPHIC SPACE）→ 普通空格；
    - **中文标点保持原样**（不做全角→半角），避免改写用户原文观感。

    一字符换一字符，因此不会改变字符数量（除非原字符不在表里），
    对已经算好偏移的下游逻辑无副作用。
    """
    if not text:
        return text

    out: list[str] = []
    changed = False
    for ch in text:
        replaced = _RADICAL_FALLBACK.get(ch)
        if replaced is None and _is_compat_char(ch):
            fixed = unicodedata.normalize("NFKC", ch)
            # NFKC 极端情况下可能给出空串或多字符：只在 1:1 时采用，否则保留原字符
            replaced = fixed if len(fixed) == 1 else None
        if replaced is not None and replaced != ch:
            out.append(replaced)
            changed = True
        else:
            out.append(ch)
    return "".join(out) if changed else text


def fold_for_match(
    text: str, *, lower: bool = True, drop_space: bool = False
) -> tuple[str, list[int]]:
    """把文本折叠成"只用于匹配"的形式，并返回 fold 后每个字符对应的原文下标。

    折叠规则：字形修正 → NFKC → 空白处理 →（可选）ASCII 小写。
    多字符展开（如 `ﬁ`→`fi`）也支持，多个折叠字符共用同一个原文下标。

    ⚠️ `lower` 的取舍：**关键词匹配要小写**（`Java` 与 `java` 应算同一个词）；
    但**引文定位不能小写** —— `Go` 会被折叠成 `go`，从而在 `google` 里误命中，
    把一个"定位不到"的证据伪装成"已核实"。所以 `services/evidence.py` 传 `lower=False`。

    ⚠️ `drop_space` 的取舍：PDF 抽取会在**中文之间插空格**（`买 家 / 卖 家`、
    `技术栈： SpringBoot 、 Vue`）。这些空格肉眼看不见，却会让逐字比对失败。
    所以定位的最后一层用 `drop_space=True`：去掉全部空格再比一次。

    为什么空白要折叠而不是删除（`drop_space=False` 时）：`locate_quote` 的
    "最长可定位前缀"回退依赖可预测的字符数；折叠成单空格与既有语义一致。
    """
    out: list[str] = []
    index_map: list[int] = []
    prev_space = False

    for i, ch in enumerate(text or ""):
        folded = unicodedata.normalize("NFKC", _RADICAL_FALLBACK.get(ch, ch))
        if not folded:
            continue
        for piece in folded:
            if piece.isspace():
                if drop_space:
                    continue
                if prev_space or not out:
                    prev_space = True
                    continue
                out.append(" ")
                index_map.append(i)
                prev_space = True
                continue
            if lower and "A" <= piece <= "Z":
                piece = piece.lower()
            out.append(piece)
            index_map.append(i)
            prev_space = False

    if out and out[-1] == " ":
        out.pop()
        index_map.pop()
    return "".join(out), index_map


def fold_text(text: str, *, lower: bool = True, drop_space: bool = False) -> str:
    """只要折叠结果（不需要下标映射时的便捷入口）。"""
    return fold_for_match(text, lower=lower, drop_space=drop_space)[0]


__all__ = ["fix_pdf_artifacts", "fold_for_match", "fold_text"]
