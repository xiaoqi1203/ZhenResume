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
from .evidence import SPEC as _evidence_spec  # noqa: E402

register(_evidence_spec)

__all__ = ["SPECS", "get_spec", "register"]
