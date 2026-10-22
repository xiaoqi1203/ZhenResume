"""Agentic RAG 运行时：模型装配、工具装配、预算、trace。

## 本包服务哪条线（**先搞清这个再读代码**）

项目有两条线，由 `WORKFLOW_MODE` 切换：

| | `fixed` 线 | `agentic` 线（本包） |
|---|---|---|
| 检索 | **fixed RAG**：`retrieve_node` 用 `rag.build_query()` 拼 query、跑一次 top_k=5 | **Agentic RAG**：模型自己决定何时调 `kb_search`、query 自拟、可多轮、可 `read_chunk` 下钻 |
| 分析 | 每个节点 `llm.complete_json()` **单次调用** | `create_agent(model, tools)` **有界循环** |
| 谁决定查什么 | 代码（提示词模板写死） | 模型（每轮决定调哪个工具） |

⚠️ **两个容易说错的说法**：
1. 「fixed 线是 LLM 检索」—— **错**。fixed 的检索是纯代码拼 query + 向量检索，
   全程无 LLM 参与；fixed 线里有 LLM，但**检索决策权不在模型手里**。
2. 「fixed 线没有 RAG」—— **也错**。它有 RAG，只是不 agentic。
   Agentic RAG 的判据是「检索策略由模型自主决定」，不是「有没有检索」。

## 本包与一次性实验（`services/agent_langchain.py`）的区别

| | 一次性实验（P2-c） | 本框架 |
|---|---|---|
| Agent 数量 | 1 个硬编码 | 任意个，声明 `AgentSpec` 即可接入 |
| 输出 | 固定 judgments | 任意 Pydantic `finish_model` |
| 工具 | 全量 7 个 | 按 Agent 声明裁剪（`tool_names`） |
| 预算 | 全局配置 | 每个 Agent 独立 `AgentBudget`（含**检索轮次**上限） |
| trace | 返回后即丢 | 结构化 trace（含逐次往返 `call_timings`），供落库与前端展示 |

## Agent 清单

| Agent | 何时跑 | 产出 |
|---|---|---|
| `jd_match_agent` / `ats_agent` / `impact_agent` / `risk_agent` | `finalize`（规则评分）**之前** | findings（含真差距/表述差异判断） |
| `knowledge_evidence` | `qc` **之后**（可选） | finding 级 chunk 引用 |

⚠️ **评分不读这些 Agent 的产出**：`finalize_node` 只吃 `parsed_resume/parsed_jd/role_family/raw_text/jd_text`，
内部再跑一遍 `rule_baseline_findings`。所以同一份简历两条线**分数必然相同** —— 这是设计，
目的是保证「同一简历两次诊断分数一致」（否则 Agent 每次写法不同，分数就会漂）。

设计原则（与项目既有约定一致）：
- **模型与工具都复用已有实现**：模型走 `settings.llm_chain` 的供应商×模型阶梯，
  工具走 `services.agent_tools.ToolRunner`（与旧流水线同一份执行器）；
- **不推翻确定性**：Agent 只产出"判断 + 证据"，是否采纳由服务端校验层决定
  （引文必须 `locate_quote` 能定位、chunk 必须存在）；
- **失败不静默**：预算耗尽/递归超限/墙钟超时都会在 trace 里留
  `truncated`/`forced_finish`/`recursion_hit`/`timeout_hit`。
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
