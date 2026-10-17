"""语义判定的服务端校验（V2.0）。

职责边界（与改动说明书 §6 一致）：
- **LLM 只负责生成判断与引文**（真差距 / 表述差异 + 逐字原文片段）；
- **本模块负责决定这份判断能不能进分数**——纯函数、不查库、不调模型、可断言。

为什么需要"证据强度"这一层（实测结论，不是拍脑袋）：
`locate_quote` 只能证明**引文不是编的**，证明不了**引文够不够**。在 32 份排序验证夹具上，
仅做"可定位"校验时，模型用 `Spring Cloud` 证明"具备微服务能力"、用 `Docker` 证明"具备容器化能力"，
**81 条真差距被误判为表述差异**，导致排序相关性由 +0.835 掉到 +0.756（比无语义还差）。
叠加"证据强度"后：误判 81 → 48、相关性回到 **+0.847**、recall 仅由 100% 降到 90.3%。

因此证据强度是**默认开启**的确定性规则，不需要新增可调参数：

| 规则 | 判据 | 挡住什么 |
|---|---|---|
| 出处必须是经历要点 | 引文所在行在 `工作经历` / `项目经历` 区块内，且该行以 `- ` 起头 | 技能栏裸词（`Docker`、`Spring Cloud`） |
| 引文长度下限 | ≥ 10 字 | `MySQL`、`Redis` 这类碎片 |
| 引文不得跨越区块 | 引文与所在行同行 | 多处拼接的伪连续片段 |
"""

from __future__ import annotations

import logging
import re

from ..schemas import ParsedJd
from .evidence import locate_quote

logger = logging.getLogger(__name__)

# 语义校验通过的等价映射：[("分布式", "梳理 12 个服务的调用链路…"), ...]
SemanticCovered = list[tuple[str, str]]

MIN_QUOTE_CHARS = 10
# 经历区块：引文必须落在这些区块内（与 `parse_rules` 的分节用词保持一致口径）
EXPERIENCE_HEADINGS = ("工作经历", "项目经历", "实习经历", "工作经验", "项目经验")
# 表格/并列分隔符：用于粗略判断"引文是否跨单元格拼接"
_SPLIT_RE = re.compile(r"[|｜\t]")


def evidence_strength(resume_text: str, quote: str, *, min_chars: int = MIN_QUOTE_CHARS) -> tuple[bool, str]:
    """判断一条引文是否**足够**作为能力等价的证据。

    返回 `(是否通过, 原因)`；原因用于可观测性（写进 trace 的 notes），失败原因便于排查。
    """
    quote = (quote or "").strip()
    if not quote:
        return False, "empty_quote"
    if len(quote) < min_chars:
        return False, f"too_short({len(quote)}<{min_chars})"
    if _SPLIT_RE.search(quote):
        return False, "looks_like_table_cell"

    located = locate_quote(resume_text or "", quote)
    if located is None:
        return False, "not_located"
    start, _end = located

    line_start = resume_text.rfind("\n", 0, start) + 1
    line_end = resume_text.find("\n", start)
    line_end = len(resume_text) if line_end < 0 else line_end
    line = resume_text[line_start:line_end].strip()

    if not line.startswith("- ") and not line.startswith("* "):
        return False, "not_a_bullet"
    if quote.strip() not in line:
        return False, "spans_multiple_lines"

    section = _section_of(resume_text, line_start)
    if section not in EXPERIENCE_HEADINGS:
        return False, f"outside_experience({section or 'none'})"
    return True, "ok"


def _section_of(resume_text: str, pos: int) -> str:
    """返回 `pos` 之前最近的一个经历区块标题名；找不到返回空串。"""
    head = resume_text[:pos]
    best_name = ""
    best_idx = -1
    for name in EXPERIENCE_HEADINGS:
        idx = head.rfind(name)
        if idx > best_idx:
            best_idx, best_name = idx, name
    return best_name


def verify_semantic_judgments(
    *,
    resume_text: str,
    parsed_jd: ParsedJd,
    missing_keywords: list[str],
    covered_keywords: list[str],
    judgments: list[dict],
    require_strength: bool = True,
) -> tuple[SemanticCovered, dict]:
    """把模型判断过滤成"可进分数"的等价映射。

    校验规则（任一不过即作废，且**不抛异常**——语义判定失败不应让整条任务失败）：
    1. `verdict == "false_gap"`；
    2. 关键词确实在 JD 要求清单里（防止模型自造要求项）；
    3. 关键词确实在**字面缺失**清单里（已被字面覆盖的不重复计）；
    4. 引文能在简历原文中定位（`locate_quote`）；
    5. 引文满足证据强度（出处为经历要点、长度足够）；
    6. 同一关键词只接受一条。

    返回 `(通过列表, 统计)`；统计用于写 trace 与排查。
    """
    required = {str(k).lower() for k in parsed_jd.all_skills()}
    missing_lower = {str(m).lower() for m in missing_keywords}
    covered_lower = {str(c).lower() for c in covered_keywords}

    stats = {
        "judged_false": 0, "passed": 0,
        "rejected_not_required": 0, "rejected_already_covered": 0,
        "rejected_duplicate": 0, "rejected_not_located": 0,
        "rejected_weak_evidence": 0,
        "weak_reasons": {},
    }
    seen: set[str] = set()
    passed: SemanticCovered = []

    for item in judgments or []:
        if not isinstance(item, dict) or str(item.get("verdict")) != "false_gap":
            continue
        stats["judged_false"] += 1
        kw = str(item.get("keyword") or "").strip()
        quote = str(item.get("resume_quote") or "").strip()
        if not kw:
            continue
        key = kw.lower()
        if key not in required:
            stats["rejected_not_required"] += 1
            continue
        if key in covered_lower or key not in missing_lower:
            stats["rejected_already_covered"] += 1
            continue
        if key in seen:
            stats["rejected_duplicate"] += 1
            continue
        if not quote or locate_quote(resume_text or "", quote) is None:
            stats["rejected_not_located"] += 1
            continue
        if require_strength:
            ok, reason = evidence_strength(resume_text or "", quote)
            if not ok:
                stats["rejected_weak_evidence"] += 1
                stats["weak_reasons"][reason] = stats["weak_reasons"].get(reason, 0) + 1
                continue
        seen.add(key)
        passed.append((kw, quote))

    stats["passed"] = len(passed)
    return passed, stats


def semantic_verify_note(stats: dict) -> str:
    """把统计压成一行 notes（落 `workflow_steps`，方便线上排查）。"""
    if not stats or not stats.get("judged_false"):
        return "语义校验：无候选条目"
    weak = stats.get("weak_reasons") or {}
    weak_txt = ("（" + "、".join(f"{k}×{v}" for k, v in weak.items()) + "）") if weak else ""
    return (
        f"语义校验：候选 {stats['judged_false']} 条 → 通过 {stats.get('passed', 0)} 条；"
        f"剔除 不可定位 {stats.get('rejected_not_located', 0)}、"
        f"证据不足 {stats.get('rejected_weak_evidence', 0)}{weak_txt}、"
        f"非要求项 {stats.get('rejected_not_required', 0)}、"
        f"重复 {stats.get('rejected_duplicate', 0)}"
    )


__all__ = [
    "SemanticCovered",
    "MIN_QUOTE_CHARS",
    "evidence_strength",
    "verify_semantic_judgments",
    "semantic_verify_note",
]
