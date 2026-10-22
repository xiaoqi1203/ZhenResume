"""gold v2 标注：把"哪条切片支撑哪条结论"写成可审计的映射表。

## 为什么需要 v2（v1 的问题）

v1 的 gold 是**按文档手写**的固定列表：

```python
{"id": "rag_A00", "gold": [("后端工程师能力模型与量化标准", 0), ...]}
```

它在扩库前够用，但扩库后（19 篇 / 43 切片）暴露两个缺陷：

1. **只覆盖 5 篇自撰文档**，8 篇外部权威语料（O*NET / McMaster / Dartmouth /
   SocialTalent / EEOC）**完全不计分** —— 检索器把 O*NET 排到第 1 位也不加分；
2. **gold 与"该简历实际有什么问题"脱节**：不管这份简历缺的是 Kubernetes 还是量化，
   gold 都是同一组切片。这让 recall 变成"能不能召回到固定三篇"，而不是
   "能不能召回支撑结论的依据"。

## v2 的做法：从「结论」推出「依据」

gold 的定义应该是：**对这份简历的每条规则层结论，知识库里哪些切片真正能支撑它。**

所以 v2 是**两级映射**，每一级都显式写理由：

```
规则层结论（topic）           →  知识库切片（doc_title, chunk_index）
"经历只写了职责，没有量化"     →  表达结构#0（弱/强对照）、表达结构#1（禁用词清单）、
                                 后端能力模型#1（量化标准）、McMaster#1（量化结果）
```

### 为什么这是可审计的

- 每条映射都带 `why`，能人工复核"这个切片真的支撑这个结论吗"；
- topic 来自**规则层的实际输出**（`analyze.rule_baseline_findings`），不是拍脑袋；
- 映射表本身是数据，改一条不影响代码。

### 已知边界（诚实记录）

- **"开源"这类结论没有对应切片**：知识库里确实没有讲开源贡献的文档。
  这种情况 gold 里**不加任何切片**（不是随便找一篇凑数）—— 这也是为什么
  recall 不会到 100%，分母里含"无依据可引"的结论。
- 映射是**人工判断**，不是自动生成。自动生成（如"取同文档前 N 片"）已被实测否证：
  这样算出来的 recall 是 0.00，而 top-5 里明明有该切片。

## 用法

```python
from scripts.gold_v2 import gold_for_case
gold = gold_for_case("rag_A00")   # -> [(doc_title, chunk_index), ...]
```
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# 结论 topic → 支撑切片
# --------------------------------------------------------------------------- #
# key 是 topic 名；value 是 [(doc_title, chunk_index, why)]
#
# topic 命名与规则层发现项对齐（见 `analyze.rule_baseline_findings`）：
#   kw:<关键词>        = "JD 要求的关键词 X 在简历中缺失"
#   impact:unquantified = "经历只写了职责，没有量化结果"
TOPIC_CHUNKS: dict[str, list[tuple[str, int, str]]] = {
    # ---- 关键词缺失类：依据来自「能力模型说明该能力是什么」+「ATS 说明关键词为何重要」
    "kw:Kubernetes": [
        ("后端工程师能力模型与量化标准", 0, "工程素养层要求「容器化部署」，说明该能力在岗位能力模型里的位置"),
        ("ATS 关键词匹配的实际工作方式", 1, "要求「中英文技术名词同时出现一次（Kubernetes（K8s））」，直接支撑为何要写这个词"),
        ("Dartmouth 大学：理解申请人追踪系统（ATS）的官方建议", 1, "「关键词要与岗位描述一致」是 ATS 匹配的官方建议"),
    ],
    "kw:微服务": [
        ("后端工程师能力模型与量化标准", 0, "分布式与稳定性层讲「服务治理」，是微服务能力的上位概念"),
        ("ATS 关键词匹配的实际工作方式", 1, "「用 JD 的原始用词，不要自己换同义词」——微服务就是被换掉的同义词场景"),
        ("O*NET 15-1299.08 计算机系统工程师/架构师：官方任务", 1, "官方任务含「开发系统集成或分布式系统架构」，是微服务能力的权威上位描述"),
    ],
    "kw:分布式": [
        ("后端工程师能力模型与量化标准", 0, "「分布式与稳定性」层明确列出 CAP、服务治理、分布式事务"),
        ("O*NET 15-1299.08 计算机系统工程师/架构师：官方任务", 1, "官方任务「开发系统工程、软件工程、系统集成或分布式系统架构」直接对应"),
    ],
    "kw:高并发": [
        ("后端工程师能力模型与量化标准", 0, "数据与存储层讲「高并发系统设计」所需的索引、缓存、消息队列"),
        ("O*NET 15-1252.00 软件开发者：官方任务与工作活动", 1, "官方任务含「确定系统性能标准」，高并发属于性能标准范畴"),
        ("O*NET 15-1299.08 计算机系统工程师/架构师：官方任务", 1, "官方任务「验证系统架构的稳定性、可扩展性」是并发能力的验证口径"),
    ],
    "kw:稳定性": [
        ("后端工程师能力模型与量化标准", 0, "「分布式与稳定性」层：能说清自己负责的服务的可用性指标与故障处理经历"),
        ("后端工程师能力模型与量化标准", 1, "稳定性量化标准：可用性百分比、故障恢复时间（MTTR）、线上事故数量下降"),
        # ★ 补标（2026-10-22）：初版漏了这条，而它恰好是检索器排到前 3 的那一片。
        #   审计发现 8 个 topic 里 4 个完全没引外部权威语料，这是最典型的一处。
        ("O*NET 15-1299.08 计算机系统工程师/架构师：官方任务", 1, "官方任务「验证系统架构的稳定性、可扩展性、安全性」——比自撰规范更权威的同主题依据"),
    ],
    "kw:性能优化": [
        ("后端工程师能力模型与量化标准", 1, "性能量化标准：P99 延迟、响应时间优化幅度、吞吐提升倍数"),
        ("经历要点的黄金表达结构", 0, "「参与系统性能优化」→「定位并解决数据库全表扫描问题…慢查询下降 92%」的强弱对照"),
        ("O*NET 15-1254.00 Web 开发者：官方任务与性能要求", 1, "官方任务「推荐并实施性能改进」「记录服务器负载、带宽、数据库性能」——性能优化的官方动作清单"),
    ],
    "kw:SQL": [
        ("后端工程师能力模型与量化标准", 0, "数据与存储层：MySQL 索引原理与慢查询优化、事务隔离级别与锁"),
        ("O*NET 15-1252.00 软件开发者：官方任务与工作活动", 1, "官方任务「存储、检索与处理数据，用于分析系统能力与需求」——SQL 的上位能力描述"),
    ],
    # 「开源」在知识库里**没有任何支撑切片** —— 显式留空，不凑数。
    # 这是 recall 无法到 100% 的真实原因之一，也是诚实标注的一部分。

    # ---- 未量化类：这是知识库里依据最充分的一类
    "impact:unquantified": [
        ("经历要点的黄金表达结构", 0, "核心依据：强动词 + 具体对象 + 方法 + 量化结果，含弱/强对照"),
        ("经历要点的黄金表达结构", 1, "禁用词清单「负责、参与、协助、跟进、配合」+ 三种数字替代角度"),
        ("后端工程师能力模型与量化标准", 1, "量化标准五类：规模 / 性能 / 稳定性 / 成本 / 效率"),
        ("McMaster 大学：成就陈述三段公式与动词表（官方就业中心）", 1, "官方公式 Phase III：「只要可能，就量化结果」"),
        ("McMaster 大学：成就陈述三段公式与动词表（官方就业中心）", 5, "「公式的第三段是最容易缺失的一段，没有第三段等于只写了职责」"),
    ],
    # ---- 表达空话类（2026-10-22 新增）
    #
    # 为什么加这个 topic：实测发现「自我评价怎么写才有信息量」**固定占据前 3 条之一**
    # （10 个 case 里 10 次），但它不在任何 gold 里，于是被判为"排序错误"。
    # 核对后发现：这篇文档的质量很高（讲"把这段换成任何人还成立就是废话"），
    # 它进前 3 是合理的 —— **问题在评测集**：当前 10 份夹具的规则层结论里
    # 没有"表达空话/自我评价"这一类。
    #
    # 处理方式有两种，这里选**补 gold**（而不是改检索器）：
    # - 补 gold：承认"这篇是有效依据"，只要它支撑某类结论；
    # - 改检索器：把一篇高质量文档压下去，等于为了让指标好看而损害真实质量。
    #
    # 注意：本 topic 不在 `CASE_TOPICS` 的默认列表里（这些夹具没有自我评价问题），
    # 所以它不会影响现有 10 个 case 的 gold —— 只为将来扩评测集准备。
    "clarity:self_evaluation": [
        ("自我评价怎么写才有信息量", 0, "「把这段文字换成任何一个同岗位的求职者，如果依然成立，那它就是废话」——空话判断的黄金标准"),
        ("经历要点的黄金表达结构", 1, "「禁用空话：良好的沟通能力、较强的学习能力、有责任心」"),
    ],
}


# --------------------------------------------------------------------------- #
# case → 该简历实际触发的 topic
# --------------------------------------------------------------------------- #
# 下面这张表是**手工标注的样本**（10 例），用于人工复核与回归。
#
# ⚠️ 但真正评测时**不该用这张表** —— 因为它只覆盖 10 份夹具，而库里其实有 32 份
# （`eval/sort_fixtures/sort_{A,B,C,D}{00..07}.txt`）。用 10 例算均值，方差过大，
# 效应量会被噪声淹没（P2 已踩过这个坑：同一指标三次运行 55% / 58% / 42%）。
#
# 所以提供 `derive_topics_from_findings()`：**从规则层输出自动推导 topic**。
# 它是确定性的（规则层可复现），且对任意夹具都能工作 —— 评测集因此从 10 例扩到 32 例。
CASE_TOPICS: dict[str, list[str]] = {
    "rag_A00": ["kw:Kubernetes", "kw:微服务", "kw:稳定性", "kw:开源", "impact:unquantified"],
    "rag_A03": ["kw:Kubernetes", "kw:微服务", "kw:稳定性", "impact:unquantified"],
    "rag_A05": ["kw:Kubernetes", "kw:性能优化", "kw:SQL", "kw:稳定性", "impact:unquantified"],
    "rag_B01": ["kw:Kubernetes", "kw:微服务", "kw:稳定性", "impact:unquantified"],
    "rag_B03": ["kw:Kubernetes", "kw:分布式", "kw:高并发", "impact:unquantified"],
    "rag_C01": ["kw:Kubernetes", "kw:微服务", "kw:高并发", "kw:SQL", "impact:unquantified"],
    "rag_C07": ["kw:Kubernetes", "kw:分布式", "kw:性能优化", "impact:unquantified"],
    "rag_D02": ["kw:Kubernetes", "kw:分布式", "kw:高并发", "kw:性能优化", "impact:unquantified"],
    "rag_D04": ["kw:Kubernetes", "kw:微服务", "kw:高并发", "impact:unquantified"],
    "rag_D06": ["kw:Kubernetes", "kw:分布式", "kw:高并发", "kw:性能优化", "impact:unquantified"],
}

# 规则层发现项 → topic 的映射规则。
#
# 规则层产出的 title 形如「JD 要求的关键词「Kubernetes」在简历中缺失」，
# 这里按关键词表反查；没有对应 topic 的关键词（如「开源」）会被 `TOPIC_CHUNKS` 里的
# 空列表自然过滤掉（gold 不加任何切片，不凑数）。
_KNOWN_KEYWORDS: tuple[str, ...] = (
    "Kubernetes", "微服务", "分布式", "高并发", "稳定性", "性能优化", "SQL", "开源",
)


def derive_topics_from_findings(findings: list[dict]) -> list[str]:
    """从规则层发现项**自动推导** topic 列表（确定性，可复现）。

    ## 为什么需要它

    手工表只覆盖 10 份夹具。库里实际有 32 份（A/B/C/D 各 8 份，覆盖度从 ~100% 到 ~19%），
    用全部 32 份算均值能把方差压下来。

    ## 规则

    - `title` 含「关键词 X 在简历中缺失」→ 映射到 `kw:X`（X 必须在 `_KNOWN_KEYWORDS` 里）；
    - `title` 含「只写了职责」/「没有量化」/「缺量化」→ 映射到 `impact:unquantified`；
    - 其余发现项（如"时间线空窗""敏感字段"）→ **不映射**（知识库里确实没有对应依据，
      强行映射会污染 gold）。

    ## 边界

    这个函数只处理"关键词缺失"与"未量化"两类 —— 因为只有这两类在知识库里有明确依据。
    其余维度（时间线、合规）的 gold 需要单独标注，不在本轮范围内。
    """
    topics: list[str] = []
    seen: set[str] = set()

    def _add(t: str) -> None:
        if t not in seen:
            seen.add(t)
            topics.append(t)

    for f in findings or []:
        title = str(f.get("title") or "")
        for kw in _KNOWN_KEYWORDS:
            if kw in title and ("缺失" in title or "未覆盖" in title or "缺少" in title):
                _add(f"kw:{kw}")
                break
        if any(s in title for s in ("只写了职责", "没有量化", "缺量化", "未量化", "没有量化结果")):
            _add("impact:unquantified")
    return topics


def gold_for_topics(topics: list[str]) -> list[tuple[str, int]]:
    """按 topic 列表取 gold 切片（去重）。"""
    out: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for topic in topics:
        for title, idx, _why in TOPIC_CHUNKS.get(topic, []):
            key = (title, idx)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def gold_for_case(case_id: str, *, dedupe: bool = True) -> list[tuple[str, int]]:
    """返回手工标注 case 的 gold 切片列表 `[(doc_title, chunk_index), ...]`。

    `dedupe=True` 时按 (title, index) 去重 —— 多个 topic 可能指向同一片。

    ⚠️ 只覆盖 10 份手工标注的夹具。评测请用 `gold_for_topics(derive_topics_from_findings(...))`，
    那样能覆盖全部 32 份。
    """
    topics = CASE_TOPICS.get(case_id) or []
    return gold_for_topics(topics) if dedupe else [
        (title, idx) for t in topics for title, idx, _why in TOPIC_CHUNKS.get(t, [])
    ]


def explain_case(case_id: str) -> str:
    """把该 case 的 gold 推导过程写成可读文本（人工复核用）。"""
    lines = [f"=== {case_id}"]
    for topic in CASE_TOPICS.get(case_id) or []:
        chunks = TOPIC_CHUNKS.get(topic) or []
        lines.append(f"  结论：{topic}")
        if not chunks:
            lines.append("    （知识库中无对应依据 → gold 留空，不凑数）")
        for title, idx, why in chunks:
            lines.append(f"    ← {title} #{idx}")
            lines.append(f"       理由：{why}")
    return "\n".join(lines)


__all__ = [
    "TOPIC_CHUNKS",
    "CASE_TOPICS",
    "gold_for_case",
    "gold_for_topics",
    "derive_topics_from_findings",
    "explain_case",
]


def _main() -> int:
    """`python -m scripts.gold_v2`：打印每个 case 的 gold 推导过程，供人工复核。

    这是"标注可审计"的落地方式 —— 不打开代码也能看清：
    哪条结论、引了哪片、为什么引它。
    """
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    for case_id in CASE_TOPICS:
        print(explain_case(case_id))
        print(f"  → gold 切片数：{len(gold_for_case(case_id))}")
        print()
    print(f"共 {len(CASE_TOPICS)} 个 case，{len(TOPIC_CHUNKS)} 个 topic 映射。")
    empty = [t for t, chunks in TOPIC_CHUNKS.items() if not chunks]
    if empty:
        print(f"知识库无依据、显式留空的 topic：{', '.join(empty)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
