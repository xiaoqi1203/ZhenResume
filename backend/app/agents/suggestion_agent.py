"""建议 Agent：把所有发现项收敛成「可执行清单」，并保证条数达标。

关键职责：
1. 合并重复、剔除空话、按严重度排序，红色优先；
2. 保证至少 5 条可执行建议（aaa.md §5 验收标准），不足时由 `_supplementary_findings` 补齐；
3. 产出 action_items（下一步行动清单）。
"""

from __future__ import annotations

from ..services import analyze
from ..services.llm import LLMClient, LLMResult
from .base import Agent, AgentContext, AgentOutput
from .finding_utils import coerce_findings, merge_findings, resolve_evidence

MIN_FINDINGS = 5
MAX_FINAL = 8


class SuggestionAgent(Agent):
    name = "suggestion"
    title = "建议 Agent"

    async def run(self, ctx: AgentContext, llm: LLMClient) -> AgentOutput:
        candidates = analyze.sort_findings(ctx.findings)
        baseline = _finalize(candidates)

        def mock_factory() -> dict:
            return {
                "findings": [dict(f, evidence_quote=(f.get("evidence") or {}).get("quote", "")) for f in baseline],
                "action_items": _action_items(baseline),
            }

        user = (
            f"【岗位】{ctx.parsed_jd.title}（{ctx.parsed_jd.seniority or '年限未标注'}）\n"
            f"【已发现的全部问题（共 {len(candidates)} 条）】\n{_digest(candidates, 20)}\n\n"
            f"【岗位知识库依据】\n{ctx.knowledge_digest(limit=3)}\n\n"
            "请合并重复项、剔除空话，最多保留 8 条（红色优先），"
            "确保每条都有可直接复制的 rewritten_text（缺数据用 [补充：…] 占位），"
            "并给出 3–5 条当天可执行的 action_items。"
        )

        result: LLMResult = await llm.complete_json(
            system=ctx.prompt("suggestion_agent"), user=user, mock_factory=mock_factory, agent=self.name
        )

        llm_findings = coerce_findings(result.data.get("findings"), default_category="ats", ctx=ctx)

        if llm_findings:
            # LLM 版本为主，但保留规则层中被 LLM 漏掉的红色风险（不能因为模型疏忽而丢风险）
            red_kept = [f for f in baseline if f["severity"] == "red" and not _covered(f, llm_findings)]
            findings = merge_findings(llm_findings, red_kept)
        else:
            findings = baseline

        findings = _select_final(findings)
        findings = _ensure_minimum(findings, ctx)

        action_items = [
            str(item).strip()
            for item in (result.data.get("action_items") or [])
            if isinstance(item, str) and str(item).strip()
        ][:6]
        if len(action_items) < 3:
            action_items = _action_items(findings)

        ctx.findings = findings
        ctx.action_items = action_items

        output = {
            "findings_count": len(findings),
            "severity_breakdown": _breakdown(findings),
            "action_items": action_items,
        }
        ctx.outputs[self.name] = output

        notes = result.note
        if len(candidates) < MIN_FINDINGS:
            extra = f"原始发现仅 {len(candidates)} 条，已补充通用检查项至 {len(findings)} 条以满足验收标准。"
            notes = f"{notes} | {extra}" if notes else extra

        return AgentOutput(data=output, llm=result, status="degraded" if result.degraded else "done", notes=notes)


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _finalize(findings: list[dict]) -> list[dict]:
    """补上 evidence_quote 别名字段，便于 LLM 往返。"""
    out: list[dict] = []
    for f in findings[:MAX_FINAL]:
        item = dict(f)
        item["evidence_quote"] = (f.get("evidence") or {}).get("quote", "")
        out.append(item)
    return out


def _select_final(findings: list[dict], limit: int = MAX_FINAL) -> list[dict]:
    """按条数上限筛选，但**保证每个类别至少保留一条**。

    为什么不能只按严重度取前 N 条：关键词缺失类问题动辄 4–8 条且都是红色，
    会把"时间线空窗""技能与项目矛盾"这类只有黄色、但危害更大的风险挤出报告，
    导致风险维度得分虚高（曾经出现过弱简历风险安全度 100 分的情况）。
    先给每个类别保底一条，再按严重度补足，才能保证五个维度都被看见。
    """
    ordered = analyze.sort_findings(findings)

    picked: list[dict] = []
    picked_ids: set[int] = set()
    seen_categories: set[str] = set()

    # 1) 每个类别保底一条（ordered 已按严重度排序，取到的即该类别最严重的一条）
    for item in ordered:
        category = item.get("category", "")
        if category in seen_categories:
            continue
        seen_categories.add(category)
        picked.append(item)
        picked_ids.add(id(item))

    # 2) 剩余名额按严重度补足
    for item in ordered:
        if len(picked) >= limit:
            break
        if id(item) not in picked_ids:
            picked.append(item)
            picked_ids.add(id(item))

    return analyze.sort_findings(picked)[:limit]


def _breakdown(findings: list[dict]) -> dict:
    return {sev: len([f for f in findings if f["severity"] == sev]) for sev in ("red", "yellow", "green")}


def _covered(finding: dict, others: list[dict]) -> bool:
    basis = (finding.get("original_text") or finding.get("title") or "")[:24]
    return any(basis and basis in str(o.get("original_text") or "") + str(o.get("title") or "") for o in others)


def _action_items(findings: list[dict]) -> list[str]:
    """由发现项派生行动清单：每条都是「改哪里 + 怎么改」，并去掉重复的建议。"""
    items: list[str] = []
    seen_suggestions: set[str] = set()

    for f in findings:
        if f["severity"] not in ("red", "yellow"):
            continue

        suggestion = f["suggestion"].strip()
        dedupe_key = suggestion[:40]
        if dedupe_key in seen_suggestions:
            continue
        seen_suggestions.add(dedupe_key)

        # 定位信息优先用简历原文（用户能直接搜到）；JD 类问题用标题更清楚
        evidence = f.get("evidence") or {}
        if evidence.get("source") == "resume" and (f.get("original_text") or "").strip():
            target = _clip(f["original_text"], 26)
        else:
            target = _clip(f.get("title") or "", 32)

        items.append(f"{target} → {suggestion[:60]}")
        if len(items) >= 4:
            break

    if len(items) < 3:
        items.append("通读全文，删掉所有与目标岗位无关的经历与技能，把最近 3 年经历提到最前。")
    if len(items) < 3:
        items.append("用目标 JD 的原词替换简历中的同义表达，保持术语一致。")
    return items[:5]


def _clip(text: str, limit: int) -> str:
    """在标点或空格处截断，避免把「（2021.0」这种半截内容露给用户。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    for sep in ("（", "(", "，", ",", "、", "。", " ", "|", "｜", "—", "-"):
        idx = head.rfind(sep)
        if idx >= limit // 2:
            return head[:idx] + "…"
    return head + "…"


def _ensure_minimum(findings: list[dict], ctx: AgentContext) -> list[dict]:
    """不足 5 条时补充条目（aaa.md §5：报告含至少 5 条可执行建议）。

    补充顺序有讲究：
    1. 先补**通用检查项**（`_supplementary_findings`）—— 那些是简历确实缺失的内容；
    2. 还不够就补**正面发现项**（`_positive_findings`）。

    为什么需要第 2 步：一份各方面都达标的简历，第 1 步几乎触发不了任何条目，
    结果报告只有 2 条，达不到验收标准。但硬造 5 个"问题"就是编造问题 ——
    所以这里补的是**简历里真实存在的优点**，标记为 green，并给出「面试时怎么用好它」的
    可执行建议。这样既满足条数要求，又不欺骗用户：severity 已经说明它不是问题。
    """
    if len(findings) >= MIN_FINDINGS:
        return findings

    existing_titles = {f.get("title", "") for f in findings}
    supplements: list[dict] = []

    for generator in (_supplementary_findings, _positive_findings):
        for supp in generator(ctx):
            if supp["title"] in existing_titles:
                continue
            existing_titles.add(supp["title"])
            supplements.append(supp)
            if len(findings) + len(supplements) >= MIN_FINDINGS:
                break
        if len(findings) + len(supplements) >= MIN_FINDINGS:
            break

    return analyze.sort_findings(findings + supplements)[:MAX_FINAL]


def _positive_findings(ctx: AgentContext) -> list[dict]:
    """从简历里**实际检测到的优点**生成 green 发现项。

    每一条都必须有原文依据，并且给出下一步怎么用（面试准备 / 保持一致性 / 继续加码），
    而不是「写得不错，继续加油」这种废话。
    """
    parsed = ctx.parsed_resume
    out: list[dict] = []

    bullets = analyze.content_bullets(parsed)
    quantified = [(field, text) for field, text in bullets if analyze._is_quantified(text)]
    if quantified:
        field, text = quantified[0]
        hints = analyze.METRIC_HINTS.get(ctx.role_family, analyze.METRIC_HINTS["general"])
        out.append(
            {
                "category": "impact",
                "severity": "green",
                "title": f"已有 {len(quantified)} 条要点包含量化指标，这是最强的加分项",
                "original_text": text,
                "issue": "量化成果是简历里最难被质疑的内容，说明你已经在用结果说话。",
                "suggestion": f"面试时把这条按 STAR 展开（背景 / 任务 / 行动 / 结果），并提前确认数据口径能自圆其说；"
                f"其余要点可以向这个标准看齐，优先补：{'、'.join(hints[:2])}。",
                "rewritten_text": f"{text.strip()}（本句已达标，可直接沿用；准备面试时补充数据口径与统计周期）",
                "evidence": resolve_evidence(ctx, text, field),
            }
        )

    total_kw = len(ctx.missing_keywords) + len(ctx.covered_keywords)
    if total_kw and ctx.covered_keywords:
        coverage = len(ctx.covered_keywords) / total_kw
        if coverage >= 0.6:
            sample = "、".join(ctx.covered_keywords[:4])
            out.append(
                {
                    "category": "ats",
                    "severity": "green",
                    "title": f"关键词覆盖率达 {coverage:.0%}，ATS 初筛的命中基础较好",
                    "original_text": "",
                    "issue": f"目标 JD 的能力要求中已覆盖 {len(ctx.covered_keywords)} 项（{sample}）。",
                    "suggestion": "投递同类岗位时，把简历里的能力表述替换成对应 JD 的原词（同一能力不同说法会影响机器匹配），"
                    "并保持技能栏与项目描述的术语一致。",
                    "rewritten_text": f"保持与 JD 一致的用词：{sample}",
                    "evidence": {"source": "jd", "quote": ctx.covered_keywords[0] if ctx.covered_keywords[0] in ctx.jd_text else "", "span": None, "field": "parsed.skills_core", "unverified": ctx.covered_keywords[0] not in ctx.jd_text},
                }
            )

    projects_with_tech = [p for p in parsed.projects if p.tech]
    if projects_with_tech:
        item = projects_with_tech[0]
        out.append(
            {
                "category": "gap",
                "severity": "green",
                "title": "项目经历写明了技术栈，便于面试官追问",
                "original_text": item.name,
                "issue": f"项目「{item.name}」列出了 {'、'.join(item.tech[:5])}，技术细节可被追问，说明有真实落地经验。",
                "suggestion": "为每个项目准备三个问题的答案：为什么这么选型、遇到的难点是什么、如果重做会怎么改。",
                "rewritten_text": f"{item.name}：补充「技术选型理由 + 踩过的坑 + 量化结果」三要素，作为面试讲解底稿",
                "evidence": resolve_evidence(ctx, item.name, "projects"),
            }
        )

    if parsed.work and all(w.highlights for w in parsed.work):
        out.append(
            {
                "category": "clarity",
                "severity": "green",
                "title": "每段工作经历都有内容要点，结构完整",
                "original_text": f"{parsed.work[0].company} {parsed.work[0].title}".strip(),
                "issue": "所有工作经历都写了具体职责与成果，没有出现「只有公司职位」的空条目。",
                "suggestion": "保持这个结构；后续新增经历时沿用「强动词 + 对象 + 量化结果」的写法即可。",
                "rewritten_text": "（结构已达标，无需改写）",
                "evidence": resolve_evidence(ctx, parsed.work[0].company, "work"),
            }
        )

    certificates_and_awards = [c for c in parsed.certificates if c.strip()]
    if certificates_and_awards:
        out.append(
            {
                "category": "gap",
                "severity": "green",
                "title": "有可验证的第三方证明（证书 / 荣誉），可信度更高",
                "original_text": certificates_and_awards[0],
                "issue": f"列出了 {len(certificates_and_awards)} 项证书或荣誉，在同等经历下能有效提高邀约率。",
                "suggestion": "把含金量最高的一项放在最前，并补上取得年份与获奖比例（如「前 5%」）让含金量可判断。",
                "rewritten_text": f"{certificates_and_awards[0]}（补充取得年份与获奖比例）",
                "evidence": resolve_evidence(ctx, certificates_and_awards[0], "certificates"),
            }
        )

    if parsed.basics.city and parsed.education:
        out.append(
            {
                "category": "clarity",
                "severity": "green",
                "title": "基本信息与教育背景完整，机器与 HR 都能读到",
                "original_text": f"现居{parsed.basics.city}",
                "issue": "现居城市与教育经历都已写明，避免了异地岗位的可行性疑问与 ATS 字段为空的问题。",
                "suggestion": "若可接受异地机会，在联系方式后补一句「可接受 XX 机会」，可以扩大可投递范围。",
                "rewritten_text": f"联系方式补充：现居{parsed.basics.city}（可接受 [城市] 机会）",
                "evidence": resolve_evidence(ctx, parsed.basics.city, "basics.city"),
            }
        )

    return out


def _supplementary_findings(ctx: AgentContext) -> list[dict]:
    """通用但真实的检查项：每条都基于简历实际状态，不编造。"""
    parsed = ctx.parsed_resume
    raw = ctx.resume_text
    out: list[dict] = []

    no_highlight_work = [w for w in parsed.work if not w.highlights]
    if no_highlight_work:
        w = no_highlight_work[0]
        label = f"{w.company} {w.title}".strip()
        out.append(
            {
                "category": "clarity",
                "severity": "red",
                "title": "工作经历只有公司职位，没有内容要点",
                "original_text": label,
                "issue": f"「{label}」这段经历没有解析到任何职责或成果描述，等于占位符，无法体现你的价值。",
                "suggestion": "为每段经历补 2–4 条要点，结构为「强动词 + 做了什么 + 带来什么结果」。",
                "rewritten_text": f"{label}\n- 主导 [核心模块] 的设计与开发，[补充指标：规模或性能收益]\n- 推动 [具体改进]，使 [指标] 从 [原值] 提升至 [新值]",
                "evidence": resolve_evidence(ctx, label, "work"),
            }
        )

    if not parsed.projects:
        out.append(
            {
                "category": "gap",
                "severity": "yellow",
                "title": "缺少项目经历区块",
                "original_text": "",
                "issue": "未识别到独立项目经历。对技术岗而言，项目是证明能力的主要载体，缺失会让 HR 只能靠公司名气判断。",
                "suggestion": "增加「项目经历」区块，写清项目背景、你的角色、技术方案与最终结果。",
                "rewritten_text": "项目经历\n[项目名] ｜ [你的角色] ｜ [起止时间]\n- 背景：[要解决什么问题]\n- 方案：[你做了什么技术选型与实现]\n- 结果：[补充指标：性能/成本/效率的具体变化]",
                "evidence": {"source": "resume", "quote": "", "span": None, "field": "projects", "unverified": True},
            }
        )

    if not parsed.certificates:
        out.append(
            {
                "category": "gap",
                "severity": "green",
                "title": "可以补一个「证书 / 荣誉」区块提升可信度",
                "original_text": "",
                "issue": "未识别到证书、奖项或开源贡献。在同等经历下，这类可验证的第三方证据能有效提高面试邀约率。",
                "suggestion": "列出英语等级、职业资格、竞赛获奖、开源仓库 star 数或技术博客产出。",
                "rewritten_text": "证书 / 荣誉\n- [证书名]（[取得年份]）\n- [开源项目] ｜ GitHub [N] star ｜ [一句话说明你贡献了什么]",
                "evidence": {"source": "resume", "quote": "", "span": None, "field": "certificates", "unverified": True},
            }
        )

    if not parsed.basics.city:
        out.append(
            {
                "category": "ats",
                "severity": "green",
                "title": "未写明所在城市",
                "original_text": "",
                "issue": "缺少城市信息会让异地岗位的 HR 无法判断到岗可行性，也可能影响本地岗位的初筛。",
                "suggestion": "在联系方式旁写明现居城市；若可接受异地，注明「可接受 [城市] 机会」。",
                "rewritten_text": "联系方式：138-0000-0000 ｜ name@example.com ｜ 现居杭州（可接受上海机会）",
                "evidence": {"source": "resume", "quote": "", "span": None, "field": "basics.city", "unverified": True},
            }
        )

    if not parsed.education:
        out.append(
            {
                "category": "clarity",
                "severity": "green",
                "title": "教育经历信息不完整",
                "original_text": "",
                "issue": "教育背景缺失或无法解析，部分企业的 ATS 会要求该字段非空才允许进入下一轮。",
                "suggestion": "补齐学校、专业、学历与起止时间，应届生可补充主修课程与绩点排名。",
                "rewritten_text": "教育经历\n某大学 ｜ 计算机科学与技术 ｜ 本科 ｜ 2018.09 – 2022.06",
                "evidence": {"source": "resume", "quote": "", "span": None, "field": "education", "unverified": True},
            }
        )

    skills_lower = {s.lower() for s in parsed.skills}
    if parsed.projects and skills_lower and not any(skills_lower & {t.lower() for t in p.tech} for p in parsed.projects):
        out.append(
            {
                "category": "gap",
                "severity": "yellow",
                "title": "技能栏与项目经历没有相互印证",
                "original_text": "、".join(parsed.skills[:8]),
                "issue": "技能栏列出的技术没有在任何项目描述中出现，容易被判定为「堆砌关键词」，面试时也缺少可追问的落点。",
                "suggestion": "为技能栏中最重要的 3 项技术，各自绑定一条项目中的实际使用场景。",
                "rewritten_text": f"项目经历中补充：「使用 {parsed.skills[0]} 完成 [具体模块]，[补充指标]」",
                "evidence": resolve_evidence(ctx, parsed.skills[0] if parsed.skills else "", "skills"),
            }
        )

    if raw and len(raw) < 400:
        out.append(
            {
                "category": "clarity",
                "severity": "yellow",
                "title": "简历内容过短，信息量不足",
                "original_text": raw[:80],
                "issue": f"全文仅约 {len(raw)} 字，难以支撑对能力的判断，可能是解析失败或简历确实过于简略。",
                "suggestion": "补充每段经历的具体职责与成果，目标篇幅控制在 800–1500 字（1–2 页）。",
                "rewritten_text": "（结构性建议：为每段经历补 2–4 条要点，并各含至少 1 个量化指标）",
                "evidence": resolve_evidence(ctx, raw[:40] if raw else "", "raw_text"),
            }
        )

    return out


def _digest(findings: list[dict], limit: int = 20) -> str:
    lines = []
    for i, f in enumerate(findings[:limit], 1):
        lines.append(
            f"{i}. [{f['severity']}/{f['category']}] {f['title']}\n"
            f"   原文：{(f.get('original_text') or '')[:90]}\n"
            f"   问题：{(f.get('issue') or '')[:110]}"
        )
    return "\n".join(lines) or "（无发现项）"
