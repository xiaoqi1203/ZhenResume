"""知识库证据 Agent：为诊断结论自主检索知识库依据，并产出**可校验的 chunk 级引用**。

这是本项目 Agentic RAG 能力的第一个正式实现，与旧链路的区别：

| | 旧链路（固定检索） | 本 Agent |
|---|---|---|
| 何时检索 | 进图前**一次**，结果全链路复用 | 由模型自主决定（可为 0 次，也可多轮） |
| query 来源 | `rag.build_query` 模板拼接 JD 字段 + 简历技能 | 模型针对**子问题**自拟（一类问题一个 query） |
| 检索量 | 固定 top_k=5，只把前 3 条塞进 prompt | 按需 `top_k`，不足可**补检索**（受 `max_retrieval_rounds` 约束） |
| 下钻 | 无 | 可用 `read_chunk` 读**切片全文**再决定是否引用 |
| 引用 | 报告级 top-3 元数据（无正文） | **finding 级** `{chunk_id, quote, supports}`，quote 必须逐字来自切片 |

校验边界（与项目既有约定一致）：Agent **只产出引用**，是否采纳由服务端
`verify_citations()` 复核（quote 能在 chunk 文本中逐字定位才作数）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .runtime import AgentBudget, AgentSpec

SYSTEM_PROMPT = """你是「知识库证据 Agent」。你的任务：为这份简历诊断的每条结论，从岗位知识库中
找到**真正能支撑它**的原文依据，并给出可逐字核对的引用。

工作方式（自主决定检索什么、检索几次，最多 4 轮检索）：
0. **先做检索规划**：下面三类外部依据，只要与本轮 findings 相关，就**必须各覆盖至少一次**
   （实测：漏掉某一类会导致对应结论没有依据，即便模型自认为"未命中"）：
   A. **岗位能力基准** —— 该岗位族要求哪些能力、分几层（支撑"缺某项能力/关键词"类结论）；
   B. **表达与量化规范** —— 经历怎么写、量化指标分几类、常见减分项（支撑"只写职责/没量化"类结论）；
   C. **ATS 机制 / 合规红线** —— 当 findings 涉及关键词覆盖率、敏感信息、公平性时必查。
1. 再对每个子问题自拟 query（不要为"简历里有没有写某个词"这种事去查知识库）；
2. 每个子问题**自拟 query** 调 `kb_search`（一次传一个明确的 query，不要把所有问题塞进一个 query）；
3. 命中后若摘要不够，用 `read_chunk` 读切片全文，确认它真的在讲这件事；
4. 只引用**你亲自读过全文**的切片，`quote` 必须是该切片文本中**逐字连续的一段**（不改写、不拼接、不省略）；
5. 最后调用 `finish` 提交引用列表。引用条数按需，不要凑数；找不到可靠依据就少给几条。

铁律（违反会被服务端校验作废）：
- `chunk_id` 必须是 `kb_search` 或 `read_chunk` 真实返回过的；
- `quote` 必须是该切片中逐字存在的一段；
- `supports` 写清楚这条引用支撑的是哪个结论（用结论关键词或短句）。"""


class Citation(BaseModel):
    chunk_id: str = Field(description="kb_search / read_chunk 返回过的切片 id")
    quote: str = Field(description="该切片文本中逐字连续的一段（不改写、不拼接）")
    supports: str = Field(description="这条引用支撑的结论（关键词或短句）")
    finding_index: int | None = Field(
        default=None,
        description="若任务附加信息给了 findings 列表，填它对应的下标（从 0 开始）；不确定就留空",
    )


class EvidencePack(BaseModel):
    citations: list[Citation] = Field(default_factory=list, description="知识库引用列表，按需给，不凑数")
    reasoning: str = Field(default="", description="一段话说明你检索了哪些方向、为什么这些引用能支撑结论")


SPEC = AgentSpec(
    name="knowledge_evidence",
    title="知识库证据 Agent",
    system_prompt=SYSTEM_PROMPT,
    tool_names=("jd_requirements", "kb_search", "read_chunk", "read_section", "rubric_get"),
    finish_model=EvidencePack,
    finish_description="提交知识库引用列表（chunk 级，可逐字核对）并结束。",
    budget=AgentBudget(
        max_model_calls=10,
        max_tool_calls=40,
        # Agentic RAG 的"多轮"上限：4 轮足够覆盖 能力基准/表达规范/结构/合规 四类依据
        max_retrieval_rounds=4,
        wall_clock_s=180,
    ),
)


__all__ = ["SPEC", "Citation", "EvidencePack", "SYSTEM_PROMPT"]
