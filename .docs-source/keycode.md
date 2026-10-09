# ZhenResume（简甄）关键代码解析 —— 非 Agent、非 RAG 核心

> 范围：评分引擎、规则解析器、数据契约、LLM 适配与降级链、自检脚本、渲染层、数据库层。
> 方法：逐文件通读源码，所有非显然结论给出 `文件:行号` + ≤15 行原文引用；公式常量逐字引用，不转述。
> 基线规模：`analyze.py` 704 行、`parse_rules.py` 537 行、`verify.py` 659 行、`models.py` 340 行、`schemas.py` 267 行、`llm.py` 195 行、`db.py` 72 行、`config.py` 92 行、`cost.py` 30 行、`errors.py` 105 行、`extract.py` 108 行、`report_render.py` 114 行、`resume_render.py` 575 行、`resume_templates.py` 552 行。
> 标识符保留源码原样，说明为中文。引用中的 `# …` 表示此处省略了源码行（省略处非虚构）。

## 0. 前提修正与阅读约定

**重要事实校正**：任务描述称「finding 的 dedupe/merge 在 analyze 内」。经全量核验，**`analyze.py` 中不存在任何去重/合并函数**（对该文件检索 `dedupe|merge|seen` 命中 0 处）。真实位置：

- `agents/finding_utils.py:38-40`（`_dedupe_key`）、`:43-109`（`coerce_findings`：白名单收敛 + 三条硬要求 + 单 Agent 去重 + 上限 12）、`:112-123`（`merge_findings`：跨 Agent 合并去重）
- `agents/suggestion_agent.py:98-129`（`_select_final`：类别保底配额）、`:187-216`（`_ensure_minimum`：补足 5 条）

`analyze.py` 只做三件事：**产出 finding 候选**、**计算五维分**、**`sort_findings` 排序**。第 2 节按真实代码位置写生命周期。

| 概念 | 载体 | 定义位置 |
|---|---|---|
| finding 候选 | 裸 `dict` | `analyze.*_findings()` |
| 合规 finding | `dict`（白名单 + 三要素齐备 + 已去重） | `coerce_findings` |
| 累积 findings | `AgentContext.findings` | `agents/base.py:35`，`add_findings` 直接 `extend`（:45-46） |
| 最终 findings | `ctx.findings`（suggestion + qc 后） | 被截到 ≤8、补到 ≥5、删/降级 |
| 落库 finding | `ReportFinding` 行 | `sort_order` = `sort_findings` 下标 |

---

## 1. 评分引擎（`app/services/analyze.py`）

### 1.1 维度 × 权重 × 输入 × 扣分

权重出自 `analyze.py:632-634` 的文档串，原始定义见 `aaa.md:452-458`：

```python
# analyze.py:632-634
    """按 aaa.md §六.3 的权重合成总分。

    权重：ATS 30% / 影响力 25% / 清晰度 20% / 竞争力差距 15% / 风险 10%
```

| 输出键 | 语义 | 权重 | 客观输入 | `_penalty` 参数 | 公式行号 |
|---|---|---|---|---|---|
| `ats` | ATS 关键词覆盖 | **0.30** | `coverage` | `red=5, yellow=2, cap=2` | L649 |
| `impact` | 影响力/量化 | **0.25** | `quant_ratio` | `red=5, yellow=2, cap=2` | L650 |
| `clarity` | 清晰度 | **0.20** | 无（纯扣分） | `red=6, yellow=3, cap=2` | L651 |
| `match` | 竞争力差距 | **0.15** | `coverage`（同 ATS） | `red=7, yellow=3, cap=2` | L652 |
| `risk` | 风险安全度（越高越安全） | **0.10** | 无（按 risk 类计数） | 不设 cap | L655-660 |
| `overall` | 综合分 | — | 五维加权和 | — | L662-664 |

**口径不一致（源码事实）**：无 JD 关键词时，`compute_scores` 的 coverage 取 **0.85**，Agent 侧同名口径取 **0.0**。

```python
# analyze.py:642-644
    missing, covered = missing_keywords(parsed_resume, parsed_jd, raw_text)
    total_kw = len(missing) + len(covered)
    coverage = (len(covered) / total_kw) if total_kw else 0.85
```

```python
# agents/ats_agent.py:26-27
        total = len(missing) + len(covered)
        coverage = (len(covered) / total) if total else 0.0
```

后果：零关键词边界下分数按 85% 给分（`45 + 0.85*55 = 91.75 → 92`），而 ATS Agent 的 summary 文案显示 0% 覆盖 —— 报告文案与分数不一致。

### 1.2 公式（verbatim，分两段引用）

```python
# analyze.py:646-652
    bullets = content_bullets(parsed_resume)
    quant_ratio = (len([t for _, t in bullets if _is_quantified(t)]) / len(bullets)) if bullets else 0.0

    ats_score = _clamp(45 + coverage * 55 - _penalty(findings, "ats", red=5, yellow=2, cap=2))
    impact_score = _clamp(40 + quant_ratio * 60 - _penalty(findings, "impact", red=5, yellow=2, cap=2))
    clarity_score = _clamp(95 - _penalty(findings, "clarity", red=6, yellow=3, cap=2))
    match_score = _clamp(45 + coverage * 55 - _penalty(findings, "gap", red=7, yellow=3, cap=2))
```

```python
# analyze.py:654-664
    # 风险维度不设上限：任何一条未处理的风险项都值得把安全度拉下来（重证据的产品定位）
    risk_score = _clamp(
        100
        - 32 * len([f for f in findings if f["category"] == "risk" and f["severity"] == "red"])
        - 22 * len([f for f in findings if f["category"] == "risk" and f["severity"] == "yellow"])
        - 5 * len([f for f in findings if f["category"] == "risk" and f["severity"] == "green"])
    )

    overall = _clamp(
        ats_score * 0.30 + impact_score * 0.25 + clarity_score * 0.20 + match_score * 0.15 + risk_score * 0.10
    )
```

```python
# analyze.py:666-672（返回字典，末行省略闭括号）
    return {
        "overall": overall,
        "ats": ats_score,
        "impact": impact_score,
        "clarity": clarity_score,
        "match": match_score,
        "risk": risk_score,
```

归一化/钳制（L622-623）：

```python
# analyze.py:622-623
def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))
```

三条实现约束：
1. `_clamp` = `round()` → `int` → 夹到 `[0,100]`。
2. `overall` 用**已 clamp 的整数**加权（L662-664），与「先加权后 clamp」在边界上可能差 1 分。
3. 分数只依赖**两个客观比率 + findings 的 (category, severity) 计数**；标题与条数细节不影响分数（受 cap 保护）。

### 1.3 `_penalty`：同一根因只扣一次（verbatim）

```python
# analyze.py:676-683
def _penalty(findings: list[dict], category: str, *, red: int, yellow: int, cap: int = 2) -> int:
    """按严重度取该类别前 cap 条扣分，避免同一根因被多次惩罚。"""
    weights = {"red": red, "yellow": yellow, "green": 1}
    items = sorted(
        (f for f in findings if f["category"] == category),
        key=lambda f: SEVERITY_ORDER.get(f.get("severity", "yellow"), 1),
    )
    return sum(weights.get(f.get("severity", "yellow"), yellow) for f in items[:cap])
```

- `SEVERITY_ORDER = {"red": 0, "yellow": 1, "green": 2}`（L695）；排序后取最严重的前 `cap=2` 条。
- `green` 固定扣 1 分（硬编码，不随 red/yellow 参数变化）。
- 扣分上限：ats/impact ≤10，clarity ≤12，match ≤14。
- **直接下标 `f["category"]`（L680）**：入参必须已含 category 键，否则 `KeyError`。

### 1.4 风险分与 `risk_level` 映射

```python
# analyze.py:686-692
def risk_level_from_score(risk_score: int) -> str:
    """契约 §5.1：>=80 green，60-79 yellow，<60 red。"""
    if risk_score >= 80:
        return "green"
    if risk_score >= 60:
        return "yellow"
    return "red"
```

映射：`[80,100]→green`、`[60,79]→yellow`、`[0,59]→red`。派生点：`report_agent.py:23`、`engine.py:169`（落库 `AnalysisReport.risk_level`）；`verify.py:174-175` 独立复算做断言。

**反直觉组合（源码事实）**：`risk_score` 不设 cap，且 `green` 风险项也扣 5 分（L659）。而「已主动说明空窗期」产的正是 `severity: "green"` 的 risk 类条目（L452-463），因此**用户做对了反而扣 5 分**，代码中未见豁免逻辑。

### 1.5 关键词覆盖（ATS/match 共同输入）

```python
# analyze.py:84-94
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
```

匹配逻辑（L97-111）：要求集合 = `ParsedJd.all_skills()`（core+bonus+keywords）；逐词 `kw.lower() in haystack.lower()` 做**子串**判断，返回 `(missing, covered)`。子串匹配意味着短英文词（如 `RAG`）可能被误判为已覆盖 —— 编码事实，注释未提及。

### 1.6 各维度启发式（含阈值与 severity）

**ATS（`ats_findings` L114-198）**

| 触发 | 行号 | severity |
|---|---|---|
| JD 关键词缺失 | L121-140 | `base = "red" if ratio < 0.5 else "yellow"`；只取 `missing[:4]`，第 1 条用 base，其余阶梯降级 |
| 无技能栏 | L142-154 | red |
| 无邮箱且无手机 | L156-168 | yellow |
| `len(raw_text) > 3000` | L170-182 | yellow |
| 表格线/分隔符正则命中（见下方引用） | L184-196 | yellow |

```python
# analyze.py:184
    if re.search(r"[│┃|]{2,}|\+[-=]{3,}\+|={5,}", raw_text):
```

阶梯代码：

```python
# analyze.py:121-126
    if missing:
        # 严重度按「整体覆盖率」定，而不是给每个缺失词都标红 ——
        # 4 个缺失关键词是同一个事实，不该产出 4 条红色风险。
        base_severity = "red" if ratio < 0.5 else "yellow"
        for index, kw in enumerate(missing[:4]):
            severity = base_severity if index == 0 else ("yellow" if base_severity == "red" else "green")
```

标签行（L28）不参与「缺量化」判断：`^(技术栈|技术选型|使用工具|开发环境|相关技术|标签|项目职责|关键词)[:：]`，由 `content_bullets`（L208-210）过滤。

**影响力（`impact_findings` L220-293）**：要点 `len>=8` 且无量化信号取前 3 条 → `red if weak_verb else yellow`（L236）；有要点但零量化 → red（L246-259）；`EXAGGERATION_RE` 命中 ≥4 → yellow（L261-276）；全篇无 `STRONG_VERBS` → yellow（L278-291）。

```python
# analyze.py:21-25
QUANT_SIGNAL_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:%|％|万|亿|千|百|倍|人|个|次|条|款|单|笔|张|台|家|项|场|名|位|套|组|版|层|轮|份|页|行|"
    r"元|秒|分钟|小时|天|周|月|年|毫秒|ms|s|QPS|qps|TPS|tps|w|W|k|K|M))"
    r"|(\d[\d,]{2,})"
)
```

语义 = **带量词/单位的数字** 或 **任意 3 位以上数字**。弱动词 `WEAK_VERBS`（L30-41，10 组，如 `"负责": "主导"`）；强动词 `STRONG_VERBS`（L43-47，中英 25 项）；`EXAGGERATION_RE`（L49）与 `DISCRIMINATION_RE`（L50）另见下节。

**清晰度（`clarity_findings` L299-367）**：单条要点 `len > 120` → yellow（L303-320，只报最长一条）；要点 `len < 8` → yellow（L322-336，只报第一条）；工作段缺 `start`/`end` → yellow（L338-351）；无教育经历 → **green**（L353-365）。

**风险（`risk_findings` L500-544 + `timeline_gaps` L409-497）**

```python
# analyze.py:373-384、387-388（空行已略）
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
```

- `至今/现在/今/present` → `None`，由 `or now_ym` 兜底为当前月（L424、L436）。
- 月份越界（如 `2022.13`）静默回落 1 月（L382-383）。
- 时间以绝对月序号比较，差值即月数。

| 触发 | 行号 | severity |
|---|---|---|
| `gap = next_start - cur_end >= 6` 且简历内有空窗说明 | L444-464 | green（正面条目「约 N 个月的空窗期已主动说明，处理得当」） |
| 同上、无说明 | L466-477 | `red if gap >= 12 else yellow`（L469） |
| 相邻段重叠 `overlap >= 2` | L479-495 | yellow（`break`，只报第一处） |
| 项目技术未登记技能栏（`len(t) >= 2`） | L504-526 | yellow |
| `DISCRIMINATION_RE` 命中 | L529-542 | yellow |

技能一致性用**双向子串**（宁漏勿假，`analyze.py:507-512`）：

```python
# analyze.py:507-512
    def _listed(tech: str) -> bool:
        # 双向子串判断：既避免 "Spring Cloud" 因为技能栏写作 "Spring"/"Cloud" 而误报，
        # 也避免 "SQL" 因为技能栏只有 "MySQL" 而误报。宁可漏报也不制造假警报。
        return any(tech in skill or skill in tech for skill in skill_set)

    unlisted = [t for t in project_tech if not _listed(t) and len(t) >= 2]
```

空窗说明检索按句子扫描，要求同时含说明性关键词与时间锚点：

```python
# analyze.py:398-406（节选）
    for segment in re.split(r"[\n。；;]", raw_text):
        segment = segment.strip()
        if not segment or not GAP_EXPLANATION_RE.search(segment):
            continue
        if "空窗" in segment or "间隔" in segment:
            return segment
        if any(tok and tok in segment for tok in (start_token, end_token)):
            return segment
```

`GAP_EXPLANATION_RE`（L68-70）= `(备考|考研|进修|自学|学习|培训|课程|认证|考证|自由职业|接项目|外包|个人项目|创业|家庭|休整|间隔年|调整)`；`DISCRIMINATION_RE`（L50）= `(性别|男性优先|女性优先|年龄限制|限男|限女|已婚|未婚|婚育|生育状况|户籍|本地户口|身高|体重|外貌|形象气质)`。

**竞争力差距（`gap_findings` L550-616）**

```python
# analyze.py:562-565
            now = datetime.now(timezone.utc)
            have_years = (now.year * 12 + now.month - _months(earliest)) / 12
            need = parsed_jd.experience_years
            if need and have_years + 0.5 < need:
```

`have_years` 由**最早一段工作的 start** 推算（L554-558）；缺口需大于半年才报；severity `"red" if need - have_years >= 2 else "yellow"`（L569）。学历按等级下标比较（L580-584）：

```python
# analyze.py:580-584
        level_order = ["高中", "大专", "专科", "本科", "硕士", "博士"]
        need_level = next((lv for lv in level_order if lv in parsed_jd.education), None)
        have_levels = [lv for e in parsed_resume.education for lv in level_order if e.degree and lv in e.degree]
        have_level = max(have_levels, key=level_order.index) if have_levels else None
        if need_level and (not have_level or level_order.index(have_level) < level_order.index(need_level)):
```

注意「大专」与「专科」是**两个独立档位**（下标 1 与 2），并不等价。第三条：覆盖率 < 0.6 且 `coverage_hit` 非空才报（L598-614）。

### 1.7 确定性设计的硬约束

```python
# analyze.py:1-9（节选）
"""确定性分析引擎：产出「发现项」候选与五维评分。
...
2. **可解释、可回归**：同一份简历两次跑出的分数必须一致，否则「二次诊断对比分数变化」就是假的；
3. **mock 模式下它就是全部**：无 API Key 时报告质量完全由这一层决定。
```

对应实现：`report_agent.py:3-5` 明确「评分由规则层计算，不让 LLM 打分」；`verify.py:237-247` 用「同一简历重跑两次分数必须一致」做回归断言；`engine.py:116-121` 在 qc 之后**重算分数**。

---

## 2. finding 的完整生命周期

### 2.1 阶段一：规则层创建（裸 dict）

规则函数手写字段集合，以 ATS 缺失关键词为例（注意 `original_text` 是 JD 首行，而证据引文是关键词本身）：

```python
# analyze.py:127-140（节选）
            out.append(
                {
                    "category": "ats",
                    "severity": severity,
                    "title": f"JD 要求的关键词「{kw}」在简历中缺失",
                    "original_text": jd_text.strip().splitlines()[0][:120] if jd_text.strip() else "",
                    "issue": f"目标 JD 明确要求「{kw}」，但简历全文未出现该关键词，ATS 关键词匹配会直接漏掉。",
                    "suggestion": f"若确有相关经验，请在技能栏或对应项目描述中显式写出「{kw}」；若确实没有，考虑先补齐再投递。",
                    "rewritten_text": f"技能：{kw}（补充到技能栏，并在使用过它的那条经历里点名）",
```

这个「两者故意不同」的设计有专门的回归检查（`verify.py:212-213`）：

```python
# verify.py:212-213
    kw_findings = [f for f in findings if "关键词" in f["title"] and "缺失" in f["title"]]
    kw_ok = all((f["evidence"].get("quote") or "") and (f["evidence"]["quote"] in f["title"]) for f in kw_findings)
```

证据定位由 `services/evidence.py` 三级降级完成：① 精确 `find`；② 折叠空白后匹配（`_normalized_index_map` 维护折叠文本→原文下标映射）；③ 退化到最长可定位前缀：

```python
# evidence.py:67-73
        # 3) 退化到最长可定位前缀（模型可能截断或润色了引文尾部）
        for cut in range(len(norm_quote), min_prefix - 1, -1):
            nidx = norm_text.find(norm_quote[:cut])
            if nidx >= 0:
                start = index_map[nidx]
                end = index_map[min(nidx + cut - 1, len(index_map) - 1)] + 1
                return [start, end]
```

`min_prefix: int = 8`（L43）。定位失败即打 `unverified`：

```python
# evidence.py:108-111
    evidence: dict = {"source": source, "quote": quote, "span": span, "field": field}
    if span is None and source in ("resume", "jd"):
        evidence["unverified"] = True
    return evidence
```

### 2.2 阶段二：规范化 + 单 Agent 去重（`coerce_findings`）

**去重键（verbatim）**：

```python
# agents/finding_utils.py:38-40
def _dedupe_key(item: dict) -> str:
    basis = (item.get("original_text") or item.get("title") or "")[:48]
    return f"{item.get('category')}|{basis}"
```

语义：**`category` + 前 48 字符的（original_text 优先，否则 title）**。后果（可直接从代码推出）：前缀相同的长文本会被判重；两者皆空时键为 `"<category>|"`，同 category 的空文本条目只保留第一条。

白名单与三条硬性要求：

```python
# agents/finding_utils.py:55-63
        category = entry.get("category")
        if category not in VALID_CATEGORY:
            category = default_category

        severity = entry.get("severity")
        if severity not in VALID_SEVERITY:
            severity = "yellow"

        title = str(entry.get("title") or "").strip()[:250]
```

```python
# agents/finding_utils.py:82-88
        # 三条硬性要求：有问题、有建议、有可复制改写句
        if not issue or not suggestion or not rewritten:
            continue
        if EMPTY_TALK_RE.match(suggestion) and not rewritten:
            continue
        if not title:
            title = issue[:40]
```

`VALID_CATEGORY={ats,impact,clarity,risk,gap}`、`VALID_SEVERITY={red,yellow,green}`（L16-17），与 `schemas.py:13-14` 的 `Literal` 一致；`MAX_FINDINGS = 12`（L22），超出即 `break`（L106-107）。引文优先级（L74-80）：`evidence_quote > quote > evidence.quote > original_text`，注释解释了为什么不能让 `original_text` 优先（关键词缺失类问题里 `original_text` 是 JD 首行，见 L70-72）。

### 2.3 阶段三：跨 Agent 合并（`merge_findings`）

```python
# agents/finding_utils.py:112-123
def merge_findings(*groups: list[dict]) -> list[dict]:
    """跨 Agent 合并并按 (category, original_text) 去重，保留先出现的（严重度更高的先来）。"""
    merged: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for item in group or []:
            key = _dedupe_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged[:MAX_FINDINGS]
```

**参数顺序即优先级**：`ats_agent.py:53`、`impact_agent.py:53`、`risk_agent.py:61` 均为 `merge_findings(llm_findings, baseline)` —— LLM 版先入、规则版兜底，键冲突时规则版被丢弃。唯一例外是建议阶段强制保留规则层红色风险：

```python
# agents/suggestion_agent.py:50-54
            # LLM 版本为主，但保留规则层中被 LLM 漏掉的红色风险（不能因为模型疏忽而丢风险）
            red_kept = [f for f in baseline if f["severity"] == "red" and not _covered(f, llm_findings)]
            findings = merge_findings(llm_findings, red_kept)
        else:
            findings = baseline
```

各 Agent 写入方式：`parse` 不产 finding；`jd_match` 先用 `false_gaps` **反向删除**已有 finding（`jd_match_agent.py:74`）再 `add_findings`；`ats`/`impact`/`risk` 用 `merge_findings(...)` → `add_findings`；`suggestion`（`suggestion_agent.py:67`）与 `qc`（`qc_agent.py:102`）**整体替换** `ctx.findings`。`jd_match` 是唯一被文档化允许推翻规则结论的节点：

```python
# agents/jd_match_agent.py:3-5
规则层给出「字面缺失」与「年限/学历差距」的硬事实；
LLM 负责区分「真差距」与「表述差异」（例如简历写「服务治理」而 JD 写「微服务」），
因此本 Agent 允许 LLM 从缺失清单里剔除误报 —— 这是唯一允许 LLM 推翻规则结论的地方。
```

### 2.4 阶段四：条数与类别配额

`MAX_FINAL = 8`、`MIN_FINDINGS = 5`（`suggestion_agent.py:16-17`）。类别保底算法：

```python
# agents/suggestion_agent.py:112-119（节选）
    # 1) 每个类别保底一条（ordered 已按严重度排序，取到的即该类别最严重的一条）
    for item in ordered:
        category = item.get("category", "")
        if category in seen_categories:
            continue
        seen_categories.add(category)
        picked.append(item)
        picked_ids.add(id(item))
```

```python
# agents/suggestion_agent.py:121-129（节选）
    # 2) 剩余名额按严重度补足
    for item in ordered:
        if len(picked) >= limit:
            break
        if id(item) not in picked_ids:
            picked.append(item)
            picked_ids.add(id(item))

    return analyze.sort_findings(picked)[:limit]
```

注释记录了不这么做的线上后果（L101-104）：「关键词缺失类问题动辄 4–8 条且都是红色，会把"时间线空窗""技能与项目矛盾"这类只有黄色、但危害更大的风险挤出报告，导致风险维度得分虚高（曾经出现过弱简历风险安全度 100 分的情况）」。

`_ensure_minimum`（L187-216）在 suggestion 与 qc 两处调用（`suggestion_agent.py:57`、`qc_agent.py:95`），先补通用检查项、再补正面发现项，最后 `sort_findings(...)[:MAX_FINAL]`：

```python
# agents/suggestion_agent.py:205-216（节选）
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
```

补的是**真实优点**（green）而非编造问题，理由在 L194-197：「硬造 5 个"问题"就是编造问题 —— 所以这里补的是简历里真实存在的优点，标记为 green，并给出「面试时怎么用好它」的可执行建议」。`_supplementary_findings`（L328-436）7 类真实检查项；`_positive_findings`（L219-325）6 类 green 项。

### 2.5 阶段五：QC 闸门（keep / downgrade / drop）

```python
# agents/qc_agent.py:29
SEVERITY_DOWNGRADE = {"red": "yellow", "yellow": "green", "green": "green"}
```

`_check`（L131-161）判定顺序：① 歧视性内容 → drop（一票否决）；② red 且引文无法定位 → downgrade；③ 缺 `rewritten_text`/`suggestion` → drop；④ 知识库引用仅记录不拦截。

```python
# agents/qc_agent.py:142-154（节选）
    if severity == "red":
        locatable = bool(quote) and (
            locate_quote(ctx.resume_text, quote) is not None
            or locate_quote(ctx.jd_text, quote) is not None
            or evidence.get("source") == "knowledge"
        )
        if not locatable:
            return {
                "index": index,
                "action": "downgrade",
                "rule": "no_evidence",
```

LLM 复核（L71-92）对 `kept` 二次 drop/downgrade，drop 用 `kept[idx] = None` 占位后统一过滤（L85、L94）。公平性通过率（L98-100）：

```python
# agents/qc_agent.py:98-100
        evaluated = max(evaluated, 1)
        fairness_pass_rate = 1.0 - (len([v for v in violations if v.get("rule") == "discrimination"]) / evaluated)
        fairness_pass_rate = max(0.0, min(1.0, fairness_pass_rate))
```

`evaluated` 初值是质检**前**的 findings 数（L41），`max(...,1)` 保证分母非零。qc 收尾再 `_ensure_minimum` + `sort_findings`（L95-96）。

### 2.6 阶段六：排序（verbatim）

```python
# analyze.py:695-704
SEVERITY_ORDER = {"red": 0, "yellow": 1, "green": 2}


def sort_findings(findings: list[dict]) -> list[dict]:
    """red → yellow → green，同级按 category 固定顺序，保证前端展示稳定。"""
    category_order = {"ats": 0, "impact": 1, "risk": 2, "clarity": 3, "gap": 4}
    return sorted(
        findings,
        key=lambda f: (SEVERITY_ORDER.get(f.get("severity", "yellow"), 1), category_order.get(f.get("category", ""), 9)),
    )
```

二级键固定顺序 `ats → impact → risk → clarity → gap`，未知类别落 9；缺 severity 视为 yellow。`sorted` 稳定，同键条目保持插入顺序（即 Agent 执行顺序）。

### 2.7 阶段七：落库（workflow engine）

```python
# workflow/engine.py:116-121
            if agent.name == "qc":
                # QC 会改动 findings，必须在它之后再定稿分数，保证总分与问题清单一致
                ctx.scores = analyze.compute_scores(ctx.findings, ctx.parsed_resume, ctx.parsed_jd, ctx.resume_text)
                report = _persist_report(db, task, ctx)
                report_id = report.id
```

```python
# workflow/engine.py:185-198（节选）
    for order, finding in enumerate(analyze.sort_findings(ctx.findings)):
        db.add(
            ReportFinding(
                report_id=report.id,
                category=finding.get("category"),
                severity=finding.get("severity"),
                title=(finding.get("title") or "")[:250],
                original_text=finding.get("original_text"),
                issue=finding.get("issue"),
                suggestion=finding.get("suggestion"),
                rewritten_text=finding.get("rewritten_text"),
                evidence_ref=finding.get("evidence"),
                sort_order=order,
```

字段映射要点：`evidence` → 列 `evidence_ref`；`title` 截断 250 字符（列宽 `String(255)`）。其余元数据进 `AnalysisReport.extra_json`（L172-180：`missing_keywords[:20]`、`covered_keywords[:30]`、`action_items`、`knowledge_refs`、`qc`、`disclaimer`、`agent_outputs`（剔除 parse））。

### 2.8 阶段八：序列化与渲染

```python
# serializers.py:146-157（节选）
def serialize_finding(finding) -> dict:
    return {
        "finding_id": finding.id,
        "category": finding.category or "ats",
        "severity": finding.severity or "yellow",
        "title": finding.title or "",
        "original_text": finding.original_text or "",
        "issue": finding.issue or "",
        "evidence": finding.evidence_ref or {"source": "resume", "quote": "", "span": None, "field": None},
        "suggestion": finding.suggestion or "",
        "rewritten_text": finding.rewritten_text or "",
    }
```

顺序靠 `sort_order` 恢复：`serialize_report` 内 `sorted(report.findings, key=lambda x: x.sort_order or 0)`（`serializers.py:188`）；`report_render.render_markdown` 按该顺序逐条输出（`report_render.py:59`）。

### 2.9 生命周期总览（ASCII）

```
analyze.*_findings()  ──▶ 裸 dict 候选（含 evidence.span 或 unverified）   analyze.py:114/220/299/500/550
        ▼ coerce_findings(raw, default_category, ctx)
   白名单收敛 + 3 条硬要求 + _dedupe_key 去重 + ≤12              finding_utils.py:43-109
        ▼ merge_findings(llm_findings, baseline)
   跨 Agent 去重（先到者胜，LLM 优先）+ ≤12                      finding_utils.py:112-123
        ▼ ctx.add_findings(...)   [jd_match 可用 false_gaps 反向删除]   base.py:45 / jd_match_agent.py:73-74
        ▼ suggestion: _finalize(≤8) → LLM → +red_kept → _select_final → _ensure_minimum(≥5)
                                                                  suggestion_agent.py:25-57, 98-129, 187-216
        ▼ qc: 规则 4 检 → LLM 决策 → drop/downgrade → _ensure_minimum → sort_findings
                                                                  qc_agent.py:36-102
        ▼ engine: compute_scores(最终 findings) → _persist_report(sort_order=下标)
                                                                  engine.py:116-121, 159-201
        ▼ serialize_report（按 sort_order） → render_markdown / 前端
                                                                  serializers.py:188 / report_render.py:20-114
```

---

## 3. 规则解析器（`app/services/parse_rules.py`，537 行）

### 3.1 定位与能力边界（作者自陈）

```python
# parse_rules.py:3-9（节选）
1. mock 模式下作为解析 Agent 的实现（无 Key 也能产出可用结构）；
2. live 模式下作为**确定性基线** —— 先规则抽取，再让 LLM 补全与纠错，
   这样即使模型输出漂移，结构化字段也不会整体崩掉。

能力边界（诚实标注）：规则解析对「PDF 抽出的线性文本」效果好，
对多栏排版/表格简历会有串行错位；live 模式下由 LLM 兜底纠正。
```

### 3.2 通用正则（L22-28）

| 常量 | 行号 | 要点 |
|---|---|---|
| `EMAIL_RE` | 22 | `[\w.+-]+@[\w-]+\.[\w.-]+` |
| `PHONE_RE` | 23 | 前后 `(?<!\d)`/`(?!\d)` 防粘连；支持 `1[3-9]\d[-\s]?\d{4}[-\s]?\d{4}` |
| `NAME_RE` | 24 | `^[\u4e00-\u9fff]{2,4}$`（纯中文 2-4 字） |
| `DATE_TOKEN` | 25 | `\d{4}[年./\-]\d{1,2}月?` 或裸 4 位年 或 `至今\|现在\|今\|present`（三种大小写变体） |
| `DATE_RANGE_RE` | 26 | `({DATE_TOKEN})\s*[-~—–－至到]+\s*({DATE_TOKEN})` |
| `BULLET_RE` | 28 | 行首项目符号，含 `\uf000-\uf0ff` 私用区（Word/Wingdings 符号） |

### 3.3 段落切分：关键词表 + 乱序标题兜底

章节词表为 `OrderedDict`（L35-49），8 个 key：`basics / education / work / projects / skills / certificates / summary / others`。`is_section_heading` 的三重判定：

```python
# parse_rules.py:60-84（节选，含关键注释；空行已略）
    除精确/前后缀匹配外，还处理一种真实存在的 PDF 抽取伪影：
    pypdf 对带字距的 CJK 标题会输出乱序文本（如「项目经历」→「目项 经历」）。
    """
    stripped = _clean(line)
    if not stripped or len(stripped) > 16:
        return None
    norm = _norm_heading(stripped)
    for key, keywords in SECTION_KEYWORDS.items():
        for kw in keywords:
            if norm == kw or norm.startswith(kw) or norm.endswith(kw):
                return key
    # 乱序标题兜底：字符多重集相同且长度 >= 3（长度太短容易误判）
    if len(norm) >= 3:
        scrambled = "".join(sorted(norm))
```

守卫：行长 >16 直接否；`_norm_heading` 先剥 `[\s:：\-—_【】\[\]（）()]`（L56-57）。乱序兜底要求长度 ≥3 且 `sorted()` 字符多重集**完全相等**（L78-83）。切段主循环（L87-102）：空行保留为空串；命中标题则切换 `current` 且标题行本身不入内容；无标题内容进 `preamble`。

### 3.4 条目聚合：两道守卫

```python
# parse_rules.py:175-185（节选，含关键注释）
    两个必须的守卫（都踩过坑）：
    1. **以项目符号开头的行一定是要点，不是条目头**；
    2. **日期必须出现在行首或行尾**才算条目的时间标记。
       否则「参与大促稳定性保障，……，2023 年双十一期间可用性 99.98%」这种正文里提到年份的要点
       会被误判成新经历，凭空多出一段假经历（进而误报"经历没有内容要点"和"两段经历时间重叠"）。
    """
    stripped = (raw_line or "").strip()
    if not stripped:
        return False
    if BULLET_RE.match(stripped):
        return False
```

```python
# parse_rules.py:187-195（节选）
    cleaned = _clean(raw_line)
    if not cleaned:
        return False

    date_match = DATE_RANGE_RE.search(cleaned) or SINGLE_DATE_RE.search(cleaned)
    if date_match:
        near_start = date_match.start() <= 2
        near_end = date_match.end() >= len(cleaned) - 2
        return near_start or near_end
```

日期判定之后的兜底分支（L197-199，verbatim）：`if 0 < len(cleaned) <= 40 and re.search(r"(公司|集团|科技|有限|银行|研究院|大学|学院|中学|事业部|中心)", cleaned): return True`。

「行首或行尾」的量化定义是**容差 2 字符**；无日期时退到「行长 ≤40 且含机构特征词」。`_iter_entries`（L202-229）状态机：

| 分支 | 条件 | 动作 | 行号 |
|---|---|---|---|
| 1 | 像条目头 | 新建 entry；有 `pending_header` 则拼接 | 214-221 |
| 2 | 有 entry 且 `len > 6` | 追加到上一条 highlights | 222-223 |
| 3 | 否则 `len <= 30` | 记为 `pending_header`（等下一行的日期） | 224-225 |
| 4 | 否则有 entry | 追加 highlights | 226-227 |

`pending_header` 解决「公司名单独一行、日期在下一行」的真实排版。

### 3.5 日期剥离与组织/职位切分

```python
# parse_rules.py:105-115
def _split_header_and_dates(line: str) -> tuple[str, str, str]:
    """从一行里剥离出日期区间，返回 (标题部分, start, end)。"""
    match = DATE_RANGE_RE.search(line)
    if match:
        header = (line[: match.start()] + " " + line[match.end() :]).strip()
        return header, match.group(1).strip(), match.group(2).strip()
    match = SINGLE_DATE_RE.search(line)
    if match:
        header = (line[: match.start()] + " " + line[match.end() :]).strip()
        return header, match.group(1).strip(), ""
    return line.strip(), "", ""
```

优先区间、其次单日期（`end=""`）。`_split_org_title`（L118-132）：先按 `[|｜/／·・]` 与两空格切，再退化到 `-－—–` 单破折号左右切；不足两段时只返回第一段作机构名。

### 3.6 技能抽取：词表优先 + 分隔符补齐

```python
# parse_rules.py:136-157（节选，含关键注释）
    1. 先按 TECH_DICT 抽已知技术名，长词优先（保证得到 "Spring Cloud" 而不是 "Spring"+"Cloud"；
       同时 "MySQL" 命中后 "SQL" 不再单独入列）；
    ...
    matched = sorted(
        (t for t in TECH_DICT if re.search(re.escape(t), text, re.IGNORECASE)),
        key=len,
        reverse=True,
    )
    canonical: list[str] = []
    for tech in matched:
        low = tech.lower()
        if any(low in existing.lower() for existing in canonical):
            continue
        canonical.append(tech)
```

`extract_skills` 分两步走（L136 注释：「两步走，避免把多词技术名拆碎」）。第二段按 `[、,，;；/|｜·\s]+` 切分，token 长度约束 `1 < len(token) <= 24`，剔除已被长词覆盖的碎片，最终 `list(dict.fromkeys(canonical + extras))[:40]`（L160-169）。`TECH_DICT`（L383-397）为约 140 项硬编码词表，跨语言/框架/存储/中间件/云/大数据/AI/产品/运营/项目管理。

### 3.7 简历解析主流程（`parse_resume_text`，L235-377）

关键设计约束（L236-240）：**先累积普通 dict/list，最后一次性 `model_validate`**，注释说明「不要往 ParsedResume 的列表里直接 append(dict) —— pydantic 不会校验 append 进去的元素……曾经在这里踩过 AttributeError」。

| 步骤 | 要点 | 行号 |
|---|---|---|
| 分段 | `preamble` → `head_lines`，`basics` 段 → `basics_block` | 242-244 |
| 邮箱/手机 | 全文正则搜索，**不限于 basics 段** | 255-258 |
| 姓名 | 前 6 行 preamble + 前 8 行 basics 中找纯中文 2-4 字；兜底 `姓名[:：]` 标签 | 260-277 |
| 城市 | `CJK_CITY`（30 城，L29-33）首个命中 | 266-270 |
| 链接 | `(?:https?://\|github\.com/\|gitee\.com/\|www\.)` 取前 5 | 278 |
| work / projects | `_iter_entries` + `_split_org_title`；项目 tech 见下 | 281-311 |
| education | `degree` 匹配 `(博士\|硕士\|本科\|大专\|专科\|学士\|MBA\|高中)`；`major` 仅当 `len(parts)>=3` 取 `parts[2]` | 314-333 |
| skills | `extract_skills(sections["skills"])` | 336 |
| certificates | 每行 clean 后截 60 字符，去重上限 15 | 339-343 |
| timeline | 由 work+education 拼，`start`/`end` 至少一个非空 | 346-365 |
| 校验 | 一次性 `ParsedResume.model_validate({...})` | 367-377 |

项目 tech 的收紧原因（L297-301）：早期用「任意英文单词」抽取，把 `MTTR`/`QPS`/`ROI` 当成技术栈，进而误报「项目里用到的技术在技能栏没有列出」，因此改为**只认 TECH_DICT**，上限 `[:10]`（L309）。

**降级行为**：本函数**不捕获异常**；纯文本为空时所有抓取都取不到值，返回「字段齐全但全空」的合法模型（空输入 → 空结构，不抛异常）；结构不兼容时抛 `ValidationError`，由调用方兜底（见 4.4）。

### 3.8 JD 解析（`parse_jd_text`，L424-500）

以 `ParsedJd.empty()` 为起点（L427），末尾 `model_validate(parsed.model_dump())` 再净化一次（L500）。

| 字段 | 提取方式 | 行号 |
|---|---|---|
| `title` | 优先 `(?:岗位\|职位)(?:名称)?[:：]\s*([^\n，。；]{2,40})`；否则扫前 12 行，命中 `JD_TITLE_HINTS`（L399）且无日期区间 | 432-440 |
| `company` | `公司[:：]\s*([^\n，。；]{2,40})` | 443-445 |
| `seniority`/`experience_years` | 优先 `(\d+)\s*[-~—–至到]\s*(\d+)\s*年` 取最小值；否则「N 年以上 / 及以上 / N+ 年」；否则「应届 / 校招 / 无经验」→ 0 | 448-458 |
| `education` | `(博士\|硕士\|本科\|大专\|专科\|统招本科)(?:及以上\|以上)?`，保留「及以上」后缀 | 461-464 |
| `responsibilities` | `_lines_under(RESP_HEADINGS, all_headings)`，每行截 200 | 467-468 |
| `skills_core`/`skills_bonus` | 全文命中 `TECH_DICT`；出现在加分项段落者归 bonus；core ≤25、bonus ≤15 | 476-480 |
| core 兜底 | core 为空时从任职要求段抽 `[A-Za-z][A-Za-z0-9+#.]{1,20}` | 482-485 |
| `keywords` | 34 项业务域词池命中，≤20 | 488-495 |
| title 兜底 | 首行前 40 字 | 497-498 |

`_lines_under`（L407-421）用 `norm.startswith(h)` 进入捕获、遇任一其他标题即 `break`，因此**标题列表顺序决定边界**。四组标题常量见 L401-404（`RESP_HEADINGS`/`REQ_HEADINGS`/`BONUS_HEADINGS`/`COMPANY_HEADINGS`）。

### 3.9 岗位族识别（`guess_role_family`，L506-536）

haystack = `title + skills_core + keywords + responsibilities[:500]`，按 `ROLE_FAMILY_RULES`（L506-519，12 条）**顺序**首个命中即返回，否则 `"general"`（L534-537）。顺序即优先级：`backend` 规则含 `Python`，`data` 规则含 `Spark`/`数据分析`，故「Python 数据工程师」JD 会先命中 `backend`。`role_family` 下游用于 `METRIC_HINTS` 选提示（`analyze.py:222`）、知识库过滤、`_positive_findings` 文案（`suggestion_agent.py:232`）。

---

## 4. 数据契约（`app/schemas.py`）

### 4.1 枚举别名（L13-16）

```python
Severity = Literal["red", "yellow", "green"]
FindingCategory = Literal["ats", "impact", "clarity", "risk", "gap"]
KnowledgeScope = Literal["role", "industry", "resume_template", "compliance"]
PayProvider = Literal["stripe", "wechat", "alipay"]
```

与 `models.py` 的列注释、`verify.py` 的枚举断言三方一致。

### 4.2 ParsedResume（L208-244）

| 字段 | 类型 | 默认 | 子字段 |
|---|---|---|---|
| `basics` | `Basics` | `Basics()` | name/email/phone/city/links |
| `education` | `list[EducationItem]` | `[]` | school/degree/major/start/end/highlights |
| `work` | `list[WorkItem]` | `[]` | company/title/start/end/location/highlights |
| `projects` | `list[ProjectItem]` | `[]` | name/role/start/end/highlights/tech |
| `skills` | `list[str]` | `[]` | — |
| `certificates` | `list[str]` | `[]` | 荣誉/奖项在渲染层并入此字段 |
| `timeline` | `list[TimelineItem]` | `[]` | start/end/label/type |

两个被评分引擎重度依赖的派生方法（L221-244）：

```python
# schemas.py:221-228（节选）
    def bullets(self) -> list[tuple[str, str]]:
        """产出 (字段路径, 原文段落)，用于证据链定位与原右对照展示。"""
        out: list[tuple[str, str]] = []
        for i, w in enumerate(self.work):
            for j, h in enumerate(w.highlights):
                out.append((f"work[{i}].highlights[{j}]", h))
```

字段路径形如 `work[0].highlights[2]`。`timeline_entries()`（L235-244）**优先返回 `timeline`**，为空时才从 work/education 拼。

### 4.3 ParsedJd（L247-267）与 `all_skills`

字段：`title / company / seniority / experience_years(int|None) / education / skills_core / skills_bonus / responsibilities / keywords`。

```python
# schemas.py:262-267
    def all_skills(self) -> list[str]:
        seen: list[str] = []
        for s in [*self.skills_core, *self.skills_bonus, *self.keywords]:
            if s and s not in seen:
                seen.append(s)
        return seen
```

`all_skills()` 是关键词覆盖率的**唯一分母来源**（`analyze.py:100`）。

### 4.4 `empty()` 与「校验失败 → empty」

```python
# schemas.py:217-219
    @classmethod
    def empty(cls) -> "ParsedResume":
        return cls()
```

`ParsedJd.empty()` 同形（L258-260）。所有字段都有默认值，因此 `empty()` = **构造完整、内容为空**的合法实例，而非 `None`。

四种兜底引擎（前三种是 `except → empty()`）：

| 位置 | 代码 | 行号 |
|---|---|---|
| 工作流引擎 | `ParsedResume.model_validate(raw or {})` / `except: return ParsedResume.empty()` | `engine.py:145-156` |
| 序列化器 | `model_validate(raw or {}).model_dump()` / `except: empty().model_dump()` | `serializers.py:24-35` |
| 简历接口 | `model_validate(resume.parsed_json or {})` / `except: ParsedResume.empty()` | `api/resumes.py:55-57` |
| 解析 Agent（LLM 输出） | `model_validate(candidate)` / `except: return baseline` | `parse_agent.py:19-21` |

第四种回落基线而非 empty，因为该处手里已有规则基线：

```python
# agents/parse_agent.py:16-21
def _merge_parsed(baseline: ParsedResume, candidate: dict) -> ParsedResume:
    """以 LLM 输出为主、规则基线兜底。"""
    try:
        llm_parsed = ParsedResume.model_validate(candidate)
    except Exception:  # noqa: BLE001 —— 模型结构不合法就整段回落到基线
        return baseline
```

「键完整」有可执行断言：`verify.py:77-78`（ParsedResume 七键）、`verify.py:120`（ParsedJd 九键）。

### 4.5 EditorContent（L90-161）与 ParsedResume 的差别

`EditorContent` 含 `basics/summary/work/projects/education/skills/certificates/awards/custom_sections`；比 `ParsedResume` 多 `summary / awards / custom_sections` 与 `basics.target_title`。设计意图在 L85-88 的注释：ParsedResume 是「解析结果」（允许缺失、允许失败），EditorContent 是「用户亲手写的完整内容」，两者用 `resume_render` 双向转换。入库闸门：

```python
# schemas.py:143-146
        basics = self.basics
        has_basics = bool(basics.name.strip() and (basics.phone.strip() or basics.email.strip()))
        has_body = bool(self.summary.strip() or self.work or self.projects or self.education or self.skills)
        return has_basics and has_body
```

对应 `verify.py:486-492` 的「内容过少 → 400 / VALIDATION_ERROR」。请求体契约同文件：`JdCreate.raw_text` 至少 20 字（L25-33，`field_validator` 抛 `ValueError`）。

### 4.6 报告 / finding 对外字段

`serialize_report`（`serializers.py:160-193`）顶层字段：

`report_id, task_id, resume_id, resume_title, resume_source, has_editor_content, jd_title, created_at, scores{overall,ats,impact,clarity,match,risk}, risk_level, summary, findings[], missing_keywords[], action_items[], knowledge_refs[], disclaimer`。

`serialize_finding` 输出 9 字段（见 2.8）。两处集合断言把契约变成可执行测试：`verify.py:168`（报告 13 字段）、`verify.py:179`（finding 9 字段）。免责声明默认值来自 `report_agent.py:14`：

```python
# agents/report_agent.py:14
DISCLAIMER = "本报告由 AI 生成，仅供求职参考，不构成录用决策依据，也不构成法律意见。"
```

并在 `serializers.py:192` 作为缺省兜底。

---

## 5. LLM 适配与三级降级链（`services/llm.py` + `cost.py` + `errors.py`）

### 5.1 设计声明（L5-10）

```python
1. **单一入口**：所有 Agent 只调用 `LLMClient.complete_json()`，不直接碰 HTTP。
2. **DeepSeek 走 OpenAI 兼容协议**（/chat/completions + response_format=json_object），
   所以适配层天然兼容任何 OpenAI 兼容网关，把 base_url 指过去即可。
3. **三重降级链**：live 调用 → 重试 N 次 → 回落到 Agent 提供的 `mock_factory`（标记 degraded）。
```

模式在构造时确定（L70-73）：`self.mode = settings.llm_mode`、`self.model = settings.llm_model if self.mode == "live" else "mock-rule-engine"`。

### 5.2 L1：mock 短路

```python
# llm.py:90-96（节选）
        if self.mode == "mock":
            started = time.perf_counter()
            data = mock_factory()
            latency = int((time.perf_counter() - started) * 1000)
            return LLMResult(
                data=data,
                text=json.dumps(data, ensure_ascii=False),
```

其余字段：`tokens_in=0, tokens_out=0, cost=0.0, mode="mock", note="mock 模式：由规则引擎生成（未配置 DEEPSEEK_API_KEY）"`（L97-103），`degraded` 保持默认 `False`。**保证**：零网络、零 token、零成本，输出结构与 live 完全一致（`mock_factory` 由各 Agent 提供，docstring 要求「必须返回契约合规的 dict」，L88）。

### 5.3 L2：live 重试 + 指数退避

```python
# llm.py:106-114
        last_error: Exception | None = None
        attempts = max(1, settings.llm_max_retries + 1)
        for attempt in range(1, attempts + 1):
            try:
                return await self._call_deepseek(system=system, user=user, agent=agent)
            except Exception as exc:  # noqa: BLE001 —— 网络/解析/限流都归为可重试
                last_error = exc
                if attempt < attempts:
                    await asyncio.sleep(min(2 ** (attempt - 1), 4))
```

- 尝试次数 = `llm_max_retries + 1`；默认 `llm_max_retries = 2`（`config.py:39`）→ **最多 3 次**。
- 退避 `min(2**(attempt-1), 4)` 秒 → 失败后依次睡 **1s、2s**，上限 4s。
- `except Exception` 覆盖网络错误、HTTP ≥400（包成 `LlmError`）、JSON 解析失败、响应结构异常；重试不重建 prompt。

单次请求（L137-158）：

```python
# llm.py:137-146
        payload = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 4096,
        }
```

URL 为 `f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"`（L155），超时 `settings.llm_timeout_s`（L153）。失败与记账：

```python
# llm.py:161-173（节选）
        if resp.status_code >= 400:
            raise LlmError(f"DeepSeek {resp.status_code}: {resp.text[:300]}")
        ...
        data = _extract_json(content)
        usage = body.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens") or estimate_tokens(system + user))
        tokens_out = int(usage.get("completion_tokens") or estimate_tokens(content))
```

**token 优先级**：优先真实 `usage`；为 0 或缺失时本地估算（`or` 短路）。模型名以响应回填（`model=body.get("model", settings.llm_model)`，L182）。**保证**：瞬时故障不直接降级；每次调用真实 token/成本被记账（`cost=compute_cost(...)`，L180）。

### 5.4 L3：降级到 `mock_factory`

```python
# llm.py:116-131（节选）
        # 全部重试失败 → 降级到规则引擎，保证任务不整体失败
        started = time.perf_counter()
        data = mock_factory()
        ...
            tokens_in=0,
            tokens_out=0,
            cost=0.0,
            ...
            degraded=True,
            note=f"LLM 调用失败，已降级为规则引擎：{type(last_error).__name__}: {str(last_error)[:200]}",
```

**保证**：任务不因 LLM 失败整体失败；`degraded=True` 是唯一可靠降级信号。该信号向上传播：`AgentOutput.status = "degraded" if result.degraded else "done"`（如 `ats_agent.py:64`）→ `WorkflowStep.status = "degraded"`（`engine.py:96-99`）→ `step.notes = output.notes`（L100）。

注意降级分支的 `model` 仍是 `self.model`（live 下为 `settings.llm_model`）而 `mode` 写成 `"mock"`，因此**不能用 mode 反推原始模型**，必须看 `degraded`。

### 5.5 三级链路总表

| 级别 | 触发 | 行号 | tokens/cost | `degraded` | `mode` | 保证 |
|---|---|---|---|---|---|---|
| L1 mock | 无 `DEEPSEEK_API_KEY` | 90-104 | 0 / 0.0 | `False` | `mock` | 零依赖跑通全链路 |
| L2 live 重试 | 有 Key | 106-115 | 真实 usage | `False` | `live` | ≤3 次 + 指数退避 |
| L3 降级 | L2 全败 | 116-131 | 0 / 0.0 | **`True`** | `mock` | 契约合规输出 + notes 记录异常 |

### 5.6 `_extract_json` 的容忍度

```python
# llm.py:28
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
```

```python
# llm.py:45-64（节选）
def _extract_json(text: str) -> dict:
    """从模型输出里抠出 JSON 对象，容忍 markdown 代码块与前后废话。"""
    cleaned = _FENCE_RE.sub("", (text or "").strip()).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
```

三级容错：① 剥 markdown 围栏 → `json.loads`；② `{` 到 `}` **最长区间**切片再解析（容忍前后废话）；③ 全部失败后 `raise LlmError("模型未返回合法 JSON")`（L64）→ 触发 L2 重试 → 最终 L3 降级。引文省略的尾部（L59-63）与首段同形，均为 `if isinstance(parsed, dict): return parsed` + `except json.JSONDecodeError: pass`。由两处 `isinstance(parsed, dict)`（L50、L60）与 `rfind`（L56）可直接推出两条边界：**顶层数组必被拒绝**；多个 JSON 对象拼接时切片会跨对象、通常解析失败后降级。

### 5.7 成本核算（`cost.py`）

```python
# cost.py:12-21
def estimate_tokens(text: str) -> int:
    """粗估 token 数：中日韩字符按 1 token/字，其余按 4 字符/token。

    仅在 mock 模式或响应未返回 usage 时使用；live 模式优先用 API 返回的真实 usage。
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = max(0, len(text) - cjk)
    return int(cjk + other / 4) + 1
```

```python
# cost.py:24-30（节选）
def compute_cost(tokens_in: int, tokens_out: int) -> float:
    """按配置单价折算人民币，保留 4 位小数以对齐 workflow_steps.cost NUMERIC(10,4)。"""
    cost = (
        tokens_in / 1_000_000 * settings.llm_price_input_per_m
        + tokens_out / 1_000_000 * settings.llm_price_output_per_m
    )
    return round(cost, 4)
```

CJK 正则含扩展 A 与兼容区：`[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]`（L9）。估算含 `+1` 常数项，空文本返回 0。默认单价输入 2.0 / 输出 8.0（元/百万 token），`round(...,4)` 对齐 `Numeric(10,4)`（`models.py:193`）。汇总口径在 `api/admin.py:43`（`cost_total / reports_total`），`verify.py:310` 断言 `≤ 0.5`（对应 `aaa.md:492`）。

### 5.8 错误契约（`errors.py`）

```python
# errors.py:23-24
    def to_payload(self) -> dict:
        return {"detail": {"code": self.code, "message": self.message}}
```

| 类 | status | code | 行号 |
|---|---|---|---|
| `ValidationError` | 400 | `VALIDATION_ERROR` | 27-30 |
| `UnsupportedFileType` | 415 | `UNSUPPORTED_FILE_TYPE` | 33-36 |
| `FileTooLarge` | 413 | `FILE_TOO_LARGE` | 39-42 |
| `ResumeNotFound`/`JdNotFound`/`TaskNotFound`/`ReportNotFound`/`BatchNotFound` | 404 | 各自 `*_NOT_FOUND` | 45-72 |
| `PlanNotFound`/`TemplateNotFound` | 404 | `PLAN_NOT_FOUND`/`TEMPLATE_NOT_FOUND` | 75-84 |
| `PdfUnavailable` | 503 | `PDF_UNAVAILABLE` | 87-93 |
| `QuotaExceeded` | 402 | `QUOTA_EXCEEDED` | 96-99 |
| `LlmError` | 502 | `LLM_ERROR` | 102-105 |

`LlmError` 正常路径下被 `complete_json` 吞掉并转 L3 降级；只有 critical 的解析 Agent（`parse_agent.py:59` 的 `critical = True`）失败才冒泡成任务失败（`engine.py:101-106`）。

### 5.9 文件 → 文本抽取（`extract.py`，108 行）

```python
# extract.py:15-27（节选）
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".txt", ".md"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


@dataclass
class ExtractResult:
    text: str
    method: str
    note: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())
```

分派（`extract_text`，L97-108）：`.pdf → pypdf`（逐页 `extract_text` 后 `\n` 拼接）、`.docx → python-docx`（段落 + **表格**，单元格以 `" | "` 连接，L55-59）、图片 → `pytesseract.image_to_string(image, lang="chi_sim+eng")`、`.txt/.md → 多编码链`、其他 → `unsupported`。**该模块不抛异常**（L98 注释），失败信息进 `note`：

| 场景 | method | note 要点 | 行号 |
|---|---|---|---|
| PDF 无文本层 | `pypdf` | 可能是扫描件，请安装 tesseract 或改传 DOCX/文本 | 44-47 |
| 无 pytesseract | `ocr-unavailable` | 未安装 pytesseract / tesseract | 68-73 |
| OCR 抛错 | `ocr-failed` | `f"OCR 识别失败：{type(exc).__name__}: {str(exc)[:160]}"` | 80-85 |
| 编码全败 | `text` | 文本编码无法识别 | 88-94 |

编码尝试顺序 `("utf-8", "utf-8-sig", "gb18030", "latin-1")`（L89）；`latin-1` 永不抛 `UnicodeDecodeError`，故该循环对任意字节都能返回（可能乱码）。

---

## 6. 配置面（`app/config.py`）

载体 `BaseSettings`，`env_file = BACKEND_DIR / ".env"`、`env_file_encoding="utf-8"`、`extra="ignore"`（L19-23）。环境变量名 = 字段名大写（pydantic-settings 默认，大小写不敏感）。

| 字段 | 默认值 | 环境变量 | 用途 |
|---|---|---|---|
| `app_name` | `"ZhenResume 简甄"` | `APP_NAME` | 应用名 |
| `version` | `"1.0.0"` | `VERSION` | 健康检查/文档版本 |
| `database_url` | `f"sqlite+pysqlite:///{(DATA_DIR / 'zhenresume.db').as_posix()}"` | `DATABASE_URL` | DSN；换 PG 只改此处 |
| `upload_dir` | `DATA_DIR / "uploads"` | `UPLOAD_DIR` | 上传落盘目录 |
| `max_upload_mb` | `10` | `MAX_UPLOAD_MB` | 上传上限（对应 `FileTooLarge`） |
| `deepseek_api_key` | `""` | `DEEPSEEK_API_KEY` | **空则 mock 模式** |
| `deepseek_base_url` | `"https://api.deepseek.com"` | `DEEPSEEK_BASE_URL` | OpenAI 兼容网关 |
| `llm_model` | `"deepseek-chat"` | `LLM_MODEL` | 模型名 |
| `llm_timeout_s` | `60.0` | `LLM_TIMEOUT_S` | httpx 超时（秒） |
| `llm_max_retries` | `2` | `LLM_MAX_RETRIES` | 重试次数（尝试数 = +1） |
| `llm_price_input_per_m` | `2.0` | `LLM_PRICE_INPUT_PER_M` | 输入单价（元/百万 token） |
| `llm_price_output_per_m` | `8.0` | `LLM_PRICE_OUTPUT_PER_M` | 输出单价 |
| `embedding_dim` | `1536` | `EMBEDDING_DIM` | 向量维度 |
| `rag_top_k` | `5` | `RAG_TOP_K` | 检索条数 |
| `libreoffice_node` | `""` | `LIBREOFFICE_NODE` | Node 路径；留空 = PDF 关闭 |
| `libreoffice_cli` | `""` | `LIBREOFFICE_CLI` | CLI 路径 |
| `pdf_timeout_s` | `120.0` | `PDF_TIMEOUT_S` | 转换超时 |
| `free_quota` | `3` | `FREE_QUOTA` | 免费额度 |
| `frontend_base_url` | `"http://127.0.0.1:3000"` | `FRONTEND_BASE_URL` | 分享链接前缀（`reports.py:69`） |
| `cors_origins` | `"http://127.0.0.1:3000,http://localhost:3000"` | `CORS_ORIGINS` | 逗号分隔 |

派生属性（L60-78）：

```python
# config.py:60-74（节选；空行已略）
    @property
    def llm_mode(self) -> str:
        """live = 真实调用 DeepSeek；mock = 规则引擎离线生成。"""
        return "live" if self.deepseek_api_key.strip() else "mock"

    @property
    def llm_provider(self) -> str:
        return "deepseek" if self.llm_mode == "live" else "mock"
    @property
    def pdf_enabled(self) -> bool:
        """PDF 转换是否可用（依赖外部 LibreOffice Kit）。"""
        if not self.libreoffice_node or not self.libreoffice_cli:
            return False
        return Path(self.libreoffice_node).exists() and Path(self.libreoffice_cli).exists()
```

`pdf_enabled` 不仅看是否配置，还要求**两个路径真实存在**；配置了但文件被删/迁移 → 503 降级（`resume_render.py:525-526`）。`cors_origin_list` 按逗号切分去空（L76-78）。

生命周期（L80-92）：`ensure_dirs()` 用 `mkdir(parents=True, exist_ok=True)`；`get_settings` 带 `@lru_cache`，**导入即建目录**；模块级单例 `settings = get_settings()`。因项目统一 `from ..config import settings`，**运行期改环境变量不生效**；但 `verify.py:536-551` 直接给 `settings.libreoffice_node/cli` 赋空值来强制覆盖降级分支（pydantic 实例属性可写），这是降级路径能被测试覆盖的机制。

---

## 7. 数据库层（`app/db.py` + `app/models.py`）

### 7.1 引擎与连接

```python
# db.py:22-29
_is_sqlite = settings.database_url.startswith("sqlite")

engine = create_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)
```

```python
# db.py:32-39
if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record) -> None:  # pragma: no cover
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
```

每个连接都开 `foreign_keys=ON`（`ondelete="CASCADE"` 生效前提）与 WAL。

```python
# db.py:42
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
```

`autoflush=False` → 引擎内手工 `db.flush()`（`engine.py:63/91/114/130`）；`expire_on_commit=False` → commit 后属性仍可读，便于把 ORM 对象交给序列化器。建表（L45-49）：`from . import models` 后 `Base.metadata.create_all(bind=engine)`，注释明确「生产环境应替换为 Alembic 迁移」。

### 7.2 `session_scope` 语义（verbatim）

```python
# db.py:61-72
@contextmanager
def session_scope() -> Iterator[Session]:
    """后台任务（工作流）里使用，独立于请求生命周期。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```

| 维度 | `get_db`（L52-58） | `session_scope`（L61-72） |
|---|---|---|
| 使用者 | FastAPI 依赖注入（请求生命周期） | 后台工作流 / 脚本 |
| 提交 | 由调用方决定 | **正常退出自动 `commit()`** |
| 回滚 | 无 | 异常 `rollback()` 并 `re-raise` |
| 关闭 | `finally: close()` | `finally: close()` |

**推断的行为后果（标注为推断）**：`run_workflow` 用 `with session_scope() as db:` 包住整条流程（`engine.py:50`），因此任何未捕获异常会回滚整个工作流的写入，包括已落库的 `WorkflowStep` 与 `_fail()` 中已 `flush()` 的失败状态（`engine.py:138-142`）—— 注释未说明，未实测。

### 7.3 表清单（17 张，`models.py`）

| # | 表名 | 行号 | 用途 |
|---|---|---|---|
| 1 | `organizations` | 42-48 | 企业/组织，`plan` 记组织级套餐 |
| 2 | `users` | 51-65 | 用户：`role`(jobseeker/hr/admin)、`device_id` 匿名隔离、`free_quota_used` |
| 3 | `resumes` | 71-86 | 简历：`raw_text`、`parsed_json`、`file_url`、文件名/MIME/大小 |
| 4 | `job_descriptions` | 89-100 | JD：`raw_text` + `parsed_json` |
| 5 | `analysis_tasks` | 106-129 | 任务：`status`(pending/running/done/failed)、`progress`、`current_step` |
| 6 | `analysis_reports` | 132-155 | 报告：五维分 + `risk_level` + `summary` + `extra_json` |
| 7 | `report_findings` | 158-179 | 单条 finding：category/severity/title/issue/suggestion/rewritten/`evidence_ref`/`sort_order` |
| 8 | `workflow_steps` | 182-199 | 每 Agent 一行：status(含 degraded)、input/output JSON、tokens、cost、latency、notes |
| 9 | `knowledge_docs` | 205-219 | RAG 文档：scope、role_family、正文 |
| 10 | `knowledge_chunks` | 222-233 | 切片 + `embedding`（SQLite 存 JSON，PG 换 `Vector(1536)`） |
| 11 | `payments` | 239-254 | 订单：`order_no` 唯一、金额、渠道、回调原文 |
| 12 | `subscriptions` | 257-266 | 订阅：plan、status、起止时间 |
| 13 | `feedbacks` | 272-280 | 报告评分（1-5）与评论 |
| 14 | `audit_logs` | 283-291 | 审计：action/target/ip |
| 15 | `hr_batches` | 297-309 | HR 批量批次：total / finished |
| 16 | `hr_batch_items` | 312-327 | 批次内单份结果快照（overall_score/match_score/risk_level/summary） |
| 17 | `prompt_templates` | 333-340 | Prompt 版本管理：name + version 递增 + content |

文件头四条迁移约定（L3-8）：① 表名/列名与 DDL 一致；② 主键 Python 侧 `uuid4`（`_uuid()`，L31-32）；③ `[扩展]` 列需在迁移时补上；④ `knowledge_chunks.embedding` 换 PG 时改 `Vector(1536)` 并建 HNSW 索引。`created_at` 均为 `DateTime(timezone=True)` + `_now()`（UTC），唯一例外是 `PromptTemplate.created_at` 另带 `server_default=func.now()`（L340）。命名冲突规避：ORM 属性 `doc_metadata` 映射列名 `"metadata"`（L215-216、L230），因为 `metadata` 是 SQLAlchemy 保留属性。

---

## 8. 自检脚本 `backend/scripts/verify.py`（659 行）

### 8.1 运行方式与结构

```python
# verify.py:4-9（节选）
用法（在 backend 目录下）：
    python -m scripts.verify

它会真的上传简历、解析 JD、执行 8-Agent 工作流、生成报告、下单支付、
建知识库、跑 HR 批量，并对 docs/api-contract.md 的每一项硬性要求做断言。
任何一项失败都会以非 0 退出码结束。
```

```python
# verify.py:43-50
def main() -> int:
    # 显式建表 + 灌种子：不依赖 TestClient 是否触发 lifespan，脚本单独运行也可靠
    init_db()
    with session_scope() as db:
        seed_if_empty(db)

    with TestClient(app) as client:
        return _run(client)
```

- 用 `TestClient` **同步**跑完整链路；后台任务在响应返回前执行完，因此「创建任务返回 202」后可立即断言 `done`（L140 注释）。
- `sys.path.insert(0, parent)`（L17）支持 `-m scripts.verify`；身份用 `DEVICE = "verify-device"`（L27）+ 头 `X-Device-Id`（L54）。
- 样本：`samples/sample_resume_{1_weak,2_medium,3_strong}.txt` + `sample_jd_backend.txt`。
- 退出码：有失败 `return 1`（L648），全通过 `return 0`（L650），顶层异常 `code = 2`（L656-658）。

### 8.2 检查机制（verbatim）

```python
# verify.py:32-36
def check(name: str, condition: bool, detail: str = "") -> bool:
    results.append((bool(condition), name, detail))
    flag = "PASS" if condition else "FAIL"
    print(f"[{flag}] {name}" + (f"  —— {detail}" if detail and not condition else ""))
    return bool(condition)
```

全局 `results` 列表 + 逐项 PASS/FAIL 打印 + 末尾汇总；`detail` 仅在失败时输出。全脚本无 `assert`。

### 8.3 12 个 section 与代表性检查名（verbatim）

| # | 标题 | 行号 | 代表检查名 |
|---|---|---|---|
| 1 | 健康检查（契约 §1） | 56-64 | `health 返回 200`、`health 字段完整`、`llm_mode 合法` |
| 2 | 简历上传与解析（§2） | 66-109 | `ParsedResume 键完整`、`解析出工作经历`、`不支持的文件类型返回 415`、`错误结构符合契约 §8` |
| 3 | JD 解析（§3） | 111-130 | `ParsedJD 键完整`、`识别出年限要求`（==3）、`识别出学历要求`、`过短 JD 返回 400` |
| 4 | 分析任务与 8-Agent 工作流（§4） | 132-159 | `总步数为 8`、`步骤顺序与契约一致`、`所有步骤均完成或降级`、`progress 达到 1.0` |
| 5 | 报告结构与验收标准（§5 + aaa.md §5） | 161-247 | `五维分齐全`、`risk_level 与 risk 分派生一致`、`发现项 ≥ 5 条（验收标准）`、`发现项按 red→yellow→green 排序`、`每条红色风险都有可定位原文引用（验收标准）`、`同一简历两次诊断分数一致（可回归）` |
| 6 | 定价与支付（§6.1） | 249-272 | `套餐 code 集合与契约一致`、`订单金额与套餐一致`(19.9)、`回调幂等`、`未知套餐返回 404` |
| 7 | 知识库 RAG（§6.2） | 274-298 | `种子知识库已灌入`、`检索结果按相关度降序`、`返回切片数` |
| 8 | 反馈与管理后台（§6.3/§6.4） | 300-326 | `单份成本 ≤ 0.5 元（aaa.md §4）`、`公平性通过率 100%（验收标准）`、`步骤成本明细覆盖 8 步`、`Prompt 版本号自增` |
| 9 | HR 批量初筛（§6.5） | 328-358 | `批次已全部完成`、`按总分降序排列`、`强弱简历分数有区分度`、`CSV 含 BOM（Excel 不乱码）` |
| 10 | 隐私删除与错误处理（§8 / aaa.md §4） | 360-384 | `删除简历返回 204`、`404 错误结构符合契约`、`分享 URL 指向前端` |
| 11 | 文档格式抽取回归（DOCX / PDF） | 386-414 | `{name} 工作经历段数与纯文本基线一致`、`{name} 识别出项目经历（标题未被串段）` |
| 12 | 简历模板与编辑器（§2.3–§2.5 / §6.6） | 416-619 | `内置模板 ≥ 4 套`、`每个区块都带写作提示与示例句`、`工作经历被正确结构化（渲染的纯文本可被解析器读回）`、`PDF 未配置时返回 503 而不是 500` |

### 8.4 针对规则解析器与降级路径的定点回归

```python
# verify.py:389-392
    # 同一份简历的 .txt 基线是 2 段工作经历 + 1 个项目。
    # PDF 走 pypdf，会遇到的真实坑：CJK 标题乱序（「项目经历」→「目项 经历」）、
    # 项目符号落在私用区（\uf0b7）。漏掉标题会让项目段串进工作经历，造出虚假经历。
    baseline_work = 2
```

降级路径必须**主动构造**才能覆盖（真配了 LibreOffice 时降级永不触发）：

```python
# verify.py:533-541（节选）
    # 本机配了 LibreOffice 时，上面的分支走的是「成功路径」，降级分支永远覆盖不到。
    # 但降级恰恰是最容易写错的地方（未配置时抛 500 而不是 503），所以临时抹掉配置显式验证一次。
    original_node, original_cli = settings.libreoffice_node, settings.libreoffice_cli
    try:
        settings.libreoffice_node = ""
        settings.libreoffice_cli = ""
        degraded = client.get(f"/api/v1/resumes/{ed['resume_id']}/export/pdf", headers=headers)
```

六类「回归保护」检查及各自防的倒退：

| 检查名 | 行号 | 防的是什么 |
|---|---|---|
| `发现项按 red→yellow→green 排序` | 183-185 | `sort_findings` 排序契约被破坏 |
| `报告包含风险类别发现项（风险维度不被关键词问题挤掉）` | 206-209 | 关键词类 red 挤掉 risk 类黄（即 `_select_final` 注释所述事故） |
| `关键词类发现项的引文是关键词本身` | 211-218 | `original_text` 与 `evidence.quote` 被写反 |
| `该样本触发时间线空窗检测` | 219 | 空窗检测失效 |
| `已说明空窗期时不再要求补充说明` / `已说明空窗期转为正面条目（green）` | 589-619 | 让人做已经做过的事（`_find_gap_explanation` 回归） |
| `PDF 转换后关键内容完整（未静默截断）` | 512-525 | DOCX→PDF 静默丢内容（读 PDF 文本层核对而非只看文件头） |

### 8.5 「通过」长什么样 / 它证明了什么

```python
# verify.py:629-637（节选）
    print(f"\n{'=' * 72}")
    print(f"验证结果：{passed}/{total} 项通过")
    # 检查项总数会随环境浮动，这里直接说明原因，避免误以为"少了 3 项 = 环境不全"。
    # 差异来源：① PDF 导出是否可用（5 项 ↔ 2 项降级检查）；
    #          ② 免费额度是否放行二次诊断一致性校验（1 项）。
```

```python
# verify.py:642
    print("      常见总数：177（含 PDF）/ 174（未配置 PDF）。两者都代表「全部通过」。")
```

判定标准是 **`failed == []`（退出码 0）**，不是固定条数；总数在 174 与 177 之间浮动（PDF 可用性 ±3、二次诊断一致性 ±1）。成功标志为末行「全部契约检查通过。」（L649）。

**它证明了**：契约（`docs/api-contract.md`）的字段/枚举/状态码/排序/幂等/降级在真实 HTTP 链路上成立；`aaa.md §5` 验收项（≥5 条建议、红风险必有可定位引文、单份成本 ≤0.5 元、公平性 100%）成立；评分**可复现**（同一简历两次分数一致，L243）。**它不证明**：live LLM 输出质量（mock 模式全走规则引擎）、并发与性能、真实支付网关回调。

---

## 9. 渲染层架构

### 9.1 `report_render.py`（114 行）：单格式、纯 Markdown

```python
# report_render.py:1-5
"""报告渲染：Markdown 导出（契约 §5.2）。

不做 PDF：MVP 阶段 Markdown 能被浏览器直接打印成 PDF，且零依赖；
后续接 Playwright / WeasyPrint 时复用本模块的渲染结果即可。
```

唯一入口 `render_markdown(report: dict) -> str`（L20），输入就是 `serialize_report` 的 dict；路由 `api/reports.py:52-58` 直接拼 `Content-Disposition: attachment`。输出六章：① 总分与维度分（含权重列 `30%/25%/20%/15%/10%`，L38-42）→ ② 风险概览 → ③ 逐条发现（原文/问题/证据/建议/改写句）→ ④ 缺失关键词 → ⑤ 下一步行动清单 → ⑥ 知识库依据 + 免责声明，与 `aaa.md:433-448` 的报告结构逐条对应。证据缺失时不留空而是写明原因（L74-75：「该结论基于全文统计，无单条引文」）。

### 9.2 `resume_render.py`（575 行）：四条渲染路径 + 双向转换

```python
# resume_render.py:3-12（节选）
四条渲染路径必须保持一致：

1. **纯文本**：存进 `resumes.raw_text`，喂给现有的 8-Agent 诊断流水线。
   因此它的格式必须能被 `services/parse_rules.py` 正确读回 ——
   章节标题要用词表里的标准说法，经历条目的日期必须落在行尾，
   要点必须以 `- ` 开头。
2. **Markdown**：给用户复制粘贴到招聘网站或网申表单。
3. **DOCX**：给用户投递与微调。刻意**不使用表格、文本框、多栏排版**，
4. **PDF**：由 DOCX 转出，因此与 DOCX 的版式完全一致
```

`⇄` 转换（L46-173）：`to_parsed_resume(EditorContent) -> ParsedResume`、`from_parsed_resume(ParsedResume) -> EditorContent`。后者有**明确的信息损失**（L120-123：ParsedResume 没有 summary/target_title/awards/custom_sections，故这些字段为空）；`to_parsed_resume` 把 `awards` 并入 `certificates`（L96），并重建 `timeline`（L88-94）。

**与解析器对齐的往返约束**被固化为常量（L28-37）：`H_BASICS="个人信息"`、`H_SUMMARY="个人优势"`、`H_WORK="工作经历"`、`H_PROJECTS="项目经历"`、`H_EDUCATION="教育经历"`、`H_SKILLS="专业技能"`、`H_CERTIFICATES="证书"`、`H_AWARDS="荣誉奖项"`、`H_OTHERS="其他信息"` —— 这 9 个标题**全部**能被 `parse_rules.SECTION_KEYWORDS` 识别（`个人优势`→summary、`荣誉奖项`→certificates 的关键词 `荣誉` 前缀命中、`其他信息`→others）。日期落行尾对应解析器的 ±2 字符守卫：

```python
# resume_render.py:219-221
            date = _date_range(item.start, item.end)
            # 日期必须落在行尾：解析器靠「日期在行首或行尾」判定这是新的经历条目
            lines.append(f"{header}    {date}".rstrip() if date else header)
```

DOCX 两个非显然细节：① 中文字体必须写 `w:eastAsia`，否则 Word/WPS 回落宋体（`_style_run` L348-366，注释在 L350-353）；② 章节标题用**段落底边框**替代图形元素，注释理由是「ATS 常用「下边框」识别章节分隔」（L422）。PDF 由 DOCX 转出（`docx_to_pdf` L519-560）：`subprocess.run([settings.libreoffice_node, settings.libreoffice_cli, "convert", "--input", ..., "--output", ...])`；未配置即 `raise PdfUnavailable()`，超时/启动失败/非 0 退出码全部转 `PdfUnavailable`（L551-558）。文件名安全化在 L566-575（`_ILLEGAL_FILENAME` 去路径分隔符、`_SAFE_STEM` 保留中英文数字、`guess_extension` 防 `张三.pdf.docx`）；`verify.py:504` 断言响应头含 `filename*=UTF-8''`。

### 9.3 `resume_templates.py`（552 行）：模板即代码常量

```python
# resume_templates.py:3-6
**为什么是代码常量而不是数据库表**（与 `services/plans.py` 同理）：
模板的结构（有哪些区块、每栏怎么写）属于产品定义，改动应该走发版与评审，
不应该是运行期可随意漂移的数据。这样也免去了额外建表与种子灌入。
要改成后台可维护，只需把 `TEMPLATES` 换成表查询，接口与前端一行都不用动。
```

机制：`_sec(key, title, tip, examples, *, required=False, max_items=None)`（L16-32）为区块构造器，字段集 `{key,title,required,tip,examples,max_items}`；`COMMON_RULES`（L36-44）7 条共享写作规则，被各模板以 `*COMMON_RULES` 展开引用（如 L64）；`TEMPLATES`（L47+）含 4 套：`general_experienced`（L50，target=`experienced`）、`campus_fresh`（L170，`campus`）、`career_switch`（L287，`transition`）、`tech_engineer`（L405，`experienced`）。每套含 `intro / writing_rules / sections / example_content`；文件末的摘要函数只暴露 `code/name/target` 等轻量字段（L535-538），与 `verify.py:423-432` 的断言一致。

一致性的关键设计（L8-10）：模板里的写作提示从知识库文档提炼，因此**编辑器里看到的提示与诊断报告给出的建议是同一套标准**，不会自相矛盾。

---

## 10. 模块依赖 / 调用 ASCII 图

```
HTTP ─▶ app/api/*.py  (resumes / jds / tasks / reports / ...)
          │ Depends(get_db)                                    db.py:52
          ├─ api/resumes.py ─▶ services/extract.extract_text        （文件→文本）
          │                 ─▶ services/parse_rules.parse_resume_text
          │                 ─▶ services/resume_render.{to,from}_parsed_*
          ├─ api/jds.py     ─▶ services/parse_rules.parse_jd_text
          ├─ api/tasks.py   ─▶ workflow/engine.launch_workflow       （后台任务）
          └─ api/reports.py ─▶ serializers.serialize_report
                            ─▶ services/report_render.render_markdown
          │
          ▼ session_scope()  独立事务                              db.py:61
workflow/engine.run_workflow                                   engine.py:46
  · _safe_resume/_safe_jd（异常→empty）                        engine.py:145-156
  · rag.search 一次检索，全链路复用                             engine.py:75-77
  · 逐 Agent 落 WorkflowStep（status/tokens/cost/latency/notes）
  · if agent.name == "qc": compute_scores + _persist_report     engine.py:116-121
          │
          ▼ AGENT_PIPELINE（顺序即契约）              agents/__init__.py:13
parse ─▶ jd_match ─▶ ats ─▶ impact ─▶ risk ─▶ suggestion ─▶ report ─▶ qc
  │        │          │       │         │          │            │        │
  │        │          │       │         │          │            │        └▶ analyze.sort_findings
  │        │          │       │         │          │            │           analyze.compute_scores（复算）
  │        │          │       │         │          │            │           suggestion_agent._ensure_minimum
  │        │          │       │         │          │            └▶ analyze.compute_scores / risk_level_from_score
  │        │          │       │         │          └▶ _select_final / _ensure_minimum / _action_items
  │        │          │       │         └▶ analyze.risk_findings / timeline_gaps
  │        │          │       └▶ analyze.impact_findings / content_bullets / _is_quantified
  │        │          └▶ analyze.ats_findings / missing_keywords
  │        └▶ analyze.gap_findings（可用 false_gaps 反向删除 findings）
  └▶ services/parse_rules.parse_resume_text / parse_jd_text / guess_role_family
          │
          ▼ 横切依赖（所有 Agent 共享）
  agents/finding_utils.py  coerce_findings / merge_findings / _dedupe_key
      └▶ services/evidence.py  build_evidence → locate_quote（3 级匹配）
  agents/base.py           AgentContext（findings 累积，唯一共享状态）
  services/llm.py          LLMClient.complete_json
      ├▶ services/cost.py     estimate_tokens / compute_cost
      ├▶ app/errors.py        LlmError(502) → 被吞并转 L3 降级
      └▶ app/config.py        settings.llm_mode/model/retries/prices
  services/prompts.py      DEFAULTS + prompt_templates 覆盖
          │
          ▼
  app/models.py（17 张表） ◀── app/db.py（engine + SessionLocal + Base）
    写：analysis_tasks / workflow_steps / analysis_reports / report_findings
    读：resumes.parsed_json / job_descriptions.parsed_json
          │
          ▼
  app/schemas.py（ParsedResume / ParsedJd / EditorContent / 请求体）
     ▲ 被 parse_rules、resume_render、serializers、engine 共同依赖
          │
  scripts/verify.py ─▶ TestClient(app) ─▶ 上述全链路（12 section 断言）
```

依赖方向要点：**`schemas.py` 是所有结构化数据的唯一汇合点**；**`analyze.py` 不依赖任何 Agent、也不依赖数据库**（只依赖 `schemas` 与 `evidence`），因此可被 mock 路径与 verify 完整复用；**`llm.py` 不依赖任何 Agent**（`mock_factory` 由调用方注入），这是「换 provider 时 Agent 代码零改动」的结构性原因（`llm.py:5-6`）。

---

## 附：存疑与未覆盖项（诚实标注）

1. **未覆盖**：`services/rag.py`、`embeddings.py`、`plans.py`、`seed.py`、`frontend/`、各 `api/*` 的业务细节（本任务范围外，仅作调用点引用）。
2. **前提修正**：`analyze.py` 内无 dedupe/merge；去重键在 `agents/finding_utils.py:38-40`。
3. **源码事实但注释未说明的行为**（均已在上文给出引用）：coverage 缺省 0.85 与 Agent 侧 0.0 不一致（L644 vs `ats_agent.py:27`）；risk 维度 green 也扣 5 分（L659）导致「已说明空窗期」这一正向行为反而降分；`overall` 用已 clamp 的整数加权（L662-664）与先加权后 clamp 差 ±1；`_penalty` 直接下标 `f["category"]`（L680）；`_dedupe_key` 只取前 48 字符（`finding_utils.py:39`）；`missing_keywords` 子串匹配（`analyze.py:107`）；`estimate_tokens` 非 CJK 按 4 字符/token 粗估（`cost.py:12-21`）；`extract._extract_text` 因 `latin-1` 兜底而永不报「编码无法识别」（L88-94）。
4. **推测（明确标注）**：7.2 节关于 `session_scope` 异常回滚连带回滚 `_fail()` 状态，是基于 `db.py:61-72` 与 `engine.py:138-142` 的推断，注释未说明且未实测。
