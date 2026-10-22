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
# 问题项过滤用的**宽口径**敏感词：只要问题描述里拿这些字段说事，就不该出现在报告里。
# 与 DISCRIMINATION_RE 的区别：这里包含裸词「年龄」「性别」，因为要拦住
# LLM 写出的「年龄偏大」「性别不符」这类自由表述。
_SENSITIVE_FINDING_RE = re.compile(
    r"(年龄|性别|婚育|已婚|未婚|生育|备孕|孕产|户籍|户口|身高|体重|外貌|形象气质)"
)

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


def _keyword_hit(keyword: str, haystack_lower: str) -> bool:
    """关键词是否在简历里出现（haystack 已转小写）。

    英文技术词必须按**词边界**匹配，否则会误判：
    - `Go` 会命中 `Django` / `Google`
    - `Java` 会命中 `JavaScript`
    中文词没有词边界概念，直接子串匹配。
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return False
    if kw.isascii():
        return re.search(rf"(?<![a-z0-9+#.]){re.escape(kw)}(?![a-z0-9+#.])", haystack_lower) is not None
    return kw in haystack_lower


def missing_keywords(parsed_resume: ParsedResume, parsed_jd: ParsedJd, raw_text: str = "") -> tuple[list[str], list[str]]:
    """返回 (缺失关键词, 已覆盖关键词)。大小写不敏感，中英混合。

    「A 或 B」这类择一要求单独处理（`parsed_jd.skill_groups`）：
    - 命中任意一项 → 整组算 1 项「已覆盖」；
    - 一项都不命中 → 整组算 1 项「缺失」，文案合并为「A 或 B」。
    组内成员不再逐个计数，避免"简历有 Java 却报缺少 Go"这类误报。
    """
    haystack = resume_haystack(parsed_resume, raw_text).lower()

    groups = [g for g in (parsed_jd.skill_groups or []) if len(g) >= 2]
    grouped = {kw.lower() for g in groups for kw in g}

    required = parsed_jd.all_skills()
    missing: list[str] = []
    covered: list[str] = []
    for kw in required:
        kw = (kw or "").strip()
        if not kw:
            continue
        if kw.lower() in grouped:
            continue  # 由下面的择一组统一计数
        if _keyword_hit(kw, haystack):
            covered.append(kw)
        else:
            missing.append(kw)

    for group in groups:
        label = " 或 ".join(group)
        if any(_keyword_hit(kw, haystack) for kw in group):
            covered.append(label)
        else:
            missing.append(label)

    return missing, covered


def satisfied_choice_keywords(
    parsed_resume: ParsedResume, parsed_jd: ParsedJd, raw_text: str = ""
) -> set[str]:
    """择一组里只要命中任意一项，**组内全部成员**都算已满足（小写）。

    用途：LLM 仍可能从 JD 技能清单里挑出组内未写的另一门语言（如「Go」），
    用它把这类条目过滤掉，避免"简历有 Java 却建议补 Go"。
    """
    haystack = resume_haystack(parsed_resume, raw_text).lower()
    satisfied: set[str] = set()
    for group in parsed_jd.skill_groups or []:
        if len(group) < 2:
            continue
        if any(_keyword_hit(kw, haystack) for kw in group):
            satisfied.update(kw.lower() for kw in group)
    return satisfied


def coverage_units(
    parsed_resume: ParsedResume, parsed_jd: ParsedJd, raw_text: str = ""
) -> list[tuple[list[str], list[str]]]:
    """把 JD 要求拆成「覆盖单元」，供**打分**使用。

    普通关键词 = 1 个单元（1 项要求）；
    「A/B/C 择一」= 按成员拆成多项要求，命中几个算几个。

    为什么不能只按"组是否命中"二元计分：
    JD 写「熟练掌握 Java/Go/C++ 等其中一门」而简历写了两门，如果整组算 1 项，
    覆盖率会是 100%，但实际只满足 2/3 —— 用户要求"只写了 2 个就给 2/3 的分"。

    返回 `[(members, hit_members), ...]`。
    """
    haystack = resume_haystack(parsed_resume, raw_text).lower()
    groups = [g for g in (parsed_jd.skill_groups or []) if len(g) >= 2]
    grouped = {kw.lower() for g in groups for kw in g}

    units: list[tuple[list[str], list[str]]] = []
    for kw in parsed_jd.all_skills():
        kw = (kw or "").strip()
        if not kw or kw.lower() in grouped:
            continue
        units.append(([kw], [kw] if _keyword_hit(kw, haystack) else []))
    for group in groups:
        hits = [kw for kw in group if _keyword_hit(kw, haystack)]
        units.append((list(group), hits))
    return units


def ats_findings(parsed: ParsedResume, parsed_jd: ParsedJd, raw_text: str, jd_text: str) -> list[dict]:
    out: list[dict] = []

    missing, covered = missing_keywords(parsed, parsed_jd, raw_text)
    units = coverage_units(parsed, parsed_jd, raw_text)
    total = sum(len(members) for members, _ in units)
    hits = sum(len(hit) for _, hit in units)
    ratio = (hits / total) if total else 1.0

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


# --------------------------------------------------------------------------- #
# 技能分组（只用于把"补进技能栏"的建议写成可直接粘贴的分组行，不影响任何判定）
# --------------------------------------------------------------------------- #
_TECH_GROUPS: list[tuple[str, set[str]]] = [
    ("编程语言", {"java", "python", "go", "golang", "c++", "c#", "javascript", "typescript",
                  "php", "ruby", "rust", "scala", "kotlin", "swift"}),
    ("前端框架", {"react", "vue", "angular", "next.js", "nuxt", "node.js", "express", "webpack", "vite"}),
    ("后端框架", {"django", "flask", "fastapi", "spring", "spring boot", "spring cloud", "mybatis",
                  "hibernate", "gin", "echo"}),
    ("数据存储", {"mysql", "postgresql", "oracle", "sql server", "mongodb", "redis", "elasticsearch",
                  "clickhouse", "hbase", "tidb", "sqlite", "sql"}),
    ("中间件", {"kafka", "rabbitmq", "rocketmq", "pulsar", "zookeeper", "nginx"}),
    ("工具与协作", {"docker", "kubernetes", "k8s", "helm", "istio", "jenkins", "gitlab ci", "ci/cd",
                    "devops", "terraform", "ansible", "git", "linux", "maven", "gradle"}),
]

# 「能力概念词」——它们出现在 TECH_DICT 里是为了给 JD 侧做关键词匹配，
# 但**不该**被当作"要补进技能栏的技术名"推荐给用户（写进技能栏反而会被扣分）。
_CAPABILITY_CONCEPT_WORDS = {
    "微服务", "分布式", "高并发", "高可用", "性能优化", "领域驱动设计", "DDD", "中台",
    "产品设计", "需求分析", "用户研究", "数据分析", "项目管理", "敏捷开发", "Scrum",
    "跨部门协作", "UI设计", "交互设计",
}


def _tech_group(tech: str) -> str:
    low = (tech or "").strip().lower()
    for group, members in _TECH_GROUPS:
        if low in members:
            return group
    return "其他技术"


# 行首的列表编号/项目符号：`1. ` / `（2）` / `- ` / `· `
_LINE_PREFIX_RE = re.compile(r"^\s*(?:[（(]?\d{1,2}[)）.、．]|[①-⑳]|[-–—•·※▶▪◦])\s*")


def _strip_line_prefix(line: str) -> str:
    """去掉 PDF 抽取常见的行首编号/项目符号，便于判断这一行"真正在说什么"。"""
    return _LINE_PREFIX_RE.sub("", (line or "").strip()).strip()


# 「技术栈：A、B、C；基于 X 实现 Y…」——只取冒号后到第一个句号/分号为止的那串技术名。
# 为什么必须截：PDF 把 `技术栈：` 那一条和后面几条要点挤进了同一行，
# 整行当引文会让用户看到 100 多个字符，真正相关的那 6 个技术反而被埋住。
_TECH_LIST_SPLIT_RE = re.compile(r"[；;。]")


def _tidy_quote(line: str) -> str:
    """把"技术栈"那一行裁成干净的一句引文（保留原文措辞，只截断）。

    只做截断，不改写 —— 引文必须能在原文里**逐字定位**（`locate_quote` 会校验），
    所以任何"改写"都会让证据失效。
    """
    text = _strip_line_prefix(line)
    if not re.match(r"^(技术栈|技术选型|使用工具|开发环境)\s*[:：]", text):
        return text
    first = _TECH_LIST_SPLIT_RE.split(text, maxsplit=1)[0].strip()
    # 没有分号/句号可切（PDF 抽成一行了）时，按顿号/逗号在 24 字内断句，保持"一句完整的话"
    if len(first) > 40:
        for sep in ("、", "，", ","):
            idx = first.rfind(sep, 0, 40)
            if idx >= 16:
                return first[:idx].strip()
        return first[:40].strip()
    return first


def _skills_supplement_text(techs: list[str]) -> str:
    """把缺失技术写成**可直接粘贴进技能栏**的分组行。

    为什么不能只给一句「专业技能补充：vue」（实测用户第一个吐槽点）：
    1. 大小写被丢了（`vue` / `springboot`），粘进简历反而变成新的减分项；
    2. 没写放在哪一组，用户还得自己判断，复制按钮等于没用。

    所以这里按技术归类输出，且**保留简历/词表里的原始大小写**。
    """
    grouped: dict[str, list[str]] = {}
    for tech in techs:
        grouped.setdefault(_tech_group(tech), []).append(tech)
    lines = [f"专业技能（在「{group}」分组下补一行）：{'、'.join(names)}" for group, names in grouped.items()]
    return "\n".join(lines)


def _skill_project_consistency_findings(parsed: ParsedResume, raw_text: str) -> list[dict]:
    """技能栏 ↔ 项目技术栈的双向一致性检查。

    只产出**一条**结论（多条同名结论会互相挤占报告条数），且必须满足下面全部前提，
    否则宁可不报 —— 这一条踩过的误报代价比漏报大：

    1. **技能栏必须被可靠解析**。判断标准不是"非空"，而是"抽出来的每一项都像技术名"。
       实测：`extract_skills` 对分行写法（`1. 编程语言：熟练掌握 Java，了解 Python`）
       会把整句切碎，产出 `['编程语⾔：熟练掌握', '了解', '基础语法', ...]` 这类碎片。
       这时与项目技术栈比对毫无意义（碎片永远不会等于 `Vue`），却会稳定误报
       「技能与项目对不上」。所以先在 `_skills_are_reliable()` 里判定，不可靠就整条跳过。
    2. **技能栏非空**（空技能栏由 ATS Agent 单独出一条更强的 red，不在这里重复）。
    3. **确实有项目技术未登记**（双向子串判断，`Spring Cloud` 与 `Spring` 互认）。

    引文必须是**项目里真正提到该技术的那一行**，而不是随便取第一条 highlight：
    实测 `original_text` 取了项目第 1 条要点（讲买家/卖家双角色，根本不含 `Vue`），
    于是"证据来源：简历原文"旁边挂着一句和结论无关的引文，用户一眼就看穿不可信。
    """
    if not parsed.skills or not parsed.projects:
        return []

    project_tech: list[str] = []
    for project in parsed.projects:
        for tech in project.tech:
            if tech not in project_tech:
                project_tech.append(tech)
    if not project_tech:
        return []

    if not _skills_are_reliable(parsed.skills):
        return []

    skill_set = {s.lower() for s in parsed.skills}

    def _listed(tech: str) -> bool:
        low = tech.lower()
        # 双向子串判断：既避免 "Spring Cloud" 因为技能栏写作 "Spring"/"Cloud" 而误报，
        # 也避免 "SQL" 因为技能栏只有 "MySQL" 而误报。宁可漏报也不制造假警报。
        return any(low in skill or skill in low for skill in skill_set)

    unlisted = [t for t in project_tech if not _listed(t) and len(t) >= 2]
    if not unlisted:
        return []

    # 优先报「有专名的技术」，抽象能力词（性能优化/微服务/分布式）放后面。
    #
    # 为什么：`TECH_DICT` 里混着两类词 —— `Vue`/`MyBatis`（可被追问、可写进技能栏的技术名）
    # 与 `性能优化`/`分布式`（能力描述）。后者写进「专业技能」反而会被扣分，
    # 把它们和 `Vue` 并列成「补充：Vue、性能优化」会让用户觉得建议不专业。
    # 如果一处专名技术都没有，才退回报能力词（总比不报强）。
    named = [t for t in unlisted if t not in _CAPABILITY_CONCEPT_WORDS]
    chosen = named or unlisted

    # 引文 = 项目里真正提到这些技术的那一行
    #
    # ⚠️ 这里的选法与老实现完全不同（老实现直接取 `project.highlights[0]`），原因：
    # 用户报告里那条结论挂的引文是「独立设计并开发前后端分离交易系统，支持买家/卖家双角色…」，
    # 这句话里**根本没有 Vue**。用户一眼就能看出"证据来源：简历原文"是假的。
    #
    # 三层偏好：
    #   ① `技术栈：…` 标签行 —— 最直观，一句话点明技术清单；
    #   ② 含该技术名的任意要点 —— 次优，至少能对上；
    #   ③ 找不到就**留空**并标 unverified —— 绝不用无关句子凑数。
    #
    # ⚠️ 标签行判定必须容忍行首编号：PDF 抽出来的项目要点常写成 `2. 技术栈： SpringBoot 、 Vue`，
    #    用 `^(技术栈)` 会漏掉，于是退到"含技术名的任意要点"，命中 Cursor 那行 —— 引文又错了。
    unlisted_lower = {t.lower() for t in chosen}
    quote, quote_field = "", None
    for index, project in enumerate(parsed.projects):
        highlights = [h.strip() for h in (project.highlights or []) if h.strip()]
        if not highlights:
            continue

        def _hits(line: str) -> bool:
            low = line.lower()
            return any(tech in low for tech in unlisted_lower)

        def _is_tech_label(line: str) -> bool:
            # 允许 `2. ` / `（3）` / `· ` 之类前缀，再判断是否以「技术栈：」开头
            return bool(re.match(r"^(技术栈|技术选型|使用工具|开发环境)\s*[:：]", _strip_line_prefix(line)))

        hit_line = next(
            (h for h in highlights if _is_tech_label(h) and _hits(h)),
            next((h for h in highlights if _hits(h)), ""),
        )
        if hit_line:
            quote = _tidy_quote(hit_line)
            quote_field = f"projects[{index}].highlights"
            break

    # 兜底：项目要点里找不到出处时，用"技术词本身"作证据，并明确标 unverified。
    # 绝不用项目第一条要点凑数 —— 那是"引文不支持结论"的典型。
    evidence = (
        _evidence("resume", quote, quote_field, raw_text, "")
        if quote
        else {"source": "resume", "quote": "", "span": None, "field": "projects[].tech", "unverified": True}
    )

    listed_text = "、".join(chosen[:5])
    return [
        {
            "category": "risk",
            "severity": "yellow",
            "title": "项目里用到的技术在技能栏没有列出",
            "original_text": quote,
            "issue": (
                f"项目里写了 {listed_text}，但技能栏没有登记。"
                "HR/ATS 按技能栏筛人时，这些真实用过的技术会被漏掉；"
                "而技能栏与项目对不上，也容易被当成「技能栏是随手堆的」。"
            ),
            "suggestion": "把项目里确实用过的技术补进技能栏对应分组（只补你真能讲清楚的），让两处一致。",
            "rewritten_text": _skills_supplement_text(chosen[:5]),
            "evidence": evidence,
        }
    ]


# 技能栏里"整句碎片"的特征：含冒号/括号说明、或是动词短语而不是技术名
_SKILL_FRAGMENT_RE = re.compile(
    r"[:：。，,；;]|^(熟练|熟悉|了解|掌握|擅长|精通|能够|可以|使用|基于|能|会)|"
    r"(使用|开发|实现|进行|操作|运用|掌握|熟练|了解|熟悉)$"
)


def _skills_are_reliable(skills: list[str]) -> bool:
    """技能栏是否被可靠解析成"技术名列表"（而不是整句被切碎的碎片）。

    判据：**含 2 个字符及以上、且不是碎片**的项占比 >= 60%，且至少有 2 项。
    不可靠时不报一致性结论 —— 否则会把"解析器的锅"算到用户头上。

    实测反例（用户真实简历，分行写法）：
        ['Elasticsearch', 'Python', 'Spring', '编程语⾔：熟练掌握', '了解', '基础语法', ...]
    其中 `编程语⾔：熟练掌握` / `了解` / `基础语法` 都是碎片，占比过半 → 判定不可靠。
    """
    meaningful = [s for s in skills if len(s.strip()) >= 2 and not _SKILL_FRAGMENT_RE.search(s.strip())]
    if len(meaningful) < 2:
        return False
    return len(meaningful) / max(len(skills), 1) >= 0.6


def risk_findings(parsed: ParsedResume, raw_text: str) -> list[dict]:
    out = timeline_gaps(parsed, raw_text)

    out.extend(_skill_project_consistency_findings(parsed, raw_text))

    # 合规：年龄 / 性别 / 婚育等字段与岗位能力无关，**不再作为"问题项"输出**。
    # 用户明确要求："年龄性别与岗位无关易引发合规争议，这并不是问题"。
    # 这类字段仍然会在 RiskAgent 的 sensitive_fields_* 里作为中性信息记录，
    # 但不会进入 findings（不进报告问题清单、不扣分、不影响排序）。
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

    # 说明：不再单独产出「关键词重合度偏低」这条建议。
    # 覆盖率已经体现在 ATS 维度分与逐条关键词缺失里，再写一条"重合度 38%"属于同义重复，
    # 而且给出的改写建议（对齐 JD 用词）对"服务治理 vs 微服务管理"这类近义词没有实际价值。
    # 用户反馈：这条没必要。

    return out


# --------------------------------------------------------------------------- #
# 评分
# --------------------------------------------------------------------------- #
# 语义校验通过的等价映射：[("分布式", "梳理 12 个服务的调用链路…"), ...]
# 约定：调用方只传**已经过服务端引文定位校验**的条目（见改动说明书 §6.2）。
SemanticCovered = list[tuple[str, str]]


def _tier_of(kw: str, parsed_jd: ParsedJd) -> str:
    """判断一条 JD 要求属于哪一层：core / bonus / keyword。

    择一组（`Java 或 Go`）没有单一归属：取组内成员里最高的一层
    （core > bonus > keyword），否则择一条件会被当成最弱的"概念词"而少算权重。
    """
    if " 或 " in (kw or ""):
        tiers = [_tier_of(part, parsed_jd) for part in kw.split(" 或 ") if part.strip()]
        if tiers:
            return min(tiers, key=lambda t: {"core": 0, "bonus": 1, "keyword": 2}.get(t, 2))
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
    parsed_resume: ParsedResume,
    parsed_jd: ParsedJd,
    raw_text: str = "",
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
    # 按「覆盖单元」逐项计权：择一组里的每个成员各算一项，
    # 命中 2/3 就是 2/3 的权重，而不是"整组 0 分"或"整组满分"。
    for members, hit_members in coverage_units(parsed_resume, parsed_jd, raw_text):
        hit_lower = {kw.lower() for kw in hit_members}
        for kw in members:
            weight = _tier_weight(_tier_of(kw, parsed_jd))
            if kw.lower() in hit_lower or kw.lower() in verified:
                hit_w += weight
            else:
                miss_w += weight
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
    *,
    include_llm: bool = False,
) -> dict[str, int]:
    """按 aaa.md §六.3 的权重合成总分。

    权重：ATS 30% / 影响力 25% / 清晰度 20% / 竞争力差距 15% / 风险 10%

    `semantic_covered`（V2.0 新增，默认 None = 行为与改动前逐位相同）：
    服务端**已校验过引文定位**的语义等价映射，形如 `[("分布式", "原文片段"), ...]`。
    它只做一件事——把"其实是表述差异"的关键词计入覆盖，从而修正假差距造成的系统性低估。
    纯函数约束：**不查库、不调模型**，只吃调用方传入并已校验的数据。

    `include_llm`（双分制，默认 False = 行为与改动前逐位相同）：
    - False：只认规则层发现项 → **规则分**，可回归、可横向比较（同一简历两次同分）；
    - True：连模型补充的发现项一起扣分 → **深度分**，反映"模型额外抓到了多少问题"。
    两者用**同一套公式**，唯一差别是参与扣分的条目集合 —— 这样"两个分为什么不同"
    可以用一句话解释清楚，而不是两套黑箱。

    扣分模型的两个关键取舍：
    1. **同一事实只扣一次**：比如「3 条经历没量化」与「全篇没量化」是同一个根因，
       因此每个维度最多只计入 2 条发现项的扣分（按严重度取前 2），避免重复惩罚。
    2. **分数线主要看客观比率**（关键词覆盖率、量化比例），发现项只做温和修正。
       这样分数对"报告条数"不敏感，只对简历本身的质量敏感，二次诊断对比才有意义。
    3. **规则分只认规则层发现项（`origin != "llm"`）**：live 模式下 LLM 会自行增删发现项，
       如果分数跟着变，同一份简历两次诊断就会得到不同分数 —— 实测已发生
       （risk 78 ↔ 95），"改完再测对比分数"这个卖点会直接失真。
       LLM 补充的条目照常展示在报告里，但不参与规则分扣分（只参与深度分）。
    """
    scored = list(findings) if include_llm else [f for f in findings if is_rule_origin(f)]
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
        coverage, _hit_w, _total_w = _weighted_coverage(parsed_resume, parsed_jd, raw_text, semantic_covered)
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
    *,
    include_llm: bool = False,
) -> dict[str, int]:
    """报告定稿用的评分入口：只吃规则层基线，与 LLM 无关。

    `semantic_covered`：服务端已校验过引文定位的语义等价映射（见 compute_scores）。
    默认 None → 与改动前逐位相同。

    `include_llm`：仅用于**深度分**（双分制）。规则分恒为 False，
    所以「同一份简历两次诊断分数一致」这条承诺不受影响。
    """
    return compute_scores(
        rule_baseline_findings(parsed_resume, parsed_jd, role_family, raw_text, jd_text),
        parsed_resume,
        parsed_jd,
        raw_text,
        semantic_covered=semantic_covered,
        include_llm=include_llm,
    )


# --------------------------------------------------------------------------- #
# 报告定稿：分数 + 风险等级 + 摘要，**一次算完**
# --------------------------------------------------------------------------- #
# 为什么必须一次算完（实测缺陷）：
# 原来 `report` 节点用 `compute_scores(ctx.findings, …)`（含 LLM 条目、qc 之前）算摘要，
# 而落库用 `compute_scores_deterministic(...)`（规则基线）—— **两次不同计算**。
# 结果报告页把 `overall=85` 和「综合得分 84 分」并排展示（sample_resume_2_medium，5 次连跑必现）。
#
# 现在的定稿口径：
# 1. **只有一个入口** `finalize_report()`：分数、risk_level、摘要里引用的数字都从这里出；
# 2. **分数基数仍是规则基线**（保持"同一份简历两次诊断分数一致"）；
# 3. **风险等级吸收全维度的红色** —— 「风险」不是只有 `category=="risk"` 才算风险。

# 红色条目数达到这个值，风险等级直接判 red（不看单项 risk 分）
RED_LEVEL_THRESHOLD = 3


def red_count(findings: list[dict]) -> int:
    """红色条目数（不限 category）。"""
    return len([f for f in findings if f.get("severity") == "red"])


def risk_level_from_findings(risk_score: int, findings: list[dict]) -> str:
    """契约 §5.1 的派生规则 + 「红色条目不得配绿/黄等级」的一致性约束。

    基线口径（契约）：`>=80 green`、`60-79 yellow`、`<60 red`。
    在此基础上按**全维度**红色条数收紧 —— 只看 `category=="risk"` 会让
    「2 条 impact 红 + risk 分 100 → green」出现在报告里（实测空简历就是这样）。
    """
    level = risk_level_from_score(risk_score)
    reds = red_count(findings)
    if reds >= RED_LEVEL_THRESHOLD:
        return "red"
    if reds >= 1 and level == "green":
        return "yellow"
    return level


def summary_level_text(risk_level: str) -> str:
    return {"green": "风险较低", "yellow": "存在若干需要处理的风险点", "red": "存在明显风险，建议优先修正"}[risk_level]


def finalize_report(
    *,
    parsed_resume: ParsedResume,
    parsed_jd: ParsedJd,
    role_family: str,
    raw_text: str,
    jd_text: str,
    final_findings: list[dict],
    semantic_covered: SemanticCovered | None = None,
    include_depth: bool = False,
) -> dict:
    """报告定稿唯一入口：返回 `{scores, risk_level, severity_breakdown, top_finding}`。

    - `final_findings`：**用户最终看到的**发现项（qc 之后）；
    - 分数基数取规则基线（确定性）；风险等级与红/黄计数取 `final_findings`；
    - `scores` 额外带 `red_findings`，让分数与摘要的红条数有同一个来源。

    `include_depth`（双分制，默认 False = 行为与改动前逐位相同）：
    为 True 时额外算一份 `depth_scores` —— 用**同一套公式**，但让模型补充的发现项
    也参与扣分。规则分（`scores`）仍然只认规则层，因此不受影响。

    ## 为什么要双分（而不是让 Agent 结论反哺评分）

    项目的硬承诺是「同一份简历两次诊断分数一致」，这是「改完再测、对比分数变化」
    这个核心卖点成立的前提。一旦让 Agent 的结论参与规则分，模型每次写法不同，
    分数就会漂（实测发生过 risk 78 ↔ 95），承诺直接失效。

    但用户付了 agentic 模式 4–7 倍的成本，如果分数完全不变，"深度分析"的价值
    就只剩几条依据 —— 感知不到。双分制把两件事分开：
    - **规则分**：可回归、可横向比较（"我改完简历，78 → 85"仍然成立）；
    - **深度分**：体现"模型额外抓到了几个规则没抓到的问题"。
    """
    scored = compute_scores_deterministic(
        parsed_resume, parsed_jd, role_family, raw_text, jd_text, semantic_covered=semantic_covered
    )
    ordered = sort_findings(list(final_findings or []))
    breakdown = {sev: len([f for f in ordered if f.get("severity") == sev]) for sev in ("red", "yellow", "green")}
    scores = {**scored, "red_findings": breakdown["red"]}
    top = next((f for f in ordered if f.get("severity") == "red"), None) or (ordered[0] if ordered else None)
    result = {
        "scores": scores,
        "risk_level": risk_level_from_findings(scored["risk"], ordered),
        "severity_breakdown": breakdown,
        "top_finding": top,
    }
    if include_depth:
        result["depth_scores"] = compute_depth_scores(
            final_findings=ordered,
            parsed_resume=parsed_resume,
            parsed_jd=parsed_jd,
            raw_text=raw_text,
            rule_scores=scores,
            semantic_covered=semantic_covered,
        )
    return result


def compute_depth_scores(
    *,
    final_findings: list[dict],
    parsed_resume: ParsedResume,
    parsed_jd: ParsedJd,
    raw_text: str,
    rule_scores: dict,
    semantic_covered: SemanticCovered | None = None,
) -> dict:
    """深度分：同一套公式，但**模型补充的发现项也参与扣分**。

    ## 口径必须写清楚（否则用户会问"两个分为什么不一样"）

    - 覆盖率基数（关键词覆盖、量化比例）与规则分**完全相同** —— 都是确定性算的；
    - 唯一差别：规则分只数规则层条目，深度分把 qc 之后最终清单里的**全部**条目都数进去。
    - 因此 `depth <= rule` 是结构性成立的（深度分只会更低或相等），
      差别就是"模型比规则多抓到的问题量"。

    ## 为什么必须带 `delta` 与 `extra_findings`

    只说"深度分 71"没有信息量。要能回答"这 7 分差在哪"，就必须给出：
    - `delta`：`rule.overall - depth.overall`；
    - `extra_findings`：最终清单里 `origin == "llm"` 的条目数（模型独有发现）；
    - `extra_by_category`：按维度拆，让用户看到"模型在哪个维度上更敏感"。
    """
    depth = compute_scores(
        final_findings,
        parsed_resume,
        parsed_jd,
        raw_text,
        semantic_covered=semantic_covered,
        include_llm=True,
    )
    extra = [f for f in final_findings or [] if not is_rule_origin(f)]
    extra_by_category: dict[str, int] = {}
    for f in extra:
        cat = str(f.get("category") or "unknown")
        extra_by_category[cat] = extra_by_category.get(cat, 0) + 1

    return {
        "overall": depth["overall"],
        "ats": depth["ats"],
        "impact": depth["impact"],
        "clarity": depth["clarity"],
        "match": depth["match"],
        "risk": depth["risk"],
        # 与规则分的差值（>= 0；0 表示模型没抓到规则之外的新问题）
        "delta": max(0, int(rule_scores.get("overall") or 0) - int(depth["overall"] or 0)),
        # 模型独有的发现项（规则层没有的）
        "extra_findings": len(extra),
        "extra_by_category": extra_by_category,
        # 口径说明：前端/导出直接用这段文字，避免两处各写一版解释
        "explain": (
            f"规则分 {rule_scores.get('overall')} 分按确定性规则计算，可跨次对比；"
            f"深度分 {depth['overall']} 分把模型额外发现的 {len(extra)} 个问题也计入扣分。"
            if extra
            else f"规则分与深度分一致（{depth['overall']} 分）：模型没有发现规则之外的新问题。"
        ),
    }


def compose_summary(
    *,
    scores: dict,
    risk_level: str,
    breakdown: dict,
    top_finding: dict | None,
    job_title: str = "",
) -> str:
    """确定性总结文本。

    唯一硬约束：`综合得分 {N} 分` 里的 N 必须等于 `scores["overall"]` ——
    `scripts/verify.py` 有一条断言专门守这一点（同一份报告里两个总分不许打架）。
    """
    parts = [
        f"综合得分 {scores.get('overall', 0)} 分，与岗位「{job_title or '目标岗位'}」的匹配度处于中等水平，"
        f"{summary_level_text(risk_level)}。"
    ]
    if top_finding:
        parts.append(f"最需要优先处理的是「{top_finding.get('title')}」：{str(top_finding.get('issue') or '')[:70]}")
    else:
        parts.append("未发现明显硬伤，可从量化成果与关键词对齐两方面继续打磨。")
    parts.append(
        f"共 {breakdown.get('red', 0)} 项高风险、{breakdown.get('yellow', 0)} 项待优化，"
        "按行动清单逐条修改后可再诊断一次对比分数变化。"
    )
    return "".join(parts)[:300]


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
    """red → yellow → green，同级按 category 固定顺序，保证前端展示稳定。

    顺带做一次「合规过滤」：年龄、性别、婚育、户籍等与岗位能力无关的字段，
    不允许作为问题项出现在报告里（规则层已不再产出；这里拦住 LLM 自由发挥的部分）。
    用户口径：「年龄性别与岗位无关易引发合规争议，这并不是问题」。
    """
    kept = [f for f in findings if not _is_sensitive_finding(f)]
    kept = _dedupe_keyword_findings(kept)
    category_order = {"ats": 0, "impact": 1, "risk": 2, "clarity": 3, "gap": 4}
    return sorted(
        kept,
        key=lambda f: (SEVERITY_ORDER.get(f.get("severity", "yellow"), 1), category_order.get(f.get("category", ""), 9)),
    )


def _finding_keyword(finding: dict) -> str:
    """从标题里取「关键词」（中文引号内的第一段）。"""
    match = re.search(r"「([^」]+)」", str(finding.get("title") or ""))
    return match.group(1).strip().lower() if match else ""


def _dedupe_keyword_findings(findings: list[dict]) -> list[dict]:
    """同一关键词只保留一条问题项。

    实测报告里出现过两次同一件事：
      ATS   「JD 要求的关键词『Go』在简历中缺失」
      差距   「岗位要求『Go』在简历中缺少对应证据」
    前者已经足够说明问题，后者只是换个说法，属于重复噪音。
    """
    ats_keywords = {_finding_keyword(f) for f in findings if f.get("category") == "ats"}
    ats_keywords.discard("")
    if not ats_keywords:
        return findings
    out: list[dict] = []
    for finding in findings:
        if finding.get("category") == "gap":
            kw = _finding_keyword(finding)
            if kw and any(k in kw or kw in k for k in ats_keywords):
                continue
        out.append(finding)
    return out


def _is_sensitive_finding(finding: dict) -> bool:
    """问题项是否在拿年龄/性别/婚育等与岗位无关的字段说事。"""
    text = " ".join(
        str(finding.get(k) or "")
        for k in ("title", "issue", "original_text", "suggestion")
    )
    return bool(_SENSITIVE_FINDING_RE.search(text))
