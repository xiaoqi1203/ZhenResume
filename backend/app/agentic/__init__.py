"""Agentic RAG 运行时：模型装配、工具装配、预算、trace。

本包是"Agent 封装"的框架层，与之前的一次性实验（`services/agent_langchain.py`）的区别：

| | 一次性实验（P2-c） | 本框架 |
|---|---|---|
| Agent 数量 | 1 个硬编码 | 任意个，声明 `AgentSpec` 即可接入 |
| 输出 | 固定 judgments | 任意 Pydantic `finish_model` |
| 工具 | 全量 7 个 | 按 Agent 声明裁剪（`tool_names`） |
| 预算 | 全局配置 | 每个 Agent 独立 `AgentBudget`（含**检索轮次**上限） |
| trace | 返回后即丢 | 结构化 trace，供落库与前端展示 |

设计原则（与项目既有约定一致）：
- **模型与工具都复用已有实现**：模型走 `settings.llm_chain` 的供应商×模型阶梯，
  工具走 `services.agent_tools.ToolRunner`（与旧流水线同一份执行器）；
- **不推翻确定性**：Agent 只产出"判断 + 证据"，是否采纳由 `services/semantic` 等校验层决定；
- **失败不静默**：预算耗尽/递归超限都会在 trace 里留 `truncated`/`forced_finish`/`recursion_hit`。
"""

from .models import build_models
from .runtime import AgentBudget, AgentInputs, AgentRun, AgentSpec, BaseAgent, build_tools
from .registry import SPECS, get_spec, register

__all__ = [
    "AgentBudget",
    "AgentInputs",
    "AgentRun",
    "AgentSpec",
    "BaseAgent",
    "build_models",
    "build_tools",
    "SPECS",
    "get_spec",
    "register",
]
