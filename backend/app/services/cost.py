"""Token 估算与成本核算（对应 aaa.md §4「单份报告 API 成本 ≤ 0.5 元」）。"""

from __future__ import annotations

import re

from ..config import settings

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def estimate_tokens(text: str) -> int:
    """粗估 token 数：中日韩字符按 1 token/字，其余按 4 字符/token。

    仅在 mock 模式或响应未返回 usage 时使用；live 模式优先用 API 返回的真实 usage。
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = max(0, len(text) - cjk)
    return int(cjk + other / 4) + 1


def compute_cost(tokens_in: int, tokens_out: int) -> float:
    """按配置单价折算人民币，保留 4 位小数以对齐 workflow_steps.cost NUMERIC(10,4)。"""
    cost = (
        tokens_in / 1_000_000 * settings.llm_price_input_per_m
        + tokens_out / 1_000_000 * settings.llm_price_output_per_m
    )
    return round(cost, 4)
