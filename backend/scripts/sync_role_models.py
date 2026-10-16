"""把 `role_stats` 的聚合量同步成知识库文档：**岗位能力模型（数据驱动版）**。

    python -m scripts.sync_role_models              # 干跑：打印将写入的文档
    python -m scripts.sync_role_models --apply      # 写入 knowledge_docs 并建向量

## 为什么做这一步（方案 v2 §2.6①）

统计范式的**第一个消费者不是用户，是知识库**。现在的「岗位能力模型」是
`services/seed.py` 里**手写的 11 篇文档**（经验值）；这一步把它换成
**真实 HR 数据算出来的聚合量**，于是诊断的检索依据直接变准 ——
而用户在报告里看到的就一句"你 68 字，同岗位 42 字"，背后是这条链路在支撑。

## 与手写文档的关系

- 手写文档：`doc_metadata.source != 'auto_stats'` —— 保留，作为冷启动兜底与经验补充
- 本文生成的：`scope='role_model'`、`doc_metadata.source='auto_stats'`、按 `(role_family, period)` 唯一
- **同一岗位族只保留最新一期**：老期数据会被替换（write-through），避免知识库里
  同时存在"2025-Q2 后端模型"和"2026-Q1 后端模型"互相打架

## 安全边界

只读 `role_stats`（已聚合到个体消失、且过 k≥10 闸门）——**不读任何简历原文**。
所以这些文档可以放心进入检索，不存在个人信息问题。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.models import KnowledgeDoc, RoleStats  # noqa: E402
from app.services import rag  # noqa: E402

SCOPE = "role_model"
SOURCE = "auto_stats"


def render_doc(stats: RoleStats) -> str:
    """把一格统计量渲染成可检索的能力模型文档。

    刻意写成"基准 + 常见缺口"两段：前者告诉求职者**写到什么程度算好**，
    后者告诉他**这个岗位最容易漏掉什么** —— 这正是 jd_match / ats 节点最需要的判断依据。
    """
    m = stats.metrics or {}
    lines = [
        f"# {stats.role_family}岗能力模型（{stats.period}）",
        "",
        f"数据来源：{stats.sample_size} 份该岗位族已完成的简历诊断聚合（个体信息已在聚合中消失）。",
        "",
        "## 高分简历的写法基准",
    ]
    if stats.score_p75 is not None:
        lines.append(f"- 高分线：综合得分 {stats.score_p75} 分以上属于该岗位族前 25%")
    if m.get("highlight_avg_chars") is not None:
        lines.append(f"- 经历要点平均 {m['highlight_avg_chars']} 字（明显长于这个值意味着啰嗦、重点被稀释）")
    if m.get("quantified_per_project") is not None:
        lines.append(f"- 每段经历平均 {m['quantified_per_project']} 个量化数字（低于此说明成果缺乏说服力）")
    if m.get("action_verb_variety") is not None:
        lines.append(f"- 动词多样性平均 {m['action_verb_variety']} 种（反复用同一个动词开头会显得单薄）")
    if m.get("jd_keyword_coverage") is not None:
        lines.append(f"- 与岗位 JD 的关键词覆盖率平均 {m['jd_keyword_coverage']}（低于此说明与岗位要求对齐不足）")
    if m.get("star_completeness") is not None:
        lines.append(f"- 经历要点的结构化比例 {m['star_completeness']}（有动作 + 有数字的占比）")

    if m.get("top_skills"):
        lines += ["", "## 该岗位族常见技能", "、".join(m["top_skills"][:20])]
    if m.get("top_missing_keywords"):
        lines += [
            "",
            "## 该岗位族最常缺失的要求（HR 最在意、求职者最常漏）",
            "、".join(m["top_missing_keywords"][:10]),
        ]

    lines += [
        "",
        "## 给诊断的建议",
        "评审该岗位族简历时，优先看：要点是否超过基准长度、每段是否有量化数字、"
        "是否覆盖了上面列出的常见要求。缺量化、缺关键词对齐是最常见的两类问题。",
    ]
    return "\n".join(lines)


def sync(db, apply: bool = False) -> tuple[int, int]:
    """返回 (将新建/更新的文档数, 被清理的老期文档数)。"""
    rows = db.execute(select(RoleStats).where(RoleStats.sample_size >= 10)).scalars().all()
    written = cleaned = 0
    # 每个岗位族只保留最新一期
    latest: dict[str, RoleStats] = {}
    for r in rows:
        cur = latest.get(r.role_family)
        if cur is None or r.period > cur.period:
            latest[r.role_family] = r

    for family, stats in sorted(latest.items()):
        content = render_doc(stats)
        title = f"{family}岗能力模型（{stats.period}）"
        existing = db.execute(
            select(KnowledgeDoc).where(KnowledgeDoc.scope == SCOPE, KnowledgeDoc.role_family == family)
        ).scalars().all()

        # 老期数据清掉（同一岗位族只留最新一期，避免知识库自相矛盾）
        for old in existing:
            meta = old.doc_metadata or {}
            if meta.get("source") == SOURCE and meta.get("period") != stats.period:
                cleaned += 1
                if apply:
                    db.delete(old)

        target = next(
            (d for d in existing if (d.doc_metadata or {}).get("source") == SOURCE
             and (d.doc_metadata or {}).get("period") == stats.period),
            None,
        )
        if target is None:
            target = KnowledgeDoc(scope=SCOPE, role_family=family)
            db.add(target)
        target.doc_type = "role_model"
        target.title = title
        target.content = content
        target.doc_metadata = {
            "source": SOURCE,
            "period": stats.period,
            "sample_size": stats.sample_size,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        written += 1
        if apply:
            db.flush()
            # 重建切片与向量（同一 doc 下先删旧切片）
            rag.index_document(db, target)
            print(f"[OK] 已写入并建向量：{title}（第 {stats.sample_size} 份样本）")
        else:
            print(f"[干跑] 将写入：{title}（{len(content)} 字，样本 {stats.sample_size}）")
    return written, cleaned


def main() -> int:
    parser = argparse.ArgumentParser(description="把 role_stats 同步成知识库的岗位能力模型")
    parser.add_argument("--apply", action="store_true", help="真写入并建向量；不加则只打印")
    args = parser.parse_args()

    init_db()
    with session_scope() as db:
        written, cleaned = sync(db, apply=args.apply)
    print("-" * 70)
    print(f"共 {written} 篇" + ("已写入并建向量" if args.apply else "（干跑，未写入）") + f"，清理老期 {cleaned} 篇")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
