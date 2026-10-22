"""分析类 Agent 声明：**让 finalize（规则评分）之前的分析全部由 Agent 自主完成**。

这是「agent 线」的核心 —— 与 fixed 线的差别不是"多跑一个节点"，而是
**分析阶段的每一个判断都由模型自主调用封装工具取得，而不是一次性喂满上下文再要答案**：

| | fixed 线 | agent 线（本模块） |
|---|---|---|
| jd_match / ats / impact / risk | `llm.complete_json()` 单次调用 | `create_agent(model, tools)` 有界循环 |
| 模型能做什么 | 只能读提示词里塞进去的内容 | 自主决定读哪段简历、查哪些 JD 要求、下钻哪个知识库切片、核验哪句引文 |
| 证据 | 靠提示词要求"给引文"，再由后端定位 | 用 `locate_quote` **自己先核验**，核验不过就不提交 |
| 知识库 | 进图前固定检索一次，全链路复用 | 每个 Agent 按自己的子问题自拟 query 检索 |

## 为什么这四个 Agent 各自声明，而不是合成一个

它们的**关注点互不重叠且结论会互相打架**：
- `ats`：机器可读性 + 关键词覆盖（事实层）
- `impact`：量化与影响力表达（表达层）
- `risk`：时间线/矛盾/合规（风险层）
- `jd_match`：真差距 vs 表述差异（匹配层）

合成一个 Agent 会让它在同一次运行里既当"找茬的"又当"辩解的"（jd_match 本来就负责推翻误报），
结论质量会下降；分开也让 trace 能一眼看出"哪个维度用了几个工具、查了什么"。

## 三条边界（与固定线共享，一行不改）

1. **评分不进 Agent**：分数仍由 `analyze.compute_scores_deterministic` 算，Agent 只产出 findings；
2. **合规不由 Agent 裁决**：歧视性内容仍由 `qc_agent` 的正则四查否决；
3. **Agent 不能凭空造结论**：每条结论都要过 `evidence_validator`（引文必须能在原文定位）。

## 与 fixed 线的规则基线关系

Agent **不是**从零开始分析 —— 每个节点仍会先算规则基线（`analyze.ats_findings` 等），
把基线作为 `extra` 喂给 Agent。这样：
- Agent 有确定性锚点（不会因为模型波动而漏掉"空窗期"这种硬事实）；
- 模型只做**增量**：补充、修正、剔除误报 —— 这正是 `jd_match` 一直承担的职责。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .runtime import AgentBudget, AgentSpec

# --------------------------------------------------------------------------- #
# 共用：findings 结构（与 fixed 线一致，便于下游 suggestion/qc/persist 复用）
# --------------------------------------------------------------------------- #
class AgentFinding(BaseModel):
    """一条结论。字段与 `coerce_findings` 的产出逐字对应，下游无需适配。

    ## 为什么每个字段都标了硬字数上限（实测）

    单次分析的耗时几乎全在**最后一次 `finish`**：实测 risk Agent 7 次往返共 34s，
    其中 `finish` 那次占 **15.2s / 45%** —— 因为它要一口气吐出 5 条结论的全部字段
    + 每条引文 + 每条知识库引用。生成时间 ≈ 输出 token × 10ms，输出 1090 token
    就是 11s 起步。

    所以这里的 `max_length` 不是"为了好看"，而是**直接决定这个 Agent 快不快**。
    同时它也让内容更可读：报告里这就是几条短句，长段落会被前端截断。
    """

    category: str = Field(description="ats | impact | clarity | risk | gap")
    severity: str = Field(description="red | yellow | green")
    title: str = Field(max_length=40, description="≤20 字，一句话说清问题")
    original_text: str = Field(
        default="",
        max_length=200,
        description="简历原文中**逐字连续**的片段；结构性结论（无具体句子）留空",
    )
    issue: str = Field(max_length=120, description="≤40 字：为什么是问题、HR 会怎么解读")
    suggestion: str = Field(max_length=100, description="≤40 字：怎么改")
    rewritten_text: str = Field(
        max_length=260,
        description="可直接复制的改写句；缺数据用 [补充：…] 占位，禁止编造数字",
    )
    evidence_quote: str = Field(
        default="",
        max_length=200,
        description="支撑这条结论的原文引文（**必须先用 locate_quote 核验能定位**）；无引文留空",
    )
    evidence_field: str = Field(default="", max_length=60, description="引文所在区块，如 work[0].highlights")
    citation_chunk_id: str = Field(
        default="",
        description="若这条结论有知识库依据，填 kb_search/read_chunk 真实返回过的 chunk_id；没有留空",
    )
    citation_quote: str = Field(
        default="",
        description="知识库切片中**逐字连续**的一段；没有则留空",
    )


class AnalysisOutput(BaseModel):
    """一个分析 Agent 的产出。

    刻意只给 `findings` + 一段极短 summary：
    旧链路每条 Agent 都要写一段 `ats_summary` / `impact_summary`，但这些总结
    **在最终报告里从不展示**（报告用的是 `report_agent` 的 summary）。
    让模型写它们纯粹是浪费输出 token（实测输出增长 30%+，生成时间同比例上涨）。
    """

    findings: list[AgentFinding] = Field(
        default_factory=list, description="本次维度发现的结论，最多 5 条"
    )
    summary: str = Field(
        default="", description="≤60 字：这个维度的总体判断（不展示给用户，仅存 trace）"
    )


class JdMatchOutput(BaseModel):
    """JD 匹配 Agent 的产出：它额外负责**推翻误报**，所以多两个字段。"""

    findings: list[AgentFinding] = Field(default_factory=list, description="真差距，最多 4 条")
    false_gaps: list[str] = Field(
        default_factory=list,
        description="被判定为「表述差异」的关键词：简历其实具备，只是用词与 JD 不同。"
        "每项必须能在简历原文找到对应片段，否则不要写进来",
    )
    missing_keywords: list[str] = Field(
        default_factory=list, description="复核后仍然缺失的关键词"
    )
    summary: str = Field(default="", description="≤60 字总体匹配判断")


# --------------------------------------------------------------------------- #
# 工具组合
# --------------------------------------------------------------------------- #
# 分析类 Agent 共用同一套取证工具。**不含**写入类能力，全部只读 ——
# 与 `ToolRunner` 的设计一致（执行器持有只读上下文）。
#
# ⚠️ 两个批量工具（`resume_batch_search` / `locate_batch`）是**实测加进来的**：
# 第一版只给了单个版本，结果 ats Agent 为 10 个关键词各调一次 `resume_search`、
# 为 6 条引文各调一次 `locate_quote` —— 12 次模型往返、28 次工具、55 秒，
# 最后还因预算耗尽被截断（产出为空）。批量版本让同一件事一次做完。
_ANALYSIS_TOOLS: tuple[str, ...] = (
    "jd_requirements",       # 看岗位要求（核心/加分/关键词/职责）
    "resume_search",         # 单个关键词查找（需要看上下文片段时用）
    "resume_batch_search",   # ★批量核验多个关键词是否出现过（核缺失清单必用）
    "read_section",          # 读简历某区块全文（work/projects/skills/education）
    "locate_quote",          # 单个引文自检
    "locate_batch",          # ★批量核验多条引文能否定位（提交前必用）
    "kb_search",             # 检索岗位知识库（能力模型/表达规范/合规）
    "read_chunk",            # 下钻读知识库切片全文
)

# --------------------------------------------------------------------------- #
# 共用的输出纪律（所有分析 Agent 的系统提示词都拼这一段）
# --------------------------------------------------------------------------- #
_OUTPUT_RULES = """
## 输出纪律（违反会导致这条结论被服务端丢弃）

1. **先核验再提交**：任何要写进 `evidence_quote` / `original_text` 的片段，
   提交前必须用 **`locate_batch`**（多条一起核）或 `locate_quote`（单条）确认
   它能在简历原文中**逐字定位**。定位失败就不要写这条引文。
2. **引文必须逐字连续**：不得改写、不得拼接、不得省略中间内容、不得只写关键词。
3. **不编造**：不得虚构数字、公司、项目、技术栈。缺数据用 `[补充：…]` 占位。
   ⚠️ **知识库里的数字是示例，不是用户的数据**：切片正文里常带范例（如「CPU 从 85% 降至 45%」
   「P99 从 480ms 降至 120ms」），那些数字属于示例，**绝对不允许抄进 `rewritten_text`**。
   用户的改写句只能出现两类数字：① 简历原文里已有的；② `[补充：…]` 占位符。
   实测事故：模型把 KB 示例的 `85%` 抄进一条简历里根本没有 CPU 数据的改写句，
   HR 一追问「你这个 85% 哪来的」就穿帮 —— 这比不写数字伤害大得多。
4. **短**：title ≤20 字、issue ≤40 字、suggestion ≤40 字、rewritten_text ≤60 字。
   报告是给人快速扫读的，长段落会被前端截断，也会拖慢生成。
5. **最多 5 条**：合并同类问题，不要为了凑数把一个事实拆成多条。
6. **改写句必须能直接粘贴进简历**：包含原始大小写（`Vue` 不是 `vue`）、
   说明放在哪个区块、能独立成句。不要写「建议优化表述」这类空话。
7. 最后调用 `finish` 提交结构化结果，不要输出其它文字。

## 知识库依据（**这是你与固定流水线最大的区别，务必用上**）

8. **先查规范，再写改写句**：只要你的结论涉及**量化指标 / 表达结构 / ATS 机制 / 合规红线**，
   必须先 `kb_search` 检索该岗位族的规范，再**基于检索到的正文**写 `rewritten_text`。
   - 例：写"补上量化数据"这类建议前，先查「后端量化标准」，用它列出的指标名（P99 / QPS /
     可用性 / MTTR）填占位符，而不是写泛泛的 `[补充指标]`；
   - 例：判断"只写职责没量化"严重度前，先查「经历要点的黄金表达结构」看标准写法。
   - 附加信息里的 `knowledge_seed` 是**系统已经替你检索过的 3 条起点依据**（含 chunk_id），
     可以直接引用；不够用时再用 `kb_search` 补查，不要重复查同一个主题。
9. **有依据就填引用**：每条**有知识库依据**的结论，必须填
   `citation_chunk_id`（`kb_search` / `read_chunk` 真实返回过的 id）与
   `citation_quote`（该切片里**逐字连续**的一段，不改写、不拼接、不省略）。
   服务端会逐字校验：quote 在该切片里定位不到，这条引用直接作废；chunk_id 不是本次
   真实检索返回过的，也会被拒。**没有可靠依据就不要硬填**，留空比编造好。
10. **不要为"简历里有没有某个词"去查知识库**：那是 `resume_batch_search` 的活。

## 效率纪律（很重要：你的预算是有限的）

- **核验一批关键词用 `resume_batch_search`**，不要为每个词各调一次 `resume_search`
  —— 逐个查会耗尽预算，导致你还没提交就被中断，本轮分析直接作废；
- **核验一批引文用 `locate_batch`**，不要在提交前逐条 `locate_quote`；
- 读简历用 `read_section` 一次拿全一个区块，不要反复用关键词试探；
- **知识库检索合并成 1–2 次**：一次 `kb_search` 的 query 写清你要的规范主题
  （例：「后端量化标准 规模 性能 稳定性」），不要为每条结论各查一次；
- **先取证，最后一次性 `finish`**：不要在中途反复"再确认一下"。

## ⚠️ 关于检索的实测事实（读一下，能省你 1–2 轮往返）

- 你的**窄 query**（只指向一类文档，如「后端能力模型 分层」）单轮只能覆盖约 **55%** 的目标依据；
  JD 驱动的**宽 query**（岗位名 + 多个技能 + 多个关键词）能覆盖 **89%**。
- ⚠️ 但实测（2026-10-21）**不要在一条 query 里硬堆多个维度** —— 那会让检索变得不聚焦，
  第 1 轮覆盖反而下降。**正确做法是按维度分多次查**，一次一个明确方向：
  第 1 次查岗位能力基准 → 第 2 次查表达/量化规范 → 需要时第 3 次查 ATS/合规。
  这正是你有多轮预算的原因。
- 需要某一类依据时，**用 `scope` 参数点名**（`role` / `resume_template` / `industry` / `compliance`），
  比在 query 里堆词更精确。
- 返回里的 `scopes_covered` 告诉你这次覆盖了哪几类 —— 看到只有 1 类、且结论需要别的依据时，
  就再查一次，别硬用不相关的切片凑引用。
"""

# --------------------------------------------------------------------------- #
# 四个分析 Agent
# --------------------------------------------------------------------------- #
ATS_SYSTEM_PROMPT = f"""你是「ATS 可读性 Agent」。你审查的是**机器能否正确读取与匹配这份简历**。

你的关注点（只做这一层，不要评价候选人能力强弱）：
- 关键词覆盖：JD 要求的能力，简历里是否有对应表述？请在简历中实际检索后判断，
  **不要凭印象**——用 `resume_search` 查关键词，用 `read_section` 看技能栏与项目区块；
- 结构可解析性：有没有独立技能栏、联系方式是否可提取、篇幅是否过长、是否用了表格线；
- 表述一致性：同一能力在技能栏与经历里用词是否一致（A 处写 `K8s`、B 处写 `Kubernetes`
  会导致机器认为"只覆盖了一项"）。

工具用法建议：
- `jd_requirements` 看 JD 要求清单；
- **`resume_batch_search` 一次核验整批缺失关键词**是否真的没写（而不是写法不同）——
  这是本维度的核心动作，务必用它而不是逐个 `resume_search`；
- `read_section("skills")` 看技能栏结构；
- 需要知道"关键词没覆盖会被怎样对待"时用 `kb_search`。

严重度约定：**关键词整体覆盖率 < 50% 时，第一条标 red，其余 yellow**；
单个关键词缺失本身不足以标 red（同一个事实不该产出多条红色）。
{_OUTPUT_RULES}"""

IMPACT_SYSTEM_PROMPT = f"""你是「影响力 Agent」。你审查的是**经历描述有没有证明贡献量级**。

你的关注点：
- 是否量化：写的是"做了什么"还是"做到什么程度"？有数字吗？
- 动词强度：是否用「主导 / 设计 / 重构 / 优化 / 搭建」这类强动词，
  还是通篇「负责 / 参与 / 协助」；
- 绝对化表述：是否滥用「精通 / 全面负责 / 完全独立」这类经不起追问的词。

工具用法建议：
- `read_section("work")` / `read_section("projects")` 读全部经历要点；
- `resume_search` 定位某条要点在原文中的位置；
- **`kb_search` 是这个维度的必做动作**：先查该岗位族的量化标准（如后端查
  「后端工程师能力模型与量化标准」），用检索到的**指标名**（QPS / P99 / 可用性 / MTTR /
  规模 / 成本）写占位符，让改写建议贴着岗位规范而不是泛泛而谈；
- 附加信息里的 `metric_hints_fallback` 是**硬编码兜底表**，只有在 `kb_search` 没命中
  或没有可用检索时才参考它 —— 优先用检索到的正文；
- 改写句写完后用 `locate_quote` 核验原句能定位。

**严禁编造数字**：缺数据一律写成 `[补充指标：QPS]` 这种占位。
{_OUTPUT_RULES}"""

RISK_SYSTEM_PROMPT = f"""你是「风险 Agent」。你审查的是**会让 HR 追问或直接扣分的硬伤**。

你的关注点：
- 时间线：空窗期、时间重叠、只有年份没有月份、教育经历未写结束时间；
- 自相矛盾：技能栏与项目描述对不上（项目用了但技能栏没写，或反过来）；
- 夸大：无数据支撑的"显著提升""大幅优化"；
- 合规：出现性别/年龄/婚育/户籍/身高体重等与岗位无关的敏感信息。

工具用法建议：
- `read_section("work")` / `read_section("education")` 拿结构化时间线；
- `resume_batch_search` 一次查"至今""在职""目前""实习"这类会影响时间与身份判断的表述；
- `kb_search` 查「简历结构与排版规范」「公平性红线」等依据。

**敏感信息只描述为"建议删除以规避合规争议"，绝不据此评价候选人能力或匹配度。**
空窗期已经有了说明（备考/进修/自由职业等）时，不要再要求用户"补一句说明"——
让人做已经做过的事最伤信任。
{_OUTPUT_RULES}"""

JD_MATCH_SYSTEM_PROMPT = f"""你是「JD 匹配 Agent」。你的核心职责是**区分真差距与表述差异**。

这是唯一允许你推翻规则层结论的维度：
- **真差距**：JD 要求某项能力，简历里确实没有对应经历 → 进 `findings`；
- **表述差异**：简历其实做了这件事，只是用词与 JD 不同（JD 写"服务治理"、简历写"微服务拆分"）
  → 进 `false_gaps`，并**必须能在简历原文找到对应片段**才能这么判。

工具用法建议：
1. `jd_requirements(detail="all")` 拿完整要求清单；
2. 对规则层给的缺失清单，**用 `resume_batch_search` 一次全部核验**——
   不要为每个词各调一次 `resume_search`（会耗尽预算，导致本轮作废）；
3. 判为表述差异的，用 `locate_batch` 验证你找到的简历片段确实能定位；
4. `missing_keywords` 只填复核后**确实没有**的；
5. 真差距才进 `findings`，每条写清"HR 会怎么理解这个缺口"。

宁可少判几条，也不要把"其实具备"的能力判成缺失 —— 那会让用户为了不存在的缺口改简历。
{_OUTPUT_RULES}"""

# --------------------------------------------------------------------------- #
# Agent 声明
# --------------------------------------------------------------------------- #
# 预算说明（实测调参，2026-10-18）：
#
# ⚠️ **不能按"最少几步"给预算** —— 第一次给 6 次模型往返，实测四个 Agent 全部
# `truncated=True`（预算耗尽被中间件强制结束，`finish` 没被调用，产出为空）：
#
#     ats:     7 次往返 / 19 次工具，工具序列 = jd_requirements → read_section
#              → resume_search ×10 → kb_search ×2 → read_section → locate_quote ×3
#              （它在逐个核验 10 个缺失关键词，每种都单独查一次）
#
# 这说明**分析 Agent 的自然轨迹本来就比证据 Agent 长**：证据 Agent 只查 3 类依据，
# 而分析 Agent 要逐项核实 JD 要求。按最少步数给预算只会让它"查一半就被掐断"。
#
# 现在按实测自然收敛值给足：模型往返 10 次、工具 40 次。
# 同时提高墙钟到 150s —— 慢但完整，好过快要但空。
_BUDGET = AgentBudget(
    max_model_calls=10,
    max_tool_calls=40,
    # 分析 Agent 的检索是"要知道这条规范怎么说"，1-2 轮足够；
    # 实测算上补检索也就在 2 轮内（第一轮查 2 个 query 就够用）。
    max_retrieval_rounds=2,
    wall_clock_s=150,
)

ATS_SPEC = AgentSpec(
    name="ats_agent",
    title="ATS 可读性 Agent",
    system_prompt=ATS_SYSTEM_PROMPT,
    tool_names=_ANALYSIS_TOOLS,
    finish_model=AnalysisOutput,
    finish_description="提交本次 ATS 维度的结论（findings + ≤60 字 summary）并结束。",
    budget=_BUDGET,
)

IMPACT_SPEC = AgentSpec(
    name="impact_agent",
    title="影响力 Agent",
    system_prompt=IMPACT_SYSTEM_PROMPT,
    tool_names=_ANALYSIS_TOOLS,
    finish_model=AnalysisOutput,
    finish_description="提交本次影响力维度的结论（findings + ≤60 字 summary）并结束。",
    budget=_BUDGET,
)

RISK_SPEC = AgentSpec(
    name="risk_agent",
    title="风险 Agent",
    system_prompt=RISK_SYSTEM_PROMPT,
    tool_names=_ANALYSIS_TOOLS,
    finish_model=AnalysisOutput,
    finish_description="提交本次风险维度的结论（findings + ≤60 字 summary）并结束。",
    budget=_BUDGET,
)

JD_MATCH_SPEC = AgentSpec(
    name="jd_match_agent",
    title="JD 匹配 Agent",
    system_prompt=JD_MATCH_SYSTEM_PROMPT,
    tool_names=_ANALYSIS_TOOLS,
    finish_model=JdMatchOutput,
    finish_description="提交真差距、表述差异与复核后的缺失关键词，并结束。",
    budget=_BUDGET,
)

# 节点名 → AgentSpec 的映射（graph 建图时按契约步名取用）
ANALYZER_SPECS: dict[str, AgentSpec] = {
    "jd_match": JD_MATCH_SPEC,
    "ats": ATS_SPEC,
    "impact": IMPACT_SPEC,
    "risk": RISK_SPEC,
}

__all__ = [
    "AgentFinding",
    "AnalysisOutput",
    "JdMatchOutput",
    "ANALYZER_SPECS",
    "ATS_SPEC",
    "IMPACT_SPEC",
    "RISK_SPEC",
    "JD_MATCH_SPEC",
]
