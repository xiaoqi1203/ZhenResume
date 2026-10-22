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
# 日期区间的连接符 —— **必须收录 U+301C WAVE DASH「〜」**。
# 实测：一份 PDF 简历日期写作 `2023-09 〜  至今`，U+301C 不在字符类里 → 整行不被识别为
# 日期区间 → 该行被 `_looks_like_entry_header` 当成普通正文 → 教育经历起止时间丢失、
# 项目名被解析成 `〜`。这不是"排版不规范"，是抽取器把 `~` 映射成了日文波浪线。
DATE_SEP = "\\-~—–－〜～~至到到"
DATE_RANGE_RE = re.compile(rf"({DATE_TOKEN})\s*[{DATE_SEP}]+\s*({DATE_TOKEN})")
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
        return _strip_residual_sep(header), match.group(1).strip(), match.group(2).strip()
    match = SINGLE_DATE_RE.search(line)
    if match:
        header = (line[: match.start()] + " " + line[match.end() :]).strip()
        return _strip_residual_sep(header), match.group(1).strip(), ""
    return _strip_residual_sep(line), "", ""


def _split_project_header(header: str) -> tuple[str, str]:
    """把项目条目头拆成 (项目名, 角色)。

    与 `_split_org_title` 的差别（都是实测踩出来的）：
    - 项目名通常**没有分隔符**，用单空格写成 `网购交易平台 全栈开发`。
      `_split_org_title` 按"两个以上空格"切分，遇到这种写法会整行当项目名
      （`name='网购交易平台 全栈开发'`，role 为空）；
    - 但项目名本身允许含空格（`校园二手交易平台 后端`、`xxx 管理系统`），
      所以不能无条件按第一个空格切。

    判据：**最后一个空格之后的那段像不像"角色/职责"**（2–8 字、纯中文、
    且命中角色词）。像就切开，不像就整行当项目名 —— 宁可 role 为空，
    也不要把项目名切坏（项目名会进报告的标题与 HTML 导出）。
    """
    header = _strip_residual_sep(header)
    if not header:
        return "", ""

    header = re.sub(r"[|｜/／·・]+", "\t", header).strip()
    parts = [p.strip() for p in re.split(r"\t|\s{2,}", header) if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]

    # 单空格写法：`网购交易平台 全栈开发`
    if " " in header:
        head, _, tail = header.rpartition(" ")
        if head.strip() and _looks_like_project_role(tail.strip()):
            return head.strip(), tail.strip()

    return header, ""


# 项目角色词：全栈开发 / 后端 / 前端 / 独立开发 / 负责人 …
_PROJECT_ROLE_RE = re.compile(
    r"^(全栈|前端|后端|服务端|客户端|移动端|测试|运维|算法|数据|产品|设计|独立|主导|负责)?"
    r"(开发|工程师|负责人|成员|实习生|架构|设计|维护|同学|个人项目|团队项目)"
    r"(工程师|开发|人员|实习生)?$"
)


def _looks_like_project_role(text: str) -> bool:
    """判断一段短文本是否像"我在这个项目里的角色"。"""
    if not text or len(text) > 10:
        return False
    if not re.fullmatch(r"[\u4e00-\u9fff、/]{2,10}", text):
        return False
    return bool(_PROJECT_ROLE_RE.match(text))


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


# 剥离日期后残留的纯符号（`〜`、`~`、`-` 等）。它们不是标题，留着会变成项目名。
_RESIDUAL_SEP_RE = re.compile(r"^[\s\-\-—–－〜～~|｜/／·・、,，]+$")


def _strip_residual_sep(text: str) -> str:
    """去掉首尾的连接符/空白，纯符号串直接变成空串。

    实测场景：`2023-09 〜  至今 东北石油大学 软件工程 (本科)` 在日期被剥离后，
    如果 `〜` 没进 DATE_RANGE_RE 的连接符集，标题会剩成 `〜 东北石油大学 …`；
    即使修好了正则，也可能残留 `〜` 之类符号 —— 所以这里统一清一遍。
    """
    cleaned = (text or "").strip()
    if not cleaned or _RESIDUAL_SEP_RE.match(cleaned):
        return ""
    cleaned = re.sub(r"^[\s\-\-—–－〜～~|｜/／·・]+\s*", "", cleaned)
    return re.sub(r"\s*[\s\-\-—–－〜～~|｜/／·・]+$", "", cleaned).strip()


def extract_skills(section_lines: list[str]) -> list[str]:
    """从技能栏文本抽取技能列表。

    两步走，避免把多词技术名拆碎：
    1. 先按 TECH_DICT 抽已知技术名，长词优先（保证得到 "Spring Cloud" 而不是 "Spring"+"Cloud"；
       同时 "MySQL" 命中后 "SQL" 不再单独入列）；
    2. 其余内容按分隔符切分，保留「微服务」「高并发」这类非词表技能 ——
       但**必须剔除整句碎片**。

    ⚠️ 第 2 步的过滤是修 bug 加的（实测代价很大）：简历里常见的分行写法
    `1. 编程语言：熟练掌握 Java，了解 Python`，不做过滤会产出
    `['编程语⾔：熟练掌握', '了解', '基础语法', '进⾏接⼝开发', '缓存穿透防', ...]`。
    这些不是技能，后果有三：
    - 技能数虚高到 40（`min(len(skills), 15)` 之类的展示被垃圾占满）；
    - 「技能 ↔ 项目」一致性检查永远误报（碎片不可能等于 `Vue`）；
    - 前端把它当技能标签渲染，用户第一眼就看到自己的简历被解析成一堆病句。
    所以：含冒号/括号说明的、以动词开头或结尾的、纯泛称的，一律不进技能列表。
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
        if not (2 < len(token) <= 16):
            continue
        if not _looks_like_skill_name(token):
            continue
        low = token.lower()
        if any(low in existing.lower() for existing in canonical):
            continue
        extras.append(token)

    return list(dict.fromkeys(canonical + extras))[:40]


# 技能名里**不该出现**的东西：说明性标点、量词/泛称、动词短语
_SKILL_STOP_RE = re.compile(
    r"[:：（(]|等|熟悉|熟练|了解|掌握|擅长|精通|使用|了解过|基础语法|相关|下列|如下"
)
_SKILL_VERB_PREFIX_RE = re.compile(r"^(熟练|熟悉|了解|掌握|擅长|精通|能够|可以|使用|基于|能|会|进行|实现|具备)")
_SKILL_PROSE_SUFFIX_RE = re.compile(r"(开发|实现|进行|操作|运用|掌握|熟练|了解|熟悉|能力|原理|用法|机制|注解|模型)$")
# 纯泛称：不是技术，也不能算技能条目
_SKILL_GENERIC = {
    "编程语言", "后端框架", "前端框架", "数据存储", "认证与权限", "工具与协作", "其他技术",
    "技能", "专业技能", "技能特长", "语言", "框架", "工具", "数据库", "缓存", "中间件",
}
# 行首编号：`1.` / `1、` / `1)` / `（1）` / `①`
_ENUM_PREFIX_RE = re.compile(r"^\s*(?:[（(]?\d{1,2}[)）.、．]|[①-⑳])\s*")

# 「分组式技能行」的判据：出现冒号，说明是 `编程语言：熟练掌握 Java…` 这种句式。
# 这类行里**只有技术名词值得收**，其余都是表述性文字（前面 `_SKILL_*` 过滤器拦不住
# 被空格切碎的半句，例如 `编程工具辅助代码编写`、`逻辑调`、`提升开发效率`）。
_GROUPED_SKILL_LINE_RE = re.compile(r"[:：]")


def extract_skills(section_lines: list[str]) -> list[str]:
    """从技能栏文本抽取技能列表。

    **分两种书写风格处理**（这是修 bug 的核心，实测代价很大）：

    ① **分组式**：`编程语言：熟练掌握 Java，了解 Python 基础语法；`
       只收 `TECH_DICT` 命中的技术名（+ 少量能力词白名单）。
       为什么不能顺带收切分片段：`1. 编程语言：熟练掌握 Java，了解 Python 基础语法`
       按空格切开后是 `编程语言：熟练掌握` / `Java，了解` / `Python` / `基础语法；`，
       过滤后仍会漏进 `基础语法` 这类半句。只收词表命中是最干净的解法。

    ② **清单式**：`Java、Go、Spring Boot、MySQL、Redis、Kafka`
       没有冒号，整行就是逗号/顿号分隔的清单，可以放心按分隔符切。

    两种风格都先按 `TECH_DICT` 长词优先抽（保证 `Spring Cloud` 不被拆成 `Spring`+`Cloud`，
    `MySQL` 命中后 `SQL` 不入列），再决定要不要收非词表片段。
    """
    cleaned_lines = [l for l in (_clean(_ENUM_PREFIX_RE.sub("", line)) for line in section_lines) if l]
    if not cleaned_lines:
        return []
    text = "\n".join(cleaned_lines)

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
    grouped_lines = [l for l in cleaned_lines if _GROUPED_SKILL_LINE_RE.search(l)]
    list_lines = [l for l in cleaned_lines if not _GROUPED_SKILL_LINE_RE.search(l)]

    # ① 分组式行：只收能力词白名单（`微服务`/`高并发` 这类不在 TECH_DICT 但确属技能的词）
    grouped_text = "\n".join(grouped_lines)
    for word in _CAPABILITY_WORDS:
        if word in grouped_text and word not in canonical and word not in extras:
            extras.append(word)

    # ② 清单式行：按分隔符切分，逐项过滤碎片
    for token in re.split(r"[、,，;；/|｜·\s]+", "\n".join(list_lines)):
        token = token.strip("：:。.（）()")
        if not (2 < len(token) <= 16):
            continue
        if not _looks_like_skill_name(token):
            continue
        low = token.lower()
        if any(low in existing.lower() for existing in canonical):
            continue
        if token in extras:
            continue
        extras.append(token)

    return list(dict.fromkeys(canonical + extras))[:40]


# 不在 TECH_DICT 里、但确实该算技能的「能力词」（分组式技能栏里非常常见）
_CAPABILITY_WORDS = [
    "微服务", "分布式", "高并发", "高可用", "性能优化", "并发编程", "领域驱动设计",
    "数据结构", "算法", "设计模式", "单元测试", "自动化测试", "容器化", "服务治理",
    "消息队列", "负载均衡", "分布式事务", "链路追踪", "监控告警", "CI/CD",
]


def _looks_like_skill_name(token: str) -> bool:
    """判断一个切分片段是否像"技术名/能力名"，而不是整句被切下来的碎片。

    判定顺序（任一命中即判否）：
    1. 纯泛称或分组标题（`编程语言`、`数据存储`）；
    2. 含冒号/括号（`编程语言：熟练掌握`）；
    3. 以动词开头或结尾（`了解`、`进行接口开发`）；
    4. 含"等/相关/如下"这类说明性词。
    """
    token = (token or "").strip()
    if not token:
        return False
    if token in _SKILL_GENERIC:
        return False
    if _SKILL_STOP_RE.search(token):
        return False
    if _SKILL_VERB_PREFIX_RE.match(token):
        return False
    if _SKILL_PROSE_SUFFIX_RE.search(token):
        return False
    return True



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
_LABEL_LINE_RE = re.compile(r"^(?P<label>[^｜|]{2,10})\s*[｜|]\s*(?P<value>.+)$")
_BASIC_LABELS = (
    "姓名",
    "名字",
    "目标岗位",
    "求职意向",
    "应聘岗位",
    "意向岗位",
    "手机",
    "电话",
    "手机号",
    "邮箱",
    "邮箱地址",
    "城市",
    "所在城市",
    "现居城市",
)


def _merge_wrapped_lines(lines: list[str]) -> list[str]:
    """合并被 PDF 换行拆断的同一逻辑行。

    表格里内容列较窄时，`某公司 | 职位 |` 与 `2021.03 - 2024.08` 会分成两行，
    解析器会把日期当成新一段经历的标题，于是同一条经历被拆成两条。
    """
    merged: list[str] = []
    for line in lines:
        # `-`／`~` 结尾通常是日期范围被拆断（`2020.07 -` + `2024.03`）
        if merged and merged[-1].rstrip().endswith(
            ("|", "｜", "、", "，", ",", "；", ";", "-", "–", "—", "~")
        ):
            merged[-1] = merged[-1].rstrip() + " " + line.strip()
        else:
            merged.append(line)
    return merged


def _split_glued_heading(line: str) -> tuple[str, str] | None:
    """`专业技能Java、SpringBoot` → ("专业技能", "Java、SpringBoot")。

    PDF 里标签与内容的列间隔很小时重建不出竖线，两者会直接粘在一起。
    """
    stripped = line.strip()
    candidates: list[tuple[str, str]] = []
    for keywords in SECTION_KEYWORDS.values():
        for keyword in keywords:
            if stripped.startswith(keyword) and len(stripped) > len(keyword) + 1:
                rest = stripped[len(keyword) :].lstrip("：: ｜|，,")
                if len(rest) >= 2:
                    candidates.append((keyword, rest))
    if not candidates:
        return None
    # 长关键词优先：`专业技能` 先于 `技能`
    return max(candidates, key=lambda item: len(item[0]))


def _normalize_label_lines(text: str) -> str:
    """把「标签 ｜ 内容」的表格行改写成解析器认得的格式。

    表格类简历（左列标签 / 右列内容）导出的 PDF，标签与内容在同一行用竖线分隔：
        `工作经历 ｜ 某科技 | 后端开发 | 2021.03 - 2024.08`
        `姓名 ｜ 李四`
    章节切分器只认「独立成行的标题」，这些行会让整份简历滞留在 preamble、
    导致所有字段为空。这里做三件事：
      1. 合并被换行拆断的行；
      2. `标签 ｜ 内容` → 标题行 + 内容行（章节类）或 `标签：内容`（基本信息类）；
      3. `标签内容` 粘连（列间隔太小、重建不出竖线）→ 同样拆开。
    """
    lines = _merge_wrapped_lines((text or "").splitlines())

    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        match = _LABEL_LINE_RE.match(stripped)
        if match:
            label = match.group("label").strip()
            value = match.group("value").strip()
            if is_section_heading(label):
                out.append(label)
                out.append(value)
                continue
            if any(label == basic or label.startswith(basic) for basic in _BASIC_LABELS):
                out.append(f"{label}：{value}")
                continue

        glued = _split_glued_heading(stripped)
        if glued is not None:
            out.append(glued[0])
            out.append(glued[1])
            continue

        out.append(line)
    return "\n".join(out)


def parse_resume_text(text: str) -> ParsedResume:
    """先累积普通 dict/list，最后一次性 model_validate。

    不要往 ParsedResume 的列表里直接 append(dict) —— pydantic 不会校验 append 进去的元素，
    后续代码会拿到 dict 而不是模型实例（曾经在这里踩过 AttributeError）。
    """
    text = _normalize_label_lines(text or "")
    sections = split_sections(text)
    head_lines = [l for l in sections.get("preamble", []) if l.strip()]
    basics_block = [l for l in sections.get("basics", []) if l.strip()]

    basics: dict = {"name": "", "target_title": "", "email": "", "phone": "", "city": "", "links": []}
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

    # --- 目标岗位 ---
    # 优先显式标签（「目标岗位：后端开发工程师」）；
    # 否则从**联系方式行**里取首段：我们自己导出的简历会把「岗位 ｜ 手机 ｜ 邮箱 ｜ 城市 ｜ 链接」
    # 放在同一行，首段就是目标岗位 —— 之前这一行只解析了手机/邮箱/城市，岗位名因此丢失。
    explicit_title = re.search(
        r"(?:目标岗位|求职意向|应聘岗位|意向岗位)[:：]\s*([^\n，。；|｜]{2,30})", text
    )
    if explicit_title:
        basics["target_title"] = explicit_title.group(1).strip()
    else:
        for line in candidate_lines:
            cleaned = _clean(line)
            if not re.search(r"[|｜]", cleaned):
                continue
            if not (EMAIL_RE.search(cleaned) or PHONE_RE.search(cleaned)):
                continue
            for part in re.split(r"[|｜]", cleaned):
                seg = part.strip()
                if not seg or len(seg) > 24:
                    continue
                if EMAIL_RE.search(seg) or PHONE_RE.search(seg):
                    continue
                if any(city in seg for city in CJK_CITY):
                    continue
                if seg == basics.get("name") or re.search(r"\d", seg):
                    continue
                basics["target_title"] = seg
                break
            if basics["target_title"]:
                break


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
        name, role = _split_project_header(entry["header"])
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
            # 丢掉「只有日期、没有学校」的残行：表格简历里日期常被换行拆到下一行，
            # 不清理会多出一条空教育经历（报告里会显示成一条填空）。
            "education": [item for item in education if (item.get("school") or "").strip()],
            "work": [item for item in work if (item.get("company") or "").strip()],
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



# --------------------------------------------------------------------------- #
# JD 中的「择一」关系（或 / 等其中一门 / 之一 / 任一 …）
# --------------------------------------------------------------------------- #
# 实测踩过的两类误报：
#   A. JD 写「熟悉 Java 或 Go」，简历只有 Java → 报「缺少 Go」
#   B. JD 写「熟练掌握 C/C++/Java/Go 等其中一门开发语言」，简历有 Java → 仍报「缺少 Go」
# 这两种都是**择一要求**，命中任意一项即满足。
#
# 识别策略：
#   1) 显式「或 / 或者」：按分隔符切分，两侧各取一个技术词；
#   2) 择一提示词（其中一门 / 之一 / 任一 / 至少一种 / 任选 …）：
#      取该**分句**内出现的全部技术词作为一组（`/`、`、` 连接的自然被收集进来）。
# 不把「、」单独当择一：`Java、Spring、MySQL` 在 JD 里通常是并列（都要）。
_OR_SEG_RE = re.compile(r"[^\n。；;，,]{0,40}?(?:或|或者)[^\n。；;，,]{0,40}")
_OR_SPLIT_RE = re.compile(r"\s*(?:或|或者)\s*")
_CHOICE_HINT_RE = re.compile(
    r"(等其中一门|等其中一种|等其中一项|其中一门|其中一种|其中一项|之一|任一|"
    r"任意一种|任意一项|至少一门|至少一种|任选其一|任选一门|择一)"
)
_CLAUSE_BREAKS = "，,；;。\n"


def _technologies_in(segment: str) -> list[str]:
    """按 TECH_DICT 抽技术名（长词优先，避免 Spring Cloud 退化成 Spring）。"""
    hits = [t for t in TECH_DICT if re.search(re.escape(t), segment or "", re.IGNORECASE)]
    hits.sort(key=len, reverse=True)
    kept: list[str] = []
    for tech in hits:
        low = tech.lower()
        if any(low != other.lower() and low in other.lower() for other in kept):
            continue
        kept.append(tech)
    return list(dict.fromkeys(kept))


def _clause_around(text: str, pos: int) -> str:
    """取 pos 所在的分句（以中文/英文标点或换行分隔）。"""
    start = 0
    for i in range(pos - 1, -1, -1):
        if text[i] in _CLAUSE_BREAKS:
            start = i + 1
            break
    end = len(text)
    for i in range(pos, len(text)):
        if text[i] in _CLAUSE_BREAKS:
            end = i
            break
    return text[start:end]


def _extract_or_groups(text: str) -> list[list[str]]:
    """从 JD 原文里抽出**择一要求**分组，返回如 `[["Java", "Go"]]`。

    同一组只保留一次（大小写不敏感去重）。
    """
    groups: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def push(items: list[str]) -> None:
        items = list(dict.fromkeys(items))
        if len(items) < 2:
            return
        key = tuple(sorted(i.lower() for i in items))
        if key in seen:
            return
        seen.add(key)
        groups.append(items)

    source = text or ""

    # 1) 显式「A 或 B」：按「或」切分，每段取一个技术名
    for match in _OR_SEG_RE.finditer(source):
        parts = [p.strip() for p in _OR_SPLIT_RE.split(match.group(0)) if p.strip()]
        items: list[str] = []
        for part in parts:
            hits = _technologies_in(part)
            if hits:
                items.append(hits[0])
        push(items)

    # 2) 「…等其中一门 / 之一 / 任一 …」：分句内所有技术词视为择一
    for hint in _CHOICE_HINT_RE.finditer(source):
        clause = _clause_around(source, hint.start())
        items = _technologies_in(clause)
        if len(items) < 2:
            # 分句太碎（如「等其中一门开发语言」）时，回退到整行
            line_start = source.rfind("\n", 0, hint.start()) + 1
            line_end = source.find("\n", hint.start())
            if line_end < 0:
                line_end = len(source)
            items = _technologies_in(source[line_start:line_end])
        push(items)

    return groups


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
    parsed.skill_groups = _extract_or_groups(text)

    # --- 业务域关键词 ---
    # 说明：这里的结果既进 `keywords`（业务概念词），也用来**补全 skills_bonus**。
    # 为什么必须这样补：`skills_bonus` 原先只从 `TECH_DICT` 里筛，而 JD 的"加分项"
    # 往往写的是非技术的加分条件（开源贡献、稳定性建设经验、Service Mesh 等），
    # 这些词不在技术词典里 → 实测 7 个 JD 的 `skills_bonus` **全部为空**，
    # 导致"加分项权重"没有作用对象。故改为：加分项段落中出现的业务/能力词一律计入。
    #
    # ⚠️ 但**描述性套话必须排除**（实测发现的设计缺陷）：
    # "大规模""交易系统""稳定性建设"这类词是 JD 里的修饰语，不是可验证的能力项。
    # 若把它们放进要求清单，会出现"JD 多写几个形容词 → 每个候选人都必然缺几项 → 覆盖率与分数反被压低"，
    # 与"分层加权是为了更公平"的初衷相反。因此这里显式排除。
    BONUS_DESCRIPTIVE_NOISE = {
        "大规模", "交易系统", "稳定性建设", "高可用", "业务场景", "相关经验",
    }
    keyword_pool = [
        "高并发", "分布式", "微服务", "中间件", "性能优化", "稳定性", "可用性", "架构设计",
        "数据一致性", "缓存", "消息队列", "容器化", "自动化测试", "代码评审", "监控告警",
        "用户增长", "转化率", "留存", "拉新", "GMV", "ROI", "内容生态", "商业化",
        "需求评审", "原型设计", "数据驱动", "A/B测试", "项目管理", "跨部门协作", "团队协作",
        "英文文档", "开源", "技术选型", "重构", "编码规范",
        # V2.0 补充：高频加分项中**可验证**的能力表述
        "云原生", "Service Mesh", "Istio", "Envoy", "gRPC", "可观测性", "混沌工程",
        "单元测试", "持续集成", "CI/CD", "DevOps",
    ]
    kw_pool = [k for k in keyword_pool if k in text][:20]
    # 加分项段落中的可验证能力词，补进 skills_bonus（去重、去掉已属核心项的、排除套话）
    core_lower = {t.lower() for t in parsed.skills_core}
    for k in keyword_pool:
        if k in BONUS_DESCRIPTIVE_NOISE:
            continue
        if k in bonus_text and k.lower() not in core_lower and k not in parsed.skills_bonus:
            parsed.skills_bonus.append(k)
    parsed.skills_bonus = parsed.skills_bonus[:15]
    parsed.keywords = list(dict.fromkeys(parsed.skills_bonus + kw_pool))[:20]

    # 兜底：若仍无核心技能，从任职要求段落抽英文 token
    if not parsed.skills_core and req_text:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.]{1,20}", req_text):
            if token not in parsed.skills_core:
                parsed.skills_core.append(token)

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
