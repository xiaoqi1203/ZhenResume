"""同岗位对标（方案 v2 §2.6）：把 `role_stats` 的聚合量变成求职者能用的对比。

产出就是诊断报告里的 `benchmark` 字段：

    【同岗位对标】基于同岗位 428 份高分简历（后端 · 2026-Q1）
      · 要点平均字数     你 68 字  →  高分 42 字   ⚠️ 长 62%，建议压到 40 字内
      · 每个项目量化数字  你 1.2 个 →  高分 2.1 个
      · JD 关键词覆盖     你 61%   →  高分 78%     ⚠️ 缺 Java、Spring Cloud

**为什么这里有价值**：只给分数（"匹配度 72"）用户不知道该改什么；只给示例又太主观。
数字对比 + 合成示例配对，才是可执行的建议。

**为什么它合法**：读的是 `role_stats`（已聚合到个体消失、且过 k≥10 闸门），
**不读任何人的简历原文** —— 所以它对求职者展示不需要额外授权。

同一套岗位族判定与指标口径必须与 `scripts/aggregate_stats.py` 保持一致，
所以 ROLE_FAMILY_RULES 与指标计算都放在这里，脚本反过来 import 本模块，避免两处漂移。
"""

from __future__ import annotations

import re
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import JobDescription, Resume, RoleStats

# 岗位族关键词映射（人工可改）。判定用**命中计数**，不是"先匹配到谁算谁"：
# 实测踩过 —— 后端 JD 里写了"了解 Kubernetes"，若 运维 排在前面就会被判成运维，整格数据归错岗位。
ROLE_FAMILY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("算法", ("算法", "机器学习", "深度学习", "推荐", "nlp", "cv", "模型", "数据挖掘", "大模型")),
    ("数据", ("数据分析", "数据仓库", "数仓", "bi", "etl", "指标体系")),
    ("前端", ("前端", "react", "vue", "web", "h5", "小程序", "typescript")),
    ("测试", ("测试", "qa", "自动化测试", "质量保障")),
    ("运维", ("运维", "sre", "devops", "k8s", "容器", "监控")),
    ("产品", ("产品经理", "产品设计", "需求分析", "prd")),
    ("运营", ("运营", "增长", "活动策划", "用户运营")),
    ("后端", ("后端", "服务端", "java", "golang", "spring", "微服务", "高并发", "mysql")),
]

DIGIT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|ms|s|秒|万|亿|倍|人|次|条|个|qps|tps)?", re.IGNORECASE)
MIN_SAMPLE = 10          # 与聚合脚本一致的闸门
SIMILAR_BAND = 0.10      # 相差 10% 以内算"接近"
COMPARE_KEYS = (
    "highlight_avg_chars",
    "quantified_per_project",
    "action_verb_variety",
    "jd_keyword_coverage",
)

# 指标元信息：哪个方向算好、怎么生成可执行的提示
_LABELS = {
    "highlight_avg_chars": ("要点平均字数", "less"),
    "quantified_per_project": ("每段量化数字", "more"),
    "action_verb_variety": ("动词多样性", "more"),
    "jd_keyword_coverage": ("JD 关键词覆盖", "more"),
}


def guess_role_family(jd_title: str, jd_text: str) -> str:
    """从 JD 标题+正文判定岗位族（命中计数，取最多者；平手按规则顺序）。"""
    haystack = f"{jd_title or ''}\n{(jd_text or '')[:2000]}".lower()
    best, best_hits = "其他", 0
    for family, keys in ROLE_FAMILY_RULES:
        hits = sum(1 for k in keys if k in haystack)
        if hits > best_hits:
            best, best_hits = family, hits
    return best


def highlights_of(parsed_resume: dict | None) -> list[str]:
    """把 parsed_json 里 work + projects 的 highlights 拍平。"""
    out: list[str] = []
    for section in ("work", "projects"):
        for entry in (parsed_resume or {}).get(section) or []:
            if not isinstance(entry, dict):
                continue
            for h in entry.get("highlights") or []:
                if isinstance(h, str) and h.strip():
                    out.append(h.strip())
    return out


def jd_keywords_of(parsed_jd: dict | None) -> list[str]:
    """防御式取 JD 关键词列表：字段名在不同 schema 版本下可能是这几个之一。"""
    for key in ("keywords", "must_have", "requirements", "skills", "responsibilities"):
        value = (parsed_jd or {}).get(key)
        if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            return [v.strip() for v in value if v.strip()]
    return []


def resume_metrics(parsed_resume: dict | None, parsed_jd: dict | None, raw_text: str | None) -> dict:
    """按与聚合脚本**完全相同**的口径算这份简历的指标。

    口径必须一致，否则"你 68 字 vs 高分 42 字"是拿两把尺子量出来的。
    """
    texts = highlights_of(parsed_resume)
    lens = [len(t) for t in texts]
    quantified = [len(DIGIT_RE.findall(t)) for t in texts]
    verbs = {t[:2] for t in texts}
    star_hits = sum(1 for t in texts if DIGIT_RE.findall(t) and len(t) >= 12)

    kws = jd_keywords_of(parsed_jd)
    coverage = None
    missing: list[str] = []
    if kws:
        haystack = (raw_text or "").lower()
        hit = [k for k in kws if k.lower() in haystack]
        missing = [k for k in kws if k.lower() not in haystack]
        coverage = round(len(hit) / len(kws), 2)

    return {
        "highlight_avg_chars": round(mean(lens), 1) if lens else None,
        "quantified_per_project": round(mean(quantified), 1) if quantified else None,
        "action_verb_variety": len(verbs) if verbs else None,
        "star_completeness": round(star_hits / len(texts), 2) if texts else None,
        "jd_keyword_coverage": coverage,
        "missing_keywords": missing[:8],
    }


def _verdict(key: str, mine, peer) -> str:
    """比较方向：要点长度越短越好，其余越大越好。"""
    if mine is None or peer in (None, 0):
        return "unknown"
    direction = _LABELS.get(key, ("", "more"))[1]
    better = mine < peer if direction == "less" else mine > peer
    gap = abs(mine - peer) / max(abs(peer), 1e-9)
    if gap <= SIMILAR_BAND:
        return "similar"
    return "better" if better else "worse"


def _hint(key: str, mine, peer) -> str:
    """把差距翻译成一句可执行的话（不堆数字，只给动作）。"""
    if key == "highlight_avg_chars":
        return f"要点偏长，压到 {int(peer)} 字以内"
    if key == "quantified_per_project":
        return "每段经历至少放 2 个量化数字（性能/成本/规模）"
    if key == "action_verb_variety":
        return "动词重复度偏高，换用更具体的动作词（重构/治理/落地/拆分）"
    if key == "jd_keyword_coverage":
        return "把 JD 里的关键要求补进经历或技能区"
    return ""


def build_benchmark(db: Session, resume: Resume, jd: JobDescription | None) -> dict | None:
    """产出一份简历 vs 同岗位聚合量的对比；样本不足或岗位族未知时返回 None。

    返回 `None` 时前端整块隐藏 —— 这是契约规定，也是"数据不够就别硬给结论"的产品态度。
    """
    if jd is None:
        return None
    family = guess_role_family(jd.title or "", jd.raw_text or "")
    if family == "其他":
        return None

    stats = db.execute(
        select(RoleStats)
        .where(RoleStats.role_family == family, RoleStats.sample_size >= MIN_SAMPLE)
        .order_by(RoleStats.period.desc())
        .limit(1)
    ).scalar_one_or_none()
    if stats is None or not stats.metrics:
        return None

    mine = resume_metrics(resume.parsed_json, jd.parsed_json, resume.raw_text)
    peer_metrics = stats.metrics

    rows = []
    for key in COMPARE_KEYS:
        peer = peer_metrics.get(key)
        if peer is None or mine.get(key) is None:
            continue
        label, _direction = _LABELS.get(key, (key, "more"))
        row = {
            "key": key,
            "label": label,
            "mine": mine[key],
            "peer": peer,
            "verdict": _verdict(key, mine[key], peer),
            "higher_is_better": _LABELS.get(key, ("", "more"))[1] == "more",
        }
        if row["verdict"] == "worse":
            row["hint"] = _hint(key, mine[key], peer)
        if key == "jd_keyword_coverage" and mine.get("missing_keywords"):
            row["missing"] = mine["missing_keywords"]
        rows.append(row)

    # 一项可比指标都没有就不给这一栏（避免出现空壳）
    if not rows:
        return None

    return {
        "role_family": family,
        "period": stats.period,
        "sample_size": stats.sample_size,     # 展示"基于 N 份"，既合规又是信任背书
        "score_p75": stats.score_p75,
        "metrics": rows,
        "top_skills": (peer_metrics.get("top_skills") or [])[:10],
        "top_missing_keywords": (peer_metrics.get("top_missing_keywords") or [])[:5],
        # 示例句由 §3⑤ 合成通道填充；此处留位，保证前端字段稳定
        "examples": [],
    }
