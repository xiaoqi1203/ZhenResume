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


def compute_cost(
    tokens_in: int,
    tokens_out: int,
    *,
    price_in: float | None = None,
    price_out: float | None = None,
) -> float:
    """按单价折算人民币，保留 4 位小数以对齐 workflow_steps.cost NUMERIC(10,4)。

    单价默认取全局配置；多供应商链传入**该供应商自己的单价**，
    否则"单份成本 ≤ 0.5 元"的看板会把不同模型的价差算错。
    """
    p_in = settings.llm_price_input_per_m if price_in is None else price_in
    p_out = settings.llm_price_output_per_m if price_out is None else price_out
    cost = tokens_in / 1_000_000 * p_in + tokens_out / 1_000_000 * p_out
    return round(cost, 4)
