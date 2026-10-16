"""生成合成简历数据，用于验证「同岗位对标」功能（方案 v2 §2）。

    python -m scripts.make_sample_data --count 40 --side 3

为什么要它：`role_stats` 有 k≥10 闸门，而真实 HR 数据还没积累 —— 库里只有 verify
造的几条任务，聚合出来每格都会低于闸门、什么都看不到。这个脚本造一批**质量有梯度**
的合成简历（好的有量化数字、差的空泛），跑完整条 HR 初筛流程，让聚合有东西可算。

- `--count`：主岗位族（后端）的简历数，默认 40（高于闸门）
- `--side`：副岗位族（算法）的简历数，默认 3（**故意低于闸门**，用来验证"不出数"）

⚠️ 它写的是真实业务表（users/resumes/jds/batches），只用于开发与演示；
生产库不要跑。清理：删掉 device_id 以 `sample-` 开头的用户即可级联删除。
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db, session_scope  # noqa: E402
from app.models import HrBatch, HrBatchItem, JobDescription, Resume, User  # noqa: E402
from app.services.parse_rules import parse_jd_text, parse_resume_text  # noqa: E402
from app.services.seed import seed_if_empty  # noqa: E402

BACKEND_JD = (
    "岗位职责：负责核心交易链路的后端研发，参与高并发场景下的系统设计与性能优化。"
    "任职要求：精通 Java / Spring Boot，熟悉 MySQL 索引与慢查询优化、Redis 缓存设计；"
    "有高并发系统经验，了解 Kubernetes 与微服务治理；具备良好的工程素养与量化意识。"
)
ALGO_JD = (
    "岗位职责：负责推荐系统与排序模型的研发。"
    "任职要求：熟悉 Python、机器学习与深度学习，有推荐系统或 NLP 项目经验，"
    "掌握特征工程与模型调优，了解大数据处理框架。"
)

COMPANIES = ["某电商平台", "某出行公司", "某金融科技", "某社交产品", "某 SaaS 服务商"]
VERBS_GOOD = ["重构", "优化", "设计", "主导", "落地", "治理", "拆分", "压测"]
VERBS_POOR = ["负责", "参与", "协助", "支持"]
TECHS = ["Java", "Spring Boot", "MySQL", "Redis", "Kafka", "Kubernetes", "微服务"]


def make_resume_text(quality: str) -> str:
    """按质量档位造简历正文。

    - good：每段都有量化数字、句式短、动词多样（"高分简历"的样子）
    - mid：部分量化、句式偏长
    - poor：无量化、动词单一（"低分简历"的样子）
    """
    rnd = random.Random()
    company = rnd.choice(COMPANIES)
    lines = ["张三", "后端工程师", f"2021-2023 {company}"]
    for i in range(rnd.randint(2, 3)):
        tech = rnd.choice(TECHS)
        if quality == "good":
            verb = rnd.choice(VERBS_GOOD)
            lines.append(f"{verb}{tech}相关链路，接口 P99 从 {rnd.randint(600, 1200)}ms 降到 {rnd.randint(80, 200)}ms")
        elif quality == "mid":
            lines.append(
                f"负责使用 {tech} 完成了系统的一部分功能开发与维护工作，"
                f"期间参与了若干性能方面的改进，效果比较明显"
            )
        else:
            lines.append(f"参与使用 {tech} 进行日常开发工作，配合团队完成相关需求")
    lines.append("技能：" + "、".join(rnd.sample(TECHS, k=3)))
    return "\n".join(lines)


def _parsed_of(quality: str, text: str) -> dict:
    """直接构造 ParsedResume 结构，**不走规则解析**。

    为什么：规则解析对合成文本的章节识别不稳定 —— 实测造出来的简历 work/highlights
    全为空，于是聚合里"要点均长 / 每段量化 / 动词多样性"三项全是 None（白造）。
    合成数据的目的是造出**有质量梯度**的结构化简历，直接给结构更可控。
    """
    import re

    lines = [
        ln.strip() for ln in text.splitlines()
        if ln.strip() and not ln.startswith(("张三", "后端工程师", "2021", "技能："))
    ]
    skill_part = text.split("技能：")[-1] if "技能：" in text else ""
    skills = [s for s in re.split(r"[、,，;；/\s]+", skill_part) if s]
    return {
        "basics": {"name": "张三"},
        "summary": "",
        "work": (
            [{"company": "某互联网公司", "title": "后端工程师", "start": "2021-01", "end": "2023-06",
              "highlights": lines[:3]}]
            if lines else []
        ),
        "projects": [],
        "education": [],
        "skills": skills,
    }


def _prepare(family: str, jd_text: str, count: int, device: str) -> str:
    with session_scope() as db:
        user = User(device_id=device, role="hr", free_quota_used=0)
        db.add(user)
        db.flush()
        jd = JobDescription(
            user_id=user.id, title=f"{family}工程师", raw_text=jd_text,
            parsed_json=parse_jd_text(jd_text).model_dump(),
        )
        db.add(jd)
        db.flush()
        batch = HrBatch(user_id=user.id, jd_id=jd.id, status="pending", total=count, finished=0)
        db.add(batch)
        db.flush()
        for i in range(count):
            # 质量梯度：约 1/3 好、1/3 中、1/3 差 —— 这样 p75 与各项指标才有区分度
            quality = ("good", "mid", "poor")[i % 3]
            text = make_resume_text(quality)
            resume = Resume(
                user_id=user.id, title=f"{family}-{i + 1:03d}（{quality}）", raw_text=text,
                parsed_json=_parsed_of(quality, text),
                mime_type="text/plain", size_bytes=len(text.encode("utf-8")),
            )
            db.add(resume)
            db.flush()
            db.add(HrBatchItem(batch_id=batch.id, resume_id=resume.id, status="pending"))
        db.flush()
        return batch.id


def main() -> int:
    parser = argparse.ArgumentParser(description="生成合成简历并跑完 HR 初筛（用于验证对标功能）")
    parser.add_argument("--count", type=int, default=40, help="主岗位族（后端）简历数")
    parser.add_argument("--side", type=int, default=3, help="副岗位族（算法）简历数，故意低于闸门 k=10")
    args = parser.parse_args()

    init_db()
    with session_scope() as db:
        seed_if_empty(db)

    from app.api.hr import _process_batch

    for family, jd_text, count, device in (
        ("后端", BACKEND_JD, args.count, "sample-backend"),
        ("算法", ALGO_JD, args.side, "sample-algo"),
    ):
        batch_id = _prepare(family, jd_text, count, device)
        asyncio.run(_process_batch(batch_id))
        print(f"[OK] {family}：{count} 份已跑完（batch={batch_id[:8]}）")

    print("\n下一步：python -m scripts.aggregate_stats --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
