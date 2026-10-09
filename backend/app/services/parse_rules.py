"""规则解析：把简历/JD 纯文本结构化成契约 §7 的 ParsedResume / ParsedJD。

双重用途：
1. mock 模式下作为解析 Agent 的实现（无 Key 也能产出可用结构）；
2. live 模式下作为**确定性基线** —— 先规则抽取，再让 LLM 补全与纠错，
   这样即使模型输出漂移，结构化字段也不会整体崩掉。

能力边界（诚实标注）：规则解析对「PDF 抽出的线性文本」效果好，
对多栏排版/表格简历会有串行错位；live 模式下由 LLM 兜底纠正。
"""

from __future__ import annotations

import re
from collections import OrderedDict

from ..schemas import ParsedJd, ParsedResume

# --------------------------------------------------------------------------- #
# 通用正则
# --------------------------------------------------------------------------- #
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9}|1[3-9]\d[-\s]?\d{4}[-\s]?\d{4})(?!\d)")
NAME_RE = re.compile(r"^[\u4e00-\u9fff]{2,4}$")
DATE_TOKEN = r"(?:\d{4}\s*[年./\-]\s*\d{1,2}\s*月?|\d{4}\s*[年./\-]?|至今|现在|今|present|Present|PRESENT)"
DATE_RANGE_RE = re.compile(rf"({DATE_TOKEN})\s*[-~—–－至到]+\s*({DATE_TOKEN})")
SINGLE_DATE_RE = re.compile(rf"({DATE_TOKEN})")
BULLET_RE = re.compile(r"^[\s\-–—•·*※▶▪◦○●◆■□→>\uf000-\uf0ff\u25cf\u25a0\u25aa\u2022\u2043\u2219]+")
CJK_CITY = [
    "北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "武汉", "西安", "苏州",
    "天津", "重庆", "长沙", "郑州", "青岛", "合肥", "福州", "厦门", "济南", "大连",
    "宁波", "无锡", "东莞", "佛山", "昆明", "南昌", "贵阳", "石家庄", "哈尔滨", "沈阳",
]

SECTION_KEYWORDS: "OrderedDict[str, list[str]]" = OrderedDict(
    [
        ("basics", ["个人信息", "基本信息", "联系方式", "个人资料"]),
        ("education", ["教育经历", "教育背景", "教育情况", "学历背景", "教育与培训"]),
        ("work", ["工作经历", "工作经验", "职业经历", "工作履历", "实习经历", "实习经验", "工作与实习"]),
        ("projects", ["项目经历", "项目经验", "主要项目", "项目实践"]),
        ("skills", ["专业技能", "技能清单", "技能特长", "技术栈", "掌握技能", "技能"]),
        ("certificates", ["证书", "荣誉", "获奖经历", "获奖情况", "资格证书", "语言能力"]),
        ("summary", ["自我评价", "个人总结", "个人优势", "职业总结"]),
        # 编辑器导出的自定义区块统一归到这里。
        # 刻意不映射到 ParsedResume 的任何字段：自定义区块是荣誉/开源/社团这类补充信息，
        # 不应该参与「要点是否量化」这类逐条批评。
        ("others", ["其他信息", "补充信息", "其他"]),
    ]
)


def _clean(line: str) -> str:
    return BULLET_RE.sub("", (line or "").strip()).strip()


def _norm_heading(line: str) -> str:
    return re.sub(r"[\s:：\-—_【】\[\]（）()]+", "", line or "")


def is_section_heading(line: str) -> str | None:
    """判断一行是否是章节标题，返回章节 key。

    除精确/前后缀匹配外，还处理一种真实存在的 PDF 抽取伪影：
    pypdf 对带字距的 CJK 标题会输出乱序文本（如「项目经历」→「目项 经历」）。
    因此当整行的字符多重集与某个标题关键词完全一致时，也判定为标题。
    这个兜底很重要 —— 漏掉标题会让整段内容串到上一章节里，凭空造出虚假经历。
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
        for key, keywords in SECTION_KEYWORDS.items():
            for kw in keywords:
                if len(kw) == len(norm) and "".join(sorted(kw)) == scrambled:
                    return key
    return None


def split_sections(text: str) -> dict[str, list[str]]:
    """按标题切段；无标题内容进入 preamble。"""
    sections: dict[str, list[str]] = {"preamble": []}
    current = "preamble"
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            sections.setdefault(current, []).append("")
            continue
        heading = is_section_heading(line)
        if heading:
            current = heading
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


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


def _split_org_title(header: str) -> tuple[str, str]:
    """把「某科技有限公司 | 后端开发工程师」拆成 (公司, 职位)。"""
    header = re.sub(r"[|｜/／·・]+", "\t", header).strip()
    parts = [p.strip() for p in re.split(r"\t|\s{2,}", header) if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    if not parts:
        return "", ""
    only = parts[0]
    for sep in ("-", "－", "—", "–"):
        if sep in only:
            left, _, right = only.partition(sep)
            if left.strip() and right.strip():
                return left.strip(), right.strip()
    return only, ""


def extract_skills(section_lines: list[str]) -> list[str]:
    """从技能栏文本抽取技能列表。

    两步走，避免把多词技术名拆碎：
    1. 先按 TECH_DICT 抽已知技术名，长词优先（保证得到 "Spring Cloud" 而不是 "Spring"+"Cloud"；
       同时 "MySQL" 命中后 "SQL" 不再单独入列）；
    2. 其余内容按分隔符切分，剔除已被第 1 步长词覆盖的碎片，保留「微服务」「高并发」这类非词表技能。
    """
    text = "\n".join(_clean(line) for line in section_lines if _clean(line))
    if not text:
        return []

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

    extras: list[str] = []
    for token in re.split(r"[、,，;；/|｜·\s]+", text):
        token = token.strip("：:。.（）()")
        if not (1 < len(token) <= 24):
            continue
        low = token.lower()
        if any(low in existing.lower() for existing in canonical):
            continue
        extras.append(token)

    return list(dict.fromkeys(canonical + extras))[:40]


def _looks_like_entry_header(raw_line: str) -> bool:
    """判断一行是否是新的经历条目头（含日期或公司特征词）。

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

    cleaned = _clean(raw_line)
    if not cleaned:
        return False

    date_match = DATE_RANGE_RE.search(cleaned) or SINGLE_DATE_RE.search(cleaned)
    if date_match:
        near_start = date_match.start() <= 2
        near_end = date_match.end() >= len(cleaned) - 2
        return near_start or near_end

    if 0 < len(cleaned) <= 40 and re.search(r"(公司|集团|科技|有限|银行|研究院|大学|学院|中学|事业部|中心)", cleaned):
        return True
    return False


def _iter_entries(lines: list[str]) -> list[dict]:
    """把段落行聚合成条目：每条含 header/start/end/highlights。"""
    entries: list[dict] = []
    pending_header: str | None = None

    for line in lines:
        if not line.strip():
            continue
        cleaned = _clean(line)
        if not cleaned:
            continue

        if _looks_like_entry_header(line):
            header, start, end = _split_header_and_dates(cleaned)
            if pending_header:
                # 上一行是孤立的公司名，与本行的日期合为一条
                header = f"{pending_header} {header}".strip() if header else pending_header
                pending_header = None
            entry = {"header": header, "start": start, "end": end, "highlights": []}
            entries.append(entry)
        elif entries and len(cleaned) > 6:
            entries[-1]["highlights"].append(cleaned)
        elif len(cleaned) <= 30:
            pending_header = cleaned
        elif entries:
            entries[-1]["highlights"].append(cleaned)

    return entries


# --------------------------------------------------------------------------- #
# 简历解析
# --------------------------------------------------------------------------- #
def parse_resume_text(text: str) -> ParsedResume:
    """先累积普通 dict/list，最后一次性 model_validate。

    不要往 ParsedResume 的列表里直接 append(dict) —— pydantic 不会校验 append 进去的元素，
    后续代码会拿到 dict 而不是模型实例（曾经在这里踩过 AttributeError）。
    """
    text = text or ""
    sections = split_sections(text)
    head_lines = [l for l in sections.get("preamble", []) if l.strip()]
    basics_block = [l for l in sections.get("basics", []) if l.strip()]

    basics: dict = {"name": "", "email": "", "phone": "", "city": "", "links": []}
    education: list[dict] = []
    work: list[dict] = []
    projects: list[dict] = []
    skills: list[str] = []
    certificates: list[str] = []
    timeline: list[dict] = []

    # --- 基本信息 ---
    email = EMAIL_RE.search(text)
    phone = PHONE_RE.search(text)
    basics["email"] = email.group(0) if email else ""
    basics["phone"] = phone.group(0) if phone else ""

    candidate_lines = head_lines[:6] + basics_block[:8]
    for line in candidate_lines:
        cleaned = _clean(line)
        if not basics["name"] and NAME_RE.match(cleaned):
            basics["name"] = cleaned
            continue
        if not basics["city"]:
            for city in CJK_CITY:
                if city in cleaned:
                    basics["city"] = city
                    break
    if not basics["name"]:
        for line in candidate_lines:
            cleaned = _clean(line)
            match = re.match(r"(?:姓名|名字)[:：]\s*([\u4e00-\u9fff]{2,4})", cleaned)
            if match:
                basics["name"] = match.group(1)
                break
    basics["links"] = re.findall(r"(?:https?://|github\.com/|gitee\.com/|www\.)[^\s，。；、]+", text)[:5]

    # --- 工作经历 ---
    for entry in _iter_entries(sections.get("work", [])):
        company, title = _split_org_title(entry["header"])
        work.append(
            {
                "company": company,
                "title": title,
                "start": entry["start"],
                "end": entry["end"],
                "location": "",
                "highlights": entry["highlights"],
            }
        )

    # --- 项目经历 ---
    for entry in _iter_entries(sections.get("projects", [])):
        name, role = _split_org_title(entry["header"])
        # 只认已知技术词表里的名词。
        # 早期版本用「任意英文单词」抽取，结果把 MTTR / QPS / ROI 这类指标当成技术栈，
        # 进而误报「项目里用到的技术在技能栏没有列出」。宁少不滥。
        joined = " ".join(entry["highlights"])
        tech = [t for t in TECH_DICT if re.search(re.escape(t), joined, re.IGNORECASE)]
        projects.append(
            {
                "name": name,
                "role": role,
                "start": entry["start"],
                "end": entry["end"],
                "highlights": entry["highlights"],
                "tech": list(dict.fromkeys(tech))[:10],
            }
        )

    # --- 教育经历 ---
    for entry in _iter_entries(sections.get("education", [])):
        header = entry["header"]
        degree_match = re.search(r"(博士|硕士|本科|大专|专科|学士|MBA|高中)", header)
        parts = [p.strip() for p in re.split(r"\s{2,}|\t|[|｜]", header) if p.strip()]
        school = parts[0] if parts else header
        major = ""
        if len(parts) >= 3:
            major = parts[2]
        elif len(parts) == 2 and not degree_match:
            major = parts[1]
        education.append(
            {
                "school": school,
                "degree": degree_match.group(1) if degree_match else "",
                "major": major,
                "start": entry["start"],
                "end": entry["end"],
                "highlights": entry["highlights"][:3],
            }
        )

    # --- 技能 ---
    skills = extract_skills(sections.get("skills", []))

    # --- 证书 ---
    for line in sections.get("certificates", []):
        cleaned = _clean(line)
        if cleaned:
            certificates.append(cleaned[:60])
    certificates = list(dict.fromkeys(certificates))[:15]

    # --- 时间线 ---
    for item in work:
        if item["start"] or item["end"]:
            timeline.append(
                {
                    "start": item["start"],
                    "end": item["end"],
                    "label": f"{item['company']} {item['title']}".strip(),
                    "type": "work",
                }
            )
    for item in education:
        if item["start"] or item["end"]:
            timeline.append(
                {
                    "start": item["start"],
                    "end": item["end"],
                    "label": f"{item['school']} {item['degree']}".strip(),
                    "type": "education",
                }
            )

    return ParsedResume.model_validate(
        {
            "basics": basics,
            "education": education,
            "work": work,
            "projects": projects,
            "skills": skills,
            "certificates": certificates,
            "timeline": timeline,
        }
    )


# --------------------------------------------------------------------------- #
# JD 解析
# --------------------------------------------------------------------------- #
TECH_DICT = [
    "Python", "Java", "Go", "Golang", "C++", "C#", "JavaScript", "TypeScript", "PHP", "Ruby", "Rust", "Scala", "Kotlin", "Swift",
    "React", "Vue", "Angular", "Next.js", "Nuxt", "Node.js", "Express", "Django", "Flask", "FastAPI", "Spring", "Spring Boot", "Spring Cloud",
    "MySQL", "PostgreSQL", "Oracle", "SQL Server", "MongoDB", "Redis", "Elasticsearch", "ClickHouse", "HBase", "TiDB", "SQLite",
    "Kafka", "RabbitMQ", "RocketMQ", "Pulsar", "ZooKeeper",
    "Docker", "Kubernetes", "K8s", "Helm", "Istio", "Jenkins", "GitLab CI", "CI/CD", "DevOps", "Terraform", "Ansible",
    "Linux", "Nginx", "AWS", "Azure", "GCP", "阿里云", "腾讯云", "华为云",
    "Spark", "Flink", "Hadoop", "Hive", "Airflow", "ETL", "数据仓库", "数据湖",
    "PyTorch", "TensorFlow", "LLM", "RAG", "NLP", "CV", "推荐系统", "机器学习", "深度学习",
    "微服务", "分布式", "高并发", "高可用", "性能优化", "领域驱动设计", "DDD", "中台",
    "产品设计", "需求分析", "用户研究", "数据分析", "SQL", "Excel", "Tableau", "Power BI",
    "Figma", "Sketch", "Axure", "Photoshop", "UI设计", "交互设计",
    "SEO", "SEM", "新媒体运营", "内容运营", "用户运营", "活动运营", "私域运营",
    "项目管理", "PMP", "敏捷开发", "Scrum", "跨部门协作",
]

JD_TITLE_HINTS = re.compile(r"(工程师|开发|架构师|经理|主管|总监|设计师|运营|产品|分析师|专员|顾问|研究员|科学家|测试|运维|算法|前端|后端|全栈|实习生)")

RESP_HEADINGS = ["岗位职责", "工作职责", "职位描述", "工作内容", "职责描述", "你将会做", "你的工作"]
REQ_HEADINGS = ["任职要求", "岗位要求", "职位要求", "任职资格", "我们希望", "能力要求", "任职条件"]
BONUS_HEADINGS = ["加分项", "优先考虑", "满足以下", "有如下", "额外条件"]
COMPANY_HEADINGS = ["公司介绍", "公司简介", "关于我们", "团队介绍"]


def _lines_under(lines: list[str], headings: list[str], stop_headings: list[str]) -> list[str]:
    """取出某个标题下的内容行，直到遇到下一个标题。"""
    collected: list[str] = []
    capturing = False
    for line in lines:
        stripped = _clean(line)
        norm = _norm_heading(stripped)
        if any(norm.startswith(h) for h in headings):
            capturing = True
            continue
        if capturing and any(norm.startswith(s) for s in stop_headings):
            break
        if capturing and stripped:
            collected.append(stripped)
    return collected


def parse_jd_text(text: str) -> ParsedJd:
    text = text or ""
    lines = text.splitlines()
    parsed = ParsedJd.empty()

    all_headings = RESP_HEADINGS + REQ_HEADINGS + BONUS_HEADINGS + COMPANY_HEADINGS

    # --- 岗位名 ---
    explicit = re.search(r"(?:岗位|职位)(?:名称)?[:：]\s*([^\n，。；]{2,40})", text)
    if explicit:
        parsed.title = explicit.group(1).strip()
    else:
        for line in lines[:12]:
            cleaned = _clean(line)
            if 2 <= len(cleaned) <= 30 and JD_TITLE_HINTS.search(cleaned) and not DATE_RANGE_RE.search(cleaned):
                parsed.title = cleaned.strip("：: ")
                break

    # --- 公司 ---
    company = re.search(r"公司[:：]\s*([^\n，。；]{2,40})", text)
    if company:
        parsed.company = company.group(1).strip()

    # --- 经验年限 ---
    years_range = re.search(r"(\d+)\s*[-~—–至到]\s*(\d+)\s*年", text)
    years_min = re.search(r"(\d+)\s*年(?:以上|及以上|\+)", text)
    if years_range:
        parsed.seniority = f"{years_range.group(1)}-{years_range.group(2)}年"
        parsed.experience_years = int(years_range.group(1))
    elif years_min:
        parsed.seniority = f"{years_min.group(1)}年以上"
        parsed.experience_years = int(years_min.group(1))
    elif re.search(r"应届|校招|无经验", text):
        parsed.seniority = "应届/不限"
        parsed.experience_years = 0

    # --- 学历 ---
    edu = re.search(r"(博士|硕士|本科|大专|专科|统招本科)(?:及以上|以上)?", text)
    if edu:
        suffix = "及以上" if "及以上" in edu.group(0) or "以上" in edu.group(0) else ""
        parsed.education = f"{edu.group(1)}{suffix}"

    # --- 职责 ---
    for line in _lines_under(lines, RESP_HEADINGS, all_headings):
        parsed.responsibilities.append(line[:200])

    # --- 技能 ---
    req_lines = _lines_under(lines, REQ_HEADINGS, all_headings)
    bonus_lines = _lines_under(lines, BONUS_HEADINGS, all_headings)
    req_text = "\n".join(req_lines) if req_lines else text
    bonus_text = "\n".join(bonus_lines)

    matched = [tech for tech in TECH_DICT if re.search(re.escape(tech), text, re.IGNORECASE)]
    bonus_matched = {tech for tech in TECH_DICT if re.search(re.escape(tech), bonus_text, re.IGNORECASE)}

    parsed.skills_core = [t for t in matched if t not in bonus_matched][:25]
    parsed.skills_bonus = [t for t in matched if t in bonus_matched][:15]

    if not parsed.skills_core and req_text:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.]{1,20}", req_text):
            if token not in parsed.skills_core:
                parsed.skills_core.append(token)

    # --- 关键词（业务域词汇） ---
    keyword_pool = [
        "高并发", "分布式", "微服务", "中间件", "性能优化", "稳定性", "可用性", "架构设计",
        "数据一致性", "缓存", "消息队列", "容器化", "自动化测试", "代码评审", "监控告警",
        "用户增长", "转化率", "留存", "拉新", "GMV", "ROI", "内容生态", "商业化",
        "需求评审", "原型设计", "数据驱动", "A/B测试", "项目管理", "跨部门协作", "团队协作",
        "英文文档", "开源", "技术选型", "重构", "编码规范",
    ]
    parsed.keywords = [k for k in keyword_pool if k in text][:20]

    if not parsed.title:
        parsed.title = (text.strip().splitlines() or [""])[0][:40]

    return ParsedJd.model_validate(parsed.model_dump())


# --------------------------------------------------------------------------- #
# 岗位族识别（用于知识库 scope/role_family 过滤）
# --------------------------------------------------------------------------- #
ROLE_FAMILY_RULES: list[tuple[str, str]] = [
    ("backend", r"后端|服务端|Java|Go|Golang|Python|PHP|Node|微服务|Spring"),
    ("frontend", r"前端|React|Vue|Angular|Web|H5|小程序|TypeScript"),
    ("algorithm", r"算法|机器学习|深度学习|NLP|CV|推荐|大模型|LLM|数据挖掘"),
    ("data", r"数据分析|数据仓库|大数据|BI|ETL|数仓|Hadoop|Spark|Flink"),
    ("test", r"测试|QA|质量保障|自动化测试"),
    ("ops", r"运维|SRE|DevOps|K8s|Kubernetes|云原生|安全"),
    ("product", r"产品经理|产品设计|产品运营|需求分析"),
    ("design", r"UI|UX|视觉设计|交互设计|平面设计"),
    ("marketing", r"运营|市场|营销|新媒体|增长|品牌|SEO|SEM"),
    ("hr", r"人力资源|招聘|HR|人事"),
    ("finance", r"财务|会计|审计|税务"),
    ("sales", r"销售|客户经理|BD|商务"),
]


def guess_role_family(parsed_jd: dict | ParsedJd | None) -> str:
    if parsed_jd is None:
        return "general"
    data = parsed_jd.model_dump() if isinstance(parsed_jd, ParsedJd) else dict(parsed_jd)
    haystack = " ".join(
        [
            str(data.get("title") or ""),
            " ".join(data.get("skills_core") or []),
            " ".join(data.get("keywords") or []),
            " ".join(data.get("responsibilities") or [])[:500],
        ]
    )
    for family, pattern in ROLE_FAMILY_RULES:
        if re.search(pattern, haystack, re.IGNORECASE):
            return family
    return "general"
