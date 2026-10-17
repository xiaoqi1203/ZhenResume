"""确定性分析引擎：产出「发现项」候选与五维评分。

为什么要有这一层（而不是全交给 LLM）：
1. **证据链必须确定**：引文定位、时间线计算、关键词覆盖率这类事，规则比模型更准也更便宜；
2. **可解释、可回归**：同一份简历两次跑出的分数必须一致，否则「二次诊断对比分数变化」就是假的；
3. **mock 模式下它就是全部**：无 API Key 时报告质量完全由这一层决定。

live 模式下，各 Agent 会把这些候选交给 LLM 做判断与润色，但不会推翻这里的硬事实。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ..schemas import ParsedJd, ParsedResume
from .evidence import build_evidence

try:  # 配置项只在分层加权时读取；导入失败不应让分析层整体不可用
    from ..config import settings  # type: ignore
except Exception:  # noqa: BLE001
    settings = None  # type: ignore[assignment]


def now_utc() -> datetime:
    """「当前时间」的唯一入口（时间线空窗、工作年限都依赖它）。

    为什么要收口：直接调 `datetime.now()` 会让同一份简历**跨天复跑得到不同分数**
    （12 月 31 日与 1 月 1 日算出的空窗/年限不同），而"两次诊断分数一致"是产品承诺。
    测试里 monkeypatch 本函数即可验证跨天一致性。
    """
    return datetime.now(timezone.utc)


# 评分只认「规则层（确定性）」发现项，见 compute_scores 的说明。
RULE_ORIGIN = "rule"
LLM_ORIGIN = "llm"


def is_rule_origin(finding: dict) -> bool:
    """没有 origin 标记的一律按规则层处理（历史数据与既有调用点向后兼容）。"""
    return finding.get("origin", RULE_ORIGIN) == RULE_ORIGIN


# --------------------------------------------------------------------------- #
# 词表与正则
# --------------------------------------------------------------------------- #
QUANT_SIGNAL_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:%|％|万|亿|千|百|倍|人|个|次|条|款|单|笔|张|台|家|项|场|名|位|套|组|版|层|轮|份|页|行|"
    r"元|秒|分钟|小时|天|周|月|年|毫秒|ms|s|QPS|qps|TPS|tps|w|W|k|K|M))"
    r"|(\d[\d,]{2,})"
)

# 「技术栈：Java、Kafka」这类标签行不是成果描述，不参与"缺量化"的判断
LABEL_LINE_RE = re.compile(r"^(技术栈|技术选型|使用工具|开发环境|相关技术|标签|项目职责|关键词)[:：]")

WEAK_VERBS = {
    "负责": "主导",
    "参与": "深度参与并独立交付",
    "协助": "独立承担",
    "帮助": "支撑",
    "做了": "落地",
    "进行": "推进",
    "跟进": "推动",
    "处理": "解决",
    "完成了": "交付",
    "配合": "协同推进",
}

STRONG_VERBS = [
    "主导", "牵头", "设计", "架构", "重构", "优化", "落地", "搭建", "从0到1", "从 0 到 1",
    "交付", "推动", "攻克", "沉淀", "制定", "建设", "提升", "降低", "缩短", "增长",
    "lead", "led", "designed", "built", "launched", "optimized", "reduced", "improved",
]

EXAGGERATION_RE = re.compile(r"(精通|极其|非常熟练|深入掌握|全面负责|完全独立)")
DISCRIMINATION_RE = re.compile(r"(性别|男性优先|女性优先|年龄限制|限男|限女|已婚|未婚|婚育|生育状况|户籍|本地户口|身高|体重|外貌|形象气质)")

METRIC_HINTS: dict[str, list[str]] = {
    "backend": ["日均调用量 / QPS", "P99 延迟前后对比", "可用性 (如 99.95%)", "支撑的业务规模（订单量/用户数）"],
    "frontend": ["页面首屏耗时优化幅度", "覆盖用户量 / PV", "组件复用带来的提效比例", "构建耗时下降"],
    "algorithm": ["模型指标提升（AUC/F1/Recall）", "线上业务收益（CTR/GMV 提升）", "训练数据规模", "推理耗时下降"],
    "data": ["支撑的报表/看板数量", "数据量级（TB/日增）", "任务时效提升", "口径治理减少的争议数"],
    "test": ["覆盖率提升", "拦截线上缺陷数", "自动化节省的人力工时", "回归耗时下降"],
    "ops": ["可用性 SLA", "故障恢复时间 (MTTR)", "资源成本下降", "自动化覆盖率"],
    "product": ["功能上线后的转化率/留存变化", "覆盖用户量", "需求交付周期缩短", "A/B 实验结论"],
    "design": ["设计规范覆盖的页面/组件数", "改版后的转化或满意度变化", "交付效率提升"],
    "marketing": ["ROI / CAC / GMV", "曝光量与转化率", "增长幅度", "活动参与人数"],
    "hr": ["招聘完成率", "到岗周期缩短", "渠道成本下降", "留存率"],
    "general": ["业务规模（数量/用户数）", "提升或降低的百分比", "耗时或成本的优化幅度", "交付频次"],
}

# 空窗期说明的关键词。用户已经解释过空窗期时，报告不应该再让他「补一句说明」——
# 让人做已经做过的事，是最伤信任的一类建议。
GAP_EXPLANATION_RE = re.compile(
    r"(备考|考研|进修|自学|学习|培训|课程|认证|考证|自由职业|接项目|外包|个人项目|创业|家庭|休整|间隔年|调整)"
)


def _find(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text or "", re.IGNORECASE))


def _evidence(source: str, quote: str, field: str | None, resume_text: str, jd_text: str) -> dict:
    return build_evidence(source=source, quote=quote, field=field, resume_text=resume_text, jd_text=jd_text)


# --------------------------------------------------------------------------- #
# ATS / 关键词覆盖
# --------------------------------------------------------------------------- #
def resume_haystack(parsed: ParsedResume, raw_text: str = "") -> str:
    parts = [raw_text or ""]
    parts.extend(parsed.skills)
    parts.extend(parsed.certificates)
    for w in parsed.work:
        parts.extend([w.company, w.title, *w.highlights])
    for p in parsed.projects:
        parts.extend([p.name, p.role, *p.highlights, *p.tech])
    for e in parsed.education:
        parts.extend([e.school, e.degree, e.major, *e.highlights])
    return "\n".join(p for p in parts if p)


def missing_keywords(parsed_resume: ParsedResume, parsed_jd: ParsedJd, raw_text: str = "") -> tuple[list[str], list[str]]:
    """返回 (缺失关键词, 已覆盖关键词)。大小写不敏感，中英混合。"""
    haystack = resume_haystack(parsed_resume, raw_text).lower()
    required = parsed_jd.all_skills()
    missing: list[str] = []
    covered: list[str] = []
    for kw in required:
        kw = (kw or "").strip()
        if not kw:
            continue
        if kw.lower() in haystack:
            covered.append(kw)
        else:
            missing.append(kw)
    return missing, covered


def ats_findings(parsed: ParsedResume, parsed_jd: ParsedJd, raw_text: str, jd_text: str) -> list[dict]:
    out: list[dict] = []

    missing, covered = missing_keywords(parsed, parsed_jd, raw_text)
    total = len(missing) + len(covered)
    ratio = (len(covered) / total) if total else 1.0

    if missing:
        # 严重度按「整体覆盖率」定，而不是给每个缺失词都标红 ——
        # 4 个缺失关键词是同一个事实，不该产出 4 条红色风险。
        base_severity = "red" if ratio < 0.5 else "yellow"
        for index, kw in enumerate(missing[:4]):
            severity = base_severity if index == 0 else ("yellow" if base_severity == "red" else "green")
            out.append(
                {
                    "category": "ats",
                    "severity": severity,
                    "title": f"JD 要求的关键词「{kw}」在简历中缺失",
                    "original_text": jd_text.strip().splitlines()[0][:120] if jd_text.strip() else "",
                    "issue": f"目标 JD 明确要求「{kw}」，但简历全文未出现该关键词，ATS 关键词匹配会直接漏掉。",
                    "suggestion": f"若确有相关经验，请在技能栏或对应项目描述中显式写出「{kw}」；若确实没有，考虑先补齐再投递。",
                    "rewritten_text": f"技能：{kw}（补充到技能栏，并在使用过它的那条经历里点名）",
                    "evidence": _evidence("jd", kw, "parsed.skills_core", raw_text, jd_text)
                    if kw in jd_text
                    else {"source": "jd", "quote": kw, "span": None, "field": "parsed.skills_core", "unverified": True},
                }
            )

    if not parsed.skills:
        out.append(
            {
                "category": "ats",
                "severity": "red",
                "title": "未识别到技能栏，ATS 无法提取技能标签",
                "original_text": "",
                "issue": "简历没有独立的技能清单，多数 ATS 依赖结构化的技能字段做初筛，缺失会显著降低命中率。",
                "suggestion": "在简历靠前位置增加「专业技能」区块，按「语言/框架/中间件/工具」分组列出。",
                "rewritten_text": "专业技能：\n- 语言：Python / Go\n- 框架：FastAPI / Django\n- 存储：MySQL / Redis\n- 工具：Docker / Kubernetes / Git",
                "evidence": {"source": "resume", "quote": "", "span": None, "field": "skills", "unverified": True},
            }
        )

    if not parsed.basics.email and not parsed.basics.phone:
        out.append(
            {
                "category": "ats",
                "severity": "yellow",
                "title": "未识别到联系方式",
                "original_text": "",
                "issue": "简历中没有解析到邮箱或手机号，招聘方无法回联，也影响 ATS 的候选人去重。",
                "suggestion": "在页首明确写出手机号与邮箱，使用纯文本而非图片。",
                "rewritten_text": "联系方式：138-0000-0000 ｜ name@example.com ｜ 杭州",
                "evidence": {"source": "resume", "quote": "", "span": None, "field": "basics", "unverified": True},
            }
        )

    if len(raw_text) > 3000:
        out.append(
            {
                "category": "ats",
                "severity": "yellow",
                "title": "简历篇幅偏长，关键信息被稀释",
                "original_text": "",
                "issue": f"全文约 {len(raw_text)} 字，超过 2 页的常见阈值，HR 平均浏览时间不足 30 秒，重点容易被淹没。",
                "suggestion": "压缩到 1–2 页：删掉与目标岗位无关的经历，把最近 3 年经历放在最前。",
                "rewritten_text": "（结构性建议，无需改写具体句子）",
                "evidence": {"source": "resume", "quote": "", "span": None, "field": "raw_text", "unverified": True},
            }
        )

    if re.search(r"[│┃|]{2,}|\+[-=]{3,}\+|={5,}", raw_text):
        out.append(
            {
                "category": "ats",
                "severity": "yellow",
                "title": "简历含表格线/分隔符，可能干扰解析",
                "original_text": "",
                "issue": "检测到表格边框或制表字符，部分 ATS 解析这类版式时会串行错位，导致字段错配。",
                "suggestion": "改用单栏纯文本版式，用换行与加粗区分层级，避免使用 Word 表格排布正文。",
                "rewritten_text": "（结构性建议，无需改写具体句子）",
                "evidence": {"source": "resume", "quote": "", "span": None, "field": "raw_text", "unverified": True},
            }
        )

    return out


# --------------------------------------------------------------------------- #
# 影响力
# --------------------------------------------------------------------------- #
def _is_quantified(text: str) -> bool:
    return bool(QUANT_SIGNAL_RE.search(text or ""))


def content_bullets(parsed: ParsedResume) -> list[tuple[str, str]]:
    """经历要点，但剔除「技术栈：Java、Kafka」这类标签行 —— 它们不是成果描述。"""
    return [(field, text) for field, text in parsed.bullets() if not LABEL_LINE_RE.match((text or "").strip())]


def _weak_verb(text: str) -> str | None:
    for weak in WEAK_VERBS:
        if text.strip().startswith(weak) or f" {weak}" in f" {text.strip()}":
            return weak
    return None


def impact_findings(parsed: ParsedResume, role_family: str, raw_text: str) -> list[dict]:
    out: list[dict] = []
    hints = METRIC_HINTS.get(role_family, METRIC_HINTS["general"])
    bullets = content_bullets(parsed)

    unquantified = [(field, text) for field, text in bullets if len(text) >= 8 and not _is_quantified(text)]
    for field, text in unquantified[:3]:
        weak = _weak_verb(text)
        strong = WEAK_VERBS.get(weak or "", "主导")
        body = text.strip().rstrip("。")
        if weak:
            body = body.replace(weak, strong, 1)
        hint = "、".join(hints[:2])
        out.append(
            {
                "category": "impact",
                "severity": "red" if weak else "yellow",
                "title": "经历只写了职责，没有量化结果",
                "original_text": text,
                "issue": "只描述做了什么，没有说明做到什么程度。HR 无法判断贡献量级，这条经历的可信度与说服力都被削弱。",
                "suggestion": f"补上规模与结果指标，优先写：{hint}。",
                "rewritten_text": f"{body}，[补充指标：{hints[0]}]",
                "evidence": _evidence("resume", text, field, raw_text, ""),
            }
        )

    quantified = [t for _, t in bullets if _is_quantified(t)]
    if bullets and not quantified:
        out.append(
            {
                "category": "impact",
                "severity": "red",
                "title": "全篇没有任何量化成果",
                "original_text": bullets[0][1] if bullets else "",
                "issue": f"共 {len(bullets)} 条经历要点，没有任何一条包含数字指标，这是简历通过率的最大杀手。",
                "suggestion": "至少为最近两段经历各补 2 条带数字的成果，数字可以是规模、增幅、耗时或成本。",
                "rewritten_text": f"{bullets[0][1].rstrip('。')}，[补充指标：{hints[0]}]" if bullets else "",
                "evidence": _evidence("resume", bullets[0][1], bullets[0][0], raw_text, "") if bullets else {},
            }
        )

    exaggeration_hits = EXAGGERATION_RE.findall(raw_text or "")
    if len(exaggeration_hits) >= 4:
        sample = "、".join(dict.fromkeys(exaggeration_hits))[:40]
        quote = next((t for _, t in bullets if EXAGGERATION_RE.search(t)), "")
        out.append(
            {
                "category": "impact",
                "severity": "yellow",
                "title": "过度使用「精通」等绝对化表述",
                "original_text": quote,
                "issue": f"全文出现 {len(exaggeration_hits)} 处绝对化措辞（{sample}）。面试官会逐条追问，答不上来反而扣分。",
                "suggestion": "按熟练度分级表述：精通 → 熟练使用 / 深入理解；并用具体成果证明掌握程度。",
                "rewritten_text": quote.replace("精通", "熟练使用") if quote else "将「精通 X」改为「熟练使用 X，并用它完成了 <具体成果>」",
                "evidence": _evidence("resume", quote, None, raw_text, "") if quote else {"source": "resume", "quote": "", "span": None, "field": None, "unverified": True},
            }
        )

    if bullets and not any(any(v.lower() in t.lower() for v in STRONG_VERBS) for _, t in bullets):
        field, text = bullets[0]
        out.append(
            {
                "category": "impact",
                "severity": "yellow",
                "title": "缺少强势动词，句子缺乏推动感",
                "original_text": text,
                "issue": "全篇没有出现「主导 / 设计 / 重构 / 优化 / 搭建」这类强动词，读起来像岗位说明书而不是个人成绩单。",
                "suggestion": "每条要点用强动词开头，明确你在其中的角色与主动权。",
                "rewritten_text": f"主导{text.strip().rstrip('。').lstrip('负责参与协助')}，[补充指标：{hints[0]}]",
                "evidence": _evidence("resume", text, field, raw_text, ""),
            }
        )

    return out


# --------------------------------------------------------------------------- #
# 清晰度
# --------------------------------------------------------------------------- #
def clarity_findings(parsed: ParsedResume, raw_text: str) -> list[dict]:
    out: list[dict] = []
    bullets = parsed.bullets()

    long_bullets = [(f, t) for f, t in bullets if len(t) > 120]
    if long_bullets:
        field, text = long_bullets[0]
        out.append(
            {
                "category": "clarity",
                "severity": "yellow",
                "title": "单条要点过长，信息密度被稀释",
                "original_text": text,
                "issue": f"该条 {len(text)} 字，超过 120 字的可读阈值，读者容易跳读。共 {len(long_bullets)} 条存在此问题。",
                "suggestion": "拆成 2–3 条短句，每条只讲一件事：做了什么 → 用了什么方法 → 拿到什么结果。",
                "rewritten_text": "；".join(
                    [s.strip() for s in re.split(r"[，,；;。]", text) if s.strip()][:3]
                )
                + "（拆分为独立条目，每条补一个结果）",
                "evidence": _evidence("resume", text, field, raw_text, ""),
            }
        )

    fragments = [(f, t) for f, t in bullets if len(t.strip()) < 8]
    if fragments:
        field, text = fragments[0]
        out.append(
            {
                "category": "clarity",
                "severity": "yellow",
                "title": "存在过短的碎片化表述",
                "original_text": text,
                "issue": f"共 {len(fragments)} 条要点不足 8 字，缺少动作与结果，无法承载有效信息。",
                "suggestion": "把碎片合并进相邻条目，或补全为「动作 + 对象 + 结果」的完整句。",
                "rewritten_text": f"{text.strip().rstrip('。')}——[补充：做了什么 + 带来什么结果]",
                "evidence": _evidence("resume", text, field, raw_text, ""),
            }
        )

    missing_dates = [w.company or w.title for w in parsed.work if not w.start or not w.end]
    if parsed.work and missing_dates:
        out.append(
            {
                "category": "clarity",
                "severity": "yellow",
                "title": "部分工作经历缺少起止时间",
                "original_text": "、".join([m for m in missing_dates if m][:3]),
                "issue": f"{len(missing_dates)} 段经历没有解析到完整的起止时间。时间缺失会直接触发 HR 对空窗期的怀疑。",
                "suggestion": "统一用「YYYY.MM – YYYY.MM」格式补齐每段经历的起止时间，至今的写「至今」。",
                "rewritten_text": "某科技有限公司 ｜ 后端开发工程师 ｜ 2022.07 – 至今",
                "evidence": {"source": "resume", "quote": next((m for m in missing_dates if m), ""), "span": None, "field": "work", "unverified": True},
            }
        )

    if not parsed.education:
        out.append(
            {
                "category": "clarity",
                "severity": "green",
                "title": "未识别到教育经历区块",
                "original_text": "",
                "issue": "未解析到教育背景。若简历确实有该区块，可能是版式导致解析失败。",
                "suggestion": "用「教育经历」作为独立小标题，学校/专业/学历/时间各占一段。",
                "rewritten_text": "教育经历\n某大学 ｜ 计算机科学与技术 ｜ 本科 ｜ 2018.09 – 2022.06",
                "evidence": {"source": "resume", "quote": "", "span": None, "field": "education", "unverified": True},
            }
        )

    return out


# --------------------------------------------------------------------------- #
# 风险（时间线、矛盾、夸大）
# --------------------------------------------------------------------------- #
def _parse_ym(token: str) -> tuple[int, int] | None:
    token = (token or "").strip()
    if not token or re.search(r"至今|现在|今|present", token, re.IGNORECASE):
        return None
    match = re.search(r"(\d{4})\s*[年./\-]?\s*(\d{1,2})?", token)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else 1
    if not 1 <= month <= 12:
        month = 1
    return year, month


def _months(ym: tuple[int, int]) -> int:
    return ym[0] * 12 + ym[1]


def _find_gap_explanation(raw_text: str, start_token: str, end_token: str) -> str | None:
    """找出简历里解释这段空窗期的那句话；找不到返回 None。

    返回原句而不只是布尔值，是为了让「已说明空窗期」这条正面条目也能附上原文证据。
    """
    if not raw_text:
        return None
    for segment in re.split(r"[\n。；;]", raw_text):
        segment = segment.strip()
        if not segment or not GAP_EXPLANATION_RE.search(segment):
            continue
        if "空窗" in segment or "间隔" in segment:
            return segment
        if any(tok and tok in segment for tok in (start_token, end_token)):
            return segment
    return None


def timeline_gaps(parsed: ParsedResume, raw_text: str = "") -> list[dict]:
    """检测时间线断层（>6 个月空窗）与时间重叠。

    已经主动说明过空窗期的，不再报「请补一句说明」，而是转成绿色正面条目 ——
    用户已经做对了，报告应该说「保持这个做法」而不是重复要求。
    """
    from datetime import datetime, timezone

    out: list[dict] = []
    now_ym = (now_utc().year, now_utc().month)

    spans: list[tuple[int, int, str, str, str]] = []
    for kind, items in (("work", parsed.work), ("education", parsed.education)):
        for item in items:
            start = _parse_ym(getattr(item, "start", ""))
            end = _parse_ym(getattr(item, "end", "")) or now_ym
            if not start:
                continue
            label = getattr(item, "company", "") or getattr(item, "school", "")
            if kind == "work":
                label = f"{label} {getattr(item, 'title', '')}".strip()
            spans.append((_months(start), _months(end), label, getattr(item, "start", ""), getattr(item, "end", "")))

    work_spans = []
    for kind, items in (("work", parsed.work),):
        for item in items:
            start = _parse_ym(getattr(item, "start", ""))
            end = _parse_ym(getattr(item, "end", "")) or now_ym
            if start:
                work_spans.append((_months(start), _months(end), f"{item.company} {item.title}".strip(), item.start, item.end))

    work_spans.sort(key=lambda s: s[0])
    for i in range(len(work_spans) - 1):
        cur_end = work_spans[i][1]
        next_start = work_spans[i + 1][0]
        gap = next_start - cur_end
        if gap >= 6:
            label = work_spans[i][2]
            prev_end_token = work_spans[i][4]
            next_start_token = work_spans[i + 1][3]

            explanation = _find_gap_explanation(raw_text, prev_end_token, next_start_token)
            if explanation:
                out.append(
                    {
                        "category": "risk",
                        "severity": "green",
                        "title": f"约 {gap} 个月的空窗期已主动说明，处理得当",
                        "original_text": explanation,
                        "issue": f"这段 {gap} 个月的空窗在简历里已有交代，HR 不会把它当成无法解释的断层。",
                        "suggestion": "保持这个做法。面试时把这段时间的具体产出讲清楚（学了什么、做出什么），比只说「在备考」更有说服力。",
                        "rewritten_text": f"{prev_end_token} – {next_start_token}：[继续保留说明，并补一个可验证的产出，如「完成 XX 课程的实战项目」]",
                        "evidence": _evidence("resume", explanation, None, raw_text, ""),
                    }
                )
                continue

            out.append(
                {
                    "category": "risk",
                    "severity": "red" if gap >= 12 else "yellow",
                    "title": f"时间线存在约 {gap} 个月空窗期",
                    "original_text": f"{label}（{work_spans[i][3]} – {prev_end_token}）",
                    "issue": f"上一段经历结束到下一段开始之间约有 {gap} 个月没有记录。HR 会直接追问空窗原因。",
                    "suggestion": "主动补上一句说明：进修、自由职业、家庭原因或项目过渡，并写清这段时间的产出。",
                    "rewritten_text": f"{prev_end_token} – {next_start_token}：[一句话说明，例如「全职备考并通过 XX 认证 / 独立承接 XX 项目」]",
                    "evidence": {"source": "resume", "quote": prev_end_token if prev_end_token in raw_text else "", "span": None, "field": None, "unverified": prev_end_token not in raw_text},
                }
            )

    for i in range(len(work_spans) - 1):
        if work_spans[i + 1][0] < work_spans[i][1]:
            overlap = work_spans[i][1] - work_spans[i + 1][0]
            if overlap >= 2:
                out.append(
                    {
                        "category": "risk",
                        "severity": "yellow",
                        "title": "两段工作经历时间重叠",
                        "original_text": f"{work_spans[i][2]} / {work_spans[i + 1][2]}",
                        "issue": f"两段经历有约 {overlap} 个月重叠，若非兼职或内部转岗，会被视为时间线矛盾。",
                        "suggestion": "确认日期是否笔误；若确为兼职/顾问/内部转岗，在对应条目后注明性质。",
                        "rewritten_text": f"{work_spans[i + 1][2]}（兼职/顾问，与上一段并行）",
                        "evidence": {"source": "resume", "quote": work_spans[i + 1][3], "span": None, "field": "work", "unverified": True},
                    }
                )
                break

    return out


def risk_findings(parsed: ParsedResume, raw_text: str) -> list[dict]:
    out = timeline_gaps(parsed, raw_text)

    # 技能与项目矛盾：项目里用了但技能栏没写的技术
    project_tech = {t.lower() for p in parsed.projects for t in p.tech}
    skill_set = {s.lower() for s in parsed.skills}

    def _listed(tech: str) -> bool:
        # 双向子串判断：既避免 "Spring Cloud" 因为技能栏写作 "Spring"/"Cloud" 而误报，
        # 也避免 "SQL" 因为技能栏只有 "MySQL" 而误报。宁可漏报也不制造假警报。
        return any(tech in skill or skill in tech for skill in skill_set)

    unlisted = [t for t in project_tech if not _listed(t) and len(t) >= 2]
    if unlisted and parsed.skills:
        quote = next((p.highlights[0] for p in parsed.projects if p.highlights and any(t.lower() in " ".join(p.tech).lower() for t in unlisted)), "")
        out.append(
            {
                "category": "risk",
                "severity": "yellow",
                "title": "项目里用到的技术在技能栏没有列出",
                "original_text": quote,
                "issue": f"检测到 {', '.join(unlisted[:4])} 出现在项目描述中，但技能栏未登记。技能与项目对不上会被认为技能栏是堆砌的。",
                "suggestion": "把项目里实际用过的关键技术补进技能栏，保持两处一致。",
                "rewritten_text": f"专业技能补充：{', '.join(unlisted[:4])}",
                "evidence": _evidence("resume", quote, None, raw_text, "") if quote else {"source": "resume", "quote": "", "span": None, "field": "skills", "unverified": True},
            }
        )

    # 公平性/合规风险：简历或 JD 中出现歧视性字段
    hit = DISCRIMINATION_RE.search(raw_text or "")
    if hit:
        out.append(
            {
                "category": "risk",
                "severity": "yellow",
                "title": f"简历中出现与岗位无关的敏感信息「{hit.group(0)}」",
                "original_text": hit.group(0),
                "issue": "婚育、户籍、年龄、身高体重等信息与岗位能力无关，容易引发合规争议，也可能带来先入为主的偏见。",
                "suggestion": "删除该类信息，把版面留给与岗位直接相关的能力与成果。",
                "rewritten_text": "（建议直接删除该字段）",
                "evidence": _evidence("resume", hit.group(0), None, raw_text, ""),
            }
        )

    return out


# --------------------------------------------------------------------------- #
# 竞争力差距（与岗位基准画像对比）
# --------------------------------------------------------------------------- #
def gap_findings(parsed_resume: ParsedResume, parsed_jd: ParsedJd, raw_text: str, jd_text: str) -> list[dict]:
    out: list[dict] = []

    if parsed_jd.experience_years is not None:
        earliest = None
        for w in parsed_resume.work:
            ym = _parse_ym(w.start)
            if ym and (earliest is None or _months(ym) < _months(earliest)):
                earliest = ym
        if earliest:
            now = now_utc()
            have_years = (now.year * 12 + now.month - _months(earliest)) / 12
            need = parsed_jd.experience_years
            if need and have_years + 0.5 < need:
                out.append(
                    {
                        "category": "gap",
                        "severity": "red" if need - have_years >= 2 else "yellow",
                        "title": f"工作年限与 JD 要求存在差距（约 {have_years:.1f} 年 vs 要求 {need} 年）",
                        "original_text": f"{parsed_resume.work[0].company} {parsed_resume.work[0].start}" if parsed_resume.work else "",
                        "issue": f"JD 要求 {need} 年经验，按简历最早工作起点估算约 {have_years:.1f} 年。机器初筛很可能直接过滤。",
                        "suggestion": "把实习、外包、个人项目一并计入并显式标注；同时用能力等价的方式弥补年限：突出与 JD 高度重合的项目成果。",
                        "rewritten_text": "在简历开头加一句定位：「3 年后端开发经验（含 1 年实习与独立项目），主导过 XX 系统」",
                        "evidence": _evidence("resume", parsed_resume.work[0].start, "work[0].start", raw_text, "") if parsed_resume.work else {},
                    }
                )

    if parsed_jd.education:
        level_order = ["高中", "大专", "专科", "本科", "硕士", "博士"]
        need_level = next((lv for lv in level_order if lv in parsed_jd.education), None)
        have_levels = [lv for e in parsed_resume.education for lv in level_order if e.degree and lv in e.degree]
        have_level = max(have_levels, key=level_order.index) if have_levels else None
        if need_level and (not have_level or level_order.index(have_level) < level_order.index(need_level)):
            out.append(
                {
                    "category": "gap",
                    "severity": "red",
                    "title": f"学历门槛不满足（简历：{have_level or '未识别'}，JD 要求：{parsed_jd.education}）",
                    "original_text": parsed_jd.education,
                    "issue": "学历是硬性过滤条件，部分 ATS 会直接拦截。",
                    "suggestion": "若为统招/专升本/在职提升，请在校名后注明学制与取得方式；同时优先投递标注「学历不限」或「能力优先」的同类岗位。",
                    "rewritten_text": f"教育经历：某大学 ｜ {parsed_jd.education.replace('及以上', '')}（统招）｜ 2018.09 – 2022.06",
                    "evidence": _evidence("jd", parsed_jd.education, "parsed.education", raw_text, jd_text),
                }
            )

    coverage_missing, coverage_hit = missing_keywords(parsed_resume, parsed_jd, raw_text)
    total = len(coverage_missing) + len(coverage_hit)
    if total and coverage_hit:
        ratio = len(coverage_hit) / total
        if ratio < 0.6:
            out.append(
                {
                    "category": "gap",
                    "severity": "yellow",
                    "title": f"与岗位要求的关键词重合度偏低（{ratio:.0%}）",
                    "original_text": "",
                    "issue": f"JD 要求的 {total} 项能力中，简历只覆盖了 {len(coverage_hit)} 项。同岗位竞争者通常会覆盖 70% 以上。",
                    "suggestion": "对照缺失清单逐项确认：确实具备的补写关键词，确实不具备的用相邻经验做迁移表述。",
                    "rewritten_text": "在技能栏与项目描述中对齐 JD 用词（同一能力用 JD 的写法表达，如「服务治理」而非「微服务管理」）",
                    "evidence": {"source": "jd", "quote": "", "span": None, "field": "parsed.skills_core", "unverified": True},
                }
            )

    return out


# --------------------------------------------------------------------------- #
# 评分
# --------------------------------------------------------------------------- #
# 语义校验通过的等价映射：[("分布式", "梳理 12 个服务的调用链路…"), ...]
# 约定：调用方只传**已经过服务端引文定位校验**的条目（见改动说明书 §6.2）。
SemanticCovered = list[tuple[str, str]]


def _tier_of(kw: str, parsed_jd: ParsedJd) -> str:
    """判断一条 JD 要求属于哪一层：core / bonus / keyword。"""
    k = (kw or "").lower()
    if any(k == s.lower() for s in parsed_jd.skills_core):
        return "core"
    if any(k == s.lower() for s in parsed_jd.skills_bonus):
        return "bonus"
    return "keyword"


def _tier_weight(tier: str) -> float:
    """分层权重（配置项）。缺失时回落 1.0。"""
    if settings is None:
        return 1.0
    return float(
        {
            "core": getattr(settings, "jd_tier_weight_core", 1.0),
            "bonus": getattr(settings, "jd_tier_weight_bonus", 0.6),
            "keyword": getattr(settings, "jd_tier_weight_keyword", 0.3),
        }.get(tier, 1.0)
    )


def _verified_keyword_set(semantic_covered: SemanticCovered | None) -> set[str]:
    """把语义等价映射转成小写关键词集合；顺带丢弃空值。"""
    return {str(kw).lower() for kw, _q in (semantic_covered or []) if str(kw or "").strip()}


def _dedupe_semantic(
    missing: list[str], covered: list[str], semantic_covered: SemanticCovered | None
) -> list[tuple[str, str]]:
    """语义等价的去重规则（与改动说明书 §4.1 一致）：
    1) 同一关键词只计一次；2) 已被字面覆盖的不重复计；3) 只接受"字面缺失"清单里的关键词。
    """
    covered_lower = {c.lower() for c in covered}
    missing_lower = {m.lower() for m in missing}
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for kw, quote in semantic_covered or []:
        kw = str(kw or "").strip()
        quote = str(quote or "").strip()
        if not kw or not quote:
            continue
        k = kw.lower()
        if k in seen or k in covered_lower or k not in missing_lower:
            continue
        seen.add(k)
        out.append((kw, quote))
    return out


def _weighted_coverage(
    missing: list[str],
    covered: list[str],
    parsed_jd: ParsedJd,
    semantic_covered: SemanticCovered | None = None,
) -> tuple[float, float, float]:
    """分层加权覆盖率：Σ(命中项权重) / Σ(全部项权重)。

    - 分层权重来自配置（core/bonus/keyword），只作用于 JD 要求侧；
    - 计入**语义校验通过**的等价项（计入命中）；
    - 分母自归一，无需额外处理；无任何项时返回原兜底 0.85。
    """
    verified = _verified_keyword_set(semantic_covered)
    hit_w = 0.0
    miss_w = 0.0
    for kw in covered:
        hit_w += _tier_weight(_tier_of(kw, parsed_jd))
    for kw in missing:
        if kw.lower() in verified:
            hit_w += _tier_weight(_tier_of(kw, parsed_jd))
        else:
            miss_w += _tier_weight(_tier_of(kw, parsed_jd))
    total_w = hit_w + miss_w
    if total_w <= 0:
        return 0.85, 0.0, 0.0
    return hit_w / total_w, hit_w, total_w


def _is_missing_keyword_finding(finding: dict) -> bool:
    """该发现项是否是"某个关键词在简历中缺失"的条目。

    判据用标题模板（`ats_findings` 生成），并做分隔符感知匹配，
    避免 `SQL` 命中 `MySQL` 这类子串误伤。
    """
    if finding.get("category") != "ats":
        return False
    title = str(finding.get("title") or "")
    return title.startswith("JD 要求的关键词") and "缺失" in title


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


def compute_scores(
    findings: list[dict],
    parsed_resume: ParsedResume,
    parsed_jd: ParsedJd,
    raw_text: str,
    semantic_covered: SemanticCovered | None = None,
) -> dict[str, int]:
    """按 aaa.md §六.3 的权重合成总分。

    权重：ATS 30% / 影响力 25% / 清晰度 20% / 竞争力差距 15% / 风险 10%

    `semantic_covered`（V2.0 新增，默认 None = 行为与改动前逐位相同）：
    服务端**已校验过引文定位**的语义等价映射，形如 `[("分布式", "原文片段"), ...]`。
    它只做一件事——把"其实是表述差异"的关键词计入覆盖，从而修正假差距造成的系统性低估。
    纯函数约束：**不查库、不调模型**，只吃调用方传入并已校验的数据。

    扣分模型的两个关键取舍：
    1. **同一事实只扣一次**：比如「3 条经历没量化」与「全篇没量化」是同一个根因，
       因此每个维度最多只计入 2 条发现项的扣分（按严重度取前 2），避免重复惩罚。
    2. **分数线主要看客观比率**（关键词覆盖率、量化比例），发现项只做温和修正。
       这样分数对"报告条数"不敏感，只对简历本身的质量敏感，二次诊断对比才有意义。
    3. **只认规则层发现项（`origin != "llm"`）**：live 模式下 LLM 会自行增删发现项，
       如果分数跟着变，同一份简历两次诊断就会得到不同分数 —— 实测已发生
       （risk 78 ↔ 95），"改完再测对比分数"这个卖点会直接失真。
       LLM 补充的条目照常展示在报告里，但不参与扣分。
    """
    scored = [f for f in findings if is_rule_origin(f)]
    missing, covered = missing_keywords(parsed_resume, parsed_jd, raw_text)

    # ---- 语义校验通过的等价映射（V2.0）----
    # 只做去重与口径判定，**公式本身一行不改**（见改动说明书 §4.1）。
    verified = _dedupe_semantic(missing, covered, semantic_covered)
    if verified:
        verified_kw = {kw.lower() for kw, _ in verified}
        # 被语义校验为等价的项，不应再以"关键词缺失"的名义扣分
        scored = [
            f for f in scored
            if not (_is_missing_keyword_finding(f) and any(kw in str(f.get("title") or "") for kw in verified_kw))
        ]

    used_v2 = str(getattr(settings, "scoring_version", "v2") or "v2").lower() == "v2"
    if used_v2:
        coverage, _hit_w, _total_w = _weighted_coverage(missing, covered, parsed_jd, semantic_covered)
    else:
        total_kw = len(missing) + len(covered)
        coverage = (len(covered) / total_kw) if total_kw else 0.85

    bullets = content_bullets(parsed_resume)
    quant_ratio = (len([t for _, t in bullets if _is_quantified(t)]) / len(bullets)) if bullets else 0.0

    ats_score = _clamp(45 + coverage * 55 - _penalty(scored, "ats", red=5, yellow=2, cap=2))
    impact_score = _clamp(40 + quant_ratio * 60 - _penalty(scored, "impact", red=5, yellow=2, cap=2))
    clarity_score = _clamp(95 - _penalty(scored, "clarity", red=6, yellow=3, cap=2))
    match_score = _clamp(45 + coverage * 55 - _penalty(scored, "gap", red=7, yellow=3, cap=2))

    # 风险维度不设上限：任何一条未处理的风险项都值得把安全度拉下来（重证据的产品定位）
    risk_score = _clamp(
        100
        - 32 * len([f for f in scored if f["category"] == "risk" and f["severity"] == "red"])
        - 22 * len([f for f in scored if f["category"] == "risk" and f["severity"] == "yellow"])
        - 5 * len([f for f in scored if f["category"] == "risk" and f["severity"] == "green"])
    )

    overall = _clamp(
        ats_score * 0.30 + impact_score * 0.25 + clarity_score * 0.20 + match_score * 0.15 + risk_score * 0.10
    )

    return {
        "overall": overall,
        "ats": ats_score,
        "impact": impact_score,
        "clarity": clarity_score,
        "match": match_score,
        "risk": risk_score,
        # 可解释性：本次由语义校验贡献的覆盖项（前端可展示"语义修正 +N"）
        "semantic_covered": [kw for kw, _ in verified],
        "semantic_covered_count": len(verified),
        "scoring_version": "v2" if used_v2 else "v1",
    }


def rule_baseline_findings(
    parsed_resume: ParsedResume,
    parsed_jd: ParsedJd,
    role_family: str,
    raw_text: str,
    jd_text: str,
) -> list[dict]:
    """规则层基线发现项 —— **纯函数**，只依赖简历/JD 文本与解析结果。

    为什么单独抽出来：**评分必须与 LLM 完全无关**。
    live 模式下模型每次会增删条目，如果分数跟着变，"同一份简历两次诊断分数一致"
    就不成立（实测：risk 78 ↔ 95、ats 62 ↔ 58）；HR 场景下更严重 ——
    排名会随模型波动，无法向 HR 解释"为什么上周 A 第一这周 B 第一"。

    所以职责切成两半：
    - **展示**：走完整流水线（规则层 + LLM 补充 + 质检 + 筛选），丰富但每次可能略有不同；
    - **评分**：只用本函数的输出，任何时候、任何模型、跨天复跑都完全一致。
    """
    out: list[dict] = []
    out += ats_findings(parsed_resume, parsed_jd, raw_text, jd_text)
    out += impact_findings(parsed_resume, role_family, raw_text)
    out += risk_findings(parsed_resume, raw_text)
    out += gap_findings(parsed_resume, parsed_jd, raw_text, jd_text)
    out += clarity_findings(parsed_resume, raw_text)
    return [dict(f, origin=RULE_ORIGIN) for f in out]


def compute_scores_deterministic(
    parsed_resume: ParsedResume,
    parsed_jd: ParsedJd,
    role_family: str,
    raw_text: str,
    jd_text: str,
    semantic_covered: SemanticCovered | None = None,
) -> dict[str, int]:
    """报告定稿用的评分入口：只吃规则层基线，与 LLM 无关。

    `semantic_covered`：服务端已校验过引文定位的语义等价映射（见 compute_scores）。
    默认 None → 与改动前逐位相同。
    """
    return compute_scores(
        rule_baseline_findings(parsed_resume, parsed_jd, role_family, raw_text, jd_text),
        parsed_resume,
        parsed_jd,
        raw_text,
        semantic_covered=semantic_covered,
    )


def _penalty(findings: list[dict], category: str, *, red: int, yellow: int, cap: int = 2) -> int:
    """按严重度取该类别前 cap 条扣分，避免同一根因被多次惩罚。"""
    weights = {"red": red, "yellow": yellow, "green": 1}
    items = sorted(
        (f for f in findings if f["category"] == category),
        key=lambda f: SEVERITY_ORDER.get(f.get("severity", "yellow"), 1),
    )
    return sum(weights.get(f.get("severity", "yellow"), yellow) for f in items[:cap])


def risk_level_from_score(risk_score: int) -> str:
    """契约 §5.1：>=80 green，60-79 yellow，<60 red。"""
    if risk_score >= 80:
        return "green"
    if risk_score >= 60:
        return "yellow"
    return "red"


SEVERITY_ORDER = {"red": 0, "yellow": 1, "green": 2}


def sort_findings(findings: list[dict]) -> list[dict]:
    """red → yellow → green，同级按 category 固定顺序，保证前端展示稳定。"""
    category_order = {"ats": 0, "impact": 1, "risk": 2, "clarity": 3, "gap": 4}
    return sorted(
        findings,
        key=lambda f: (SEVERITY_ORDER.get(f.get("severity", "yellow"), 1), category_order.get(f.get("category", ""), 9)),
    )
