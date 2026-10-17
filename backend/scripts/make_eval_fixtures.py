"""P-1：生成「语义判定 / 分层加权」评测夹具。

为什么不用人工标注：
- 现有真实数据去重后只有 9 份唯一文本，且多是测试夹具（`重建后冒烟` / `空窗期已说明`），
  不足以做 30 份标注；
- 更关键的是：**标注集的价值在于 ground truth 的可控性**。本脚本让每份简历的
  「哪些字面缺失是真差距、哪些是表述差异」在**生成时就已知**，因此 recall/precision
  可以自动计算，不需要人工逐行判定。

夹具设计（`KIND` → 期望的假差距集合）：
1. `equiv_<kw>`    ：用**不同的词**表达 JD 要求的能力（例如用"多服务调用链路治理"表达"分布式"）
                     → 期望该关键词被判为 **false_gap**，且证据必须能在原文定位；
2. `real_gap_<kw>` ：简历**确实没有**该能力、也没有等价表述 → 期望判为 **real_gap**（检验不误判）；
3. `mixed`          ：同时含若干假差距与若干真差距 → 检验精度；
4. `weak`           ：内容空泛、无量化、无等价表述 → 期望几乎全为真差距（下限样本）。

输出（均为新增文件）：
    backend/eval/fixtures/resume_XX.txt      30 份简历正文
    backend/eval/fixtures/jd_backend.txt     统一使用的 JD（取自真实库导出的那份）
    backend/eval/fixtures/expected.jsonl     每份的期望标签（ground truth）

用法（在 backend 目录下）：
    python -m scripts.make_eval_fixtures
    python -m scripts.make_eval_fixtures --count 30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import analyze  # noqa: E402
from app.services.parse_rules import parse_jd_text, parse_resume_text  # noqa: E402

EVAL_DIR = BACKEND_DIR / "eval"
FIX_DIR = EVAL_DIR / "fixtures"
SOURCE_JD = EVAL_DIR / "source_jds.jsonl"

# 每个「假差距」目标词 → 一组可替换的等价表述（都不含目标词本身）
EQUIV_PHRASES: dict[str, list[list[str]]] = {
    "分布式": [
        [
            "主导订单履约链路的服务拆分，将单体应用按业务域拆为 6 个独立服务，跨服务调用全部走异步消息",
            "梳理 12 个服务的调用链路，治理循环依赖与超时配置，故障定位时间从 40 分钟缩短至 8 分钟",
        ],
        [
            "负责跨机房数据同步方案落地，采用最终一致性补偿机制处理跨服务事务",
            "梳理 12 个服务的调用链路，治理循环依赖与超时配置",
        ],
    ],
    "高并发": [
        [
            "主导大促流量保障，设计多级限流与降级预案，峰值 QPS 从 8000 提升至 45000",
            "压测核心链路并优化连接池与线程模型，P99 延迟从 480ms 降至 120ms",
        ],
    ],
    "微服务": [
        [
            "参与服务体系拆分，按业务域边界将单体应用拆为 6 个可独立部署的服务",
            "负责服务间契约管理与版本兼容治理，接口变更采用灰度发布",
        ],
    ],
    "性能优化": [
        [
            "优化慢查询 30 余条，数据库高峰期 CPU 使用率从 85% 降至 50%",
            "重构库存扣减接口，引入本地缓存与批量写入，接口 P99 从 320ms 降至 90ms",
        ],
    ],
    "架构设计": [
        [
            "主导库存扣减方案重构，设计多级缓存与预扣减模型并推动落地",
            "独立完成订单中心领域建模，输出模块划分与依赖治理方案",
        ],
    ],
    "稳定性": [
        [
            "制定大促限流与降级预案，2023 年双十一核心链路可用性 99.98%",
            "建立链路追踪与告警体系，线上故障平均恢复时间从 40 分钟降至 8 分钟",
        ],
    ],
    "容器化": [
        [
            "基于 Docker 与 Kubernetes 完成服务容器化改造，交付流水线从 2 小时缩短至 15 分钟",
            "维护 Helm Chart 与多环境部署配置，实现一键回滚",
        ],
    ],
    "SQL": [
        [
            "编写复杂查询与存储过程支撑报表需求，通过索引与执行计划优化将报表耗时从 12s 降至 1.2s",
        ],
    ],
}

# 真差距专用描述：与目标能力无关的内容
NEUTRAL_WORK = [
    "负责内部管理后台的功能开发与维护，按需求文档完成页面与接口实现",
    "参与公司官网改版，完成 5 个页面的前端联调与上线支持",
    "协助整理测试用例并跟进线上问题单，累计处理 60 余条工单",
]


def _resume_equiv(base: dict, kw: str, variant: int) -> str:
    phrases = EQUIV_PHRASES[kw]
    picked = phrases[variant % len(phrases)]
    lines = [
        base["name"],
        f"{base['role']} | {base['city']} | {base['phone']} | {base['email']}",
        "",
        "工作经历",
        f"{base['company']} | {base['title']}    {base['period']}",
    ]
    lines += [f"- {p}" for p in picked]
    lines += [
        "",
        "专业技能",
        base["skills"],
        "",
        "教育经历",
        base["education"],
    ]
    return "\n".join(lines)


def _resume_real_gap(base: dict) -> str:
    lines = [
        base["name"],
        f"{base['role']} | {base['city']} | {base['phone']} | {base['email']}",
        "",
        "工作经历",
        f"{base['company']} | {base['title']}    {base['period']}",
    ]
    lines += [f"- {p}" for p in NEUTRAL_WORK]
    lines += [
        "",
        "专业技能",
        "Java、Spring Boot、MySQL、Git、Linux",     # 刻意缺少 JD 的核心与加分项
        "",
        "教育经历",
        base["education"],
    ]
    return "\n".join(lines)


def _resume_mixed(base: dict, equiv_kws: list[str], variant: int) -> str:
    lines = [
        base["name"],
        f"{base['role']} | {base['city']} | {base['phone']} | {base['email']}",
        "",
        "工作经历",
        f"{base['company']} | {base['title']}    {base['period']}",
    ]
    for i, kw in enumerate(equiv_kws):
        picked = EQUIV_PHRASES[kw][(variant + i) % len(EQUIV_PHRASES[kw])]
        lines += [f"- {p}" for p in picked[:1]]
    lines += [f"- {p}" for p in NEUTRAL_WORK[:1]]
    lines += [
        "",
        "专业技能",
        "Java、Spring Boot、MySQL、Redis",          # 缺 Go / Kafka / Kubernetes / 开源
        "",
        "教育经历",
        base["education"],
    ]
    return "\n".join(lines)


def _resume_weak(base: dict) -> str:
    return "\n".join(
        [
            base["name"],
            f"{base['role']} | {base['city']}",
            "",
            "工作经历",
            f"{base['company']} | {base['title']}    {base['period']}",
            "- 负责公司内部系统的日常开发与维护",
            "- 参与部分需求的评审工作",
            "",
            "专业技能",
            "Java",
            "",
            "教育经历",
            base["education"],
        ]
    )


BASES = [
    {"name": "张伟", "role": "后端开发工程师", "city": "杭州", "phone": "138-0000-0001", "email": "zhangwei@example.com",
     "company": "杭州某电商有限公司", "title": "后端开发工程师", "period": "2020.07 - 至今",
     "skills": "Java、Spring Boot、MySQL、Redis、Kafka", "education": "某某大学 | 计算机科学与技术 | 本科 | 2016.09 - 2020.06"},
    {"name": "李静", "role": "后端开发工程师", "city": "上海", "phone": "138-0000-0002", "email": "lijing@example.com",
     "company": "某支付科技有限公司", "title": "后端开发工程师", "period": "2019.03 - 至今",
     "skills": "Java、Spring Boot、MySQL、Redis、Kafka", "education": "某某理工大学 | 软件工程 | 本科 | 2015.09 - 2019.06"},
    {"name": "王强", "role": "后端开发工程师", "city": "北京", "phone": "138-0000-0003", "email": "wangqiang@example.com",
     "company": "某出行服务有限公司", "title": "后端开发工程师", "period": "2021.06 - 至今",
     "skills": "Java、Spring、MySQL、Redis", "education": "某某大学 | 信息管理与信息系统 | 本科 | 2017.09 - 2021.06"},
    {"name": "赵敏", "role": "后端开发工程师", "city": "深圳", "phone": "138-0000-0004", "email": "zhaomin@example.com",
     "company": "某云计算有限公司", "title": "后端开发工程师", "period": "2018.07 - 至今",
     "skills": "Java、Spring Boot、MySQL、Kafka、Docker", "education": "某某大学 | 计算机科学与技术 | 硕士 | 2015.09 - 2018.06"},
    {"name": "陈磊", "role": "后端开发工程师", "city": "成都", "phone": "138-0000-0005", "email": "chenlei@example.com",
     "company": "某物流科技有限公司", "title": "后端开发工程师", "period": "2020.01 - 至今",
     "skills": "Java、Spring Boot、MySQL、Redis", "education": "某某学院 | 软件工程 | 本科 | 2016.09 - 2020.06"},
]

# 夹具配方：(kind, 参数) —— 决定这份简历期望的假差距集合
PLAN: list[tuple[str, object]] = [
    ("equiv", "分布式"), ("equiv", "高并发"), ("equiv", "微服务"), ("equiv", "性能优化"),
    ("equiv", "架构设计"), ("equiv", "稳定性"), ("equiv", "容器化"), ("equiv", "SQL"),
    ("equiv", "分布式"), ("equiv", "高并发"), ("equiv", "微服务"), ("equiv", "性能优化"),
    ("equiv", "架构设计"), ("equiv", "稳定性"), ("equiv", "容器化"), ("equiv", "SQL"),
    ("equiv", "分布式"), ("equiv", "高并发"), ("equiv", "架构设计"), ("equiv", "性能优化"),
    ("real", None), ("real", None), ("real", None),
    ("mixed", ["分布式", "性能优化"]),
    ("mixed", ["分布式", "架构设计", "容器化"]),
    ("mixed", ["高并发", "稳定性"]),
    ("mixed", ["微服务", "分布式"]),
    ("mixed", ["性能优化", "稳定性", "容器化"]),
    ("weak", None),
    ("weak", None),
]


def build(count: int) -> tuple[int, list[dict]]:
    FIX_DIR.mkdir(parents=True, exist_ok=True)
    # JD：优先用真实库里导出的那份，保证与线上同分布
    if SOURCE_JD.exists():
        jd = json.loads(SOURCE_JD.read_text(encoding="utf-8").splitlines()[0])
        (FIX_DIR / "jd_backend.txt").write_text(jd["raw_text"], encoding="utf-8")
    else:
        raise SystemExit("缺少 eval/source_jds.jsonl，请先运行 python -m scripts.dump_samples")

    expected: list[dict] = []
    plan = PLAN[:count]
    for i, (kind, arg) in enumerate(plan):
        base = BASES[i % len(BASES)]
        rid = f"resume_{i:02d}"
        if kind == "equiv":
            kw = str(arg)
            text = _resume_equiv(base, kw, variant=i)
            designed_false = [kw]
            designed_real: list[str] = []
        elif kind == "real":
            text = _resume_real_gap(base)
            designed_false = []
            designed_real = ["Go", "Spring Cloud", "Kubernetes", "分布式", "高并发", "微服务",
                             "性能优化", "架构设计", "稳定性", "容器化", "开源"]
        elif kind == "mixed":
            kws = list(arg)  # type: ignore[arg-type]
            text = _resume_mixed(base, kws, variant=i)
            designed_false = kws
            designed_real = ["Go", "Spring Cloud", "Kubernetes", "开源"]
        else:  # weak
            text = _resume_weak(base)
            designed_false = []
            designed_real = ["Go", "Spring Cloud", "Kubernetes", "分布式", "高并发", "微服务",
                             "性能优化", "架构设计", "稳定性", "容器化", "开源", "SQL"]

        (FIX_DIR / f"{rid}.txt").write_text(text, encoding="utf-8")
        expected.append(
            {
                "record_id": rid,
                "kind": kind,
                "file": f"{rid}.txt",
                "designed_false_gaps": designed_false,     # 生成器设计意图
                "designed_real_gaps": designed_real,
                "expected_false_gaps": designed_false,     # 由下方 post-pass 收紧为"规则层确实判缺失"的交集
                "expected_real_gaps": designed_real,
                "unregistered_false_gaps": [],             # 设计为等价但规则层未判缺失（不计入 recall 分母）
                "note": "ground truth 由生成时确定；expected_* 已与规则层缺失清单对齐",
            }
        )

    # ---- post-pass：ground truth 与规则层对齐 ----
    # 为什么必须做：夹具的价值在于"规则层判了缺失 → 语义层能否识别为等价"。
    # 若设计为等价的那个词其实已被字面覆盖（例如等价表述里写了 Docker，而 Docker 含
    # 于 skills_core 的 Kubernetes 语境；或 MySQL 被 SQL 的子串匹配命中），
    # 规则层根本不会把它列进缺失清单，这时它**不该计入 recall 分母**。
    jd_raw = (FIX_DIR / "jd_backend.txt").read_text(encoding="utf-8")
    pj = parse_jd_text(jd_raw)
    for rec in expected:
        raw = (FIX_DIR / rec["file"]).read_text(encoding="utf-8")
        pr = parse_resume_text(raw)
        missing, _covered = analyze.missing_keywords(pr, pj, raw)
        missing_lower = {m.lower() for m in missing}
        eff = [k for k in rec["designed_false_gaps"] if k.lower() in missing_lower]
        unreg = [k for k in rec["designed_false_gaps"] if k.lower() not in missing_lower]
        rec["expected_false_gaps"] = eff
        rec["unregistered_false_gaps"] = unreg
        # 真差距也只看规则层确实判缺失的（否则无法被"误判"）
        rec["expected_real_gaps"] = [k for k in rec["designed_real_gaps"] if k.lower() in missing_lower]

    with (EVAL_DIR / "fixtures_expected.jsonl").open("w", encoding="utf-8") as fh:
        for rec in expected:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    return len(expected), expected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=30)
    args = parser.parse_args()

    n, expected = build(args.count)
    stats: dict[str, int] = {}
    for rec in expected:
        stats[rec["kind"]] = stats.get(rec["kind"], 0) + 1
    total_false = sum(len(r["expected_false_gaps"]) for r in expected)

    print(f"[OK] 生成 {n} 份夹具简历 → {FIX_DIR}")
    print(f"[OK] 期望标签         → {EVAL_DIR / 'fixtures_expected.jsonl'}")
    print(f"     配方分布：{stats}")
    print(f"     期望假差距总数：{total_false}（这些是 recall 的分母）")


if __name__ == "__main__":
    main()
