"""Agent 注册表：新增一个 Agent 只需写 `AgentSpec` 并 `register()`。"""

from __future__ import annotations

from .runtime import AgentSpec

SPECS: dict[str, AgentSpec] = {}


def register(spec: AgentSpec) -> AgentSpec:
    if spec.name in SPECS and SPECS[spec.name] is not spec:
        raise ValueError(f"Agent 重名：{spec.name}")
    SPECS[spec.name] = spec
    return spec


def get_spec(name: str) -> AgentSpec:
    try:
        return SPECS[name]
    except KeyError as exc:
        raise KeyError(f"未注册的 Agent：{name}；已注册 {sorted(SPECS)}") from exc


# ---- 内置 Agent（导入即注册）----
#
# 单份诊断的 agent 线用到两类 Agent：
#   1. 分析类（jd_match / ats / impact / risk）—— finalize（规则评分）之前的判断全由它们做；
#   2. 证据类（knowledge_evidence）—— qc 之后为最终结论挂 chunk 级知识库引用。
#
# 全部分开注册：`get_spec(name)` 按名字取，grh 建图时按契约步名映射（见 ANALYZER_SPECS）。
from .analyzers import (  # noqa: E402
    ANALYZER_SPECS,
    ATS_SPEC,
    IMPACT_SPEC,
    JD_MATCH_SPEC,
    RISK_SPEC,
)
from .evidence import SPEC as _evidence_spec  # noqa: E402

for _spec in (ATS_SPEC, IMPACT_SPEC, RISK_SPEC, JD_MATCH_SPEC, _evidence_spec):
    register(_spec)

__all__ = ["SPECS", "ANALYZER_SPECS", "get_spec", "register"]
