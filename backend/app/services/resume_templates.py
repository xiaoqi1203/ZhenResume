"""内置简历模板。

**为什么是代码常量而不是数据库表**（与 `services/plans.py` 同理）：
模板的结构（有哪些区块、每栏怎么写）属于产品定义，改动应该走发版与评审，
不应该是运行期可随意漂移的数据。这样也免去了额外建表与种子灌入。
要改成后台可维护，只需把 `TEMPLATES` 换成表查询，接口与前端一行都不用动。

模板里的写作提示不是泛泛的鸡汤，而是从知识库（`services/seed.py` 的
`resume_template` 与 `compliance` 文档）提炼出的可执行规则 —— 所以用户在编辑器里
看到的提示，与诊断报告给出的建议是同一套标准，不会自相矛盾。
"""

from __future__ import annotations

import copy


# 版式元数据的取值域（与前端设计系统 / 渲染器约定一致）：
#   style  —— 整体气质：minimal / business / fresh / creative
#   layout —— 版面骨架：single（单栏）/ sidebar（侧边栏）
#   design —— 具体设计主题：celadon / navy / ivory / cobalt / obsidian / moss / copper / indigo
# 老简历只存了 template_code，打开时要靠这些字段还原样式，所以旧模板同样必须补齐。
STYLES = ("minimal", "business", "fresh", "creative")
LAYOUTS = ("single", "sidebar")
DESIGNS = ("celadon", "navy", "ivory", "cobalt", "obsidian", "moss", "copper", "indigo")


def _sec(
    key: str,
    title: str,
    tip: str,
    examples: list[str],
    *,
    required: bool = False,
    max_items: int | None = None,
) -> dict:
    return {
        "key": key,
        "title": title,
        "required": required,
        "tip": tip,
        "examples": examples,
        "max_items": max_items,
    }


# 所有模板共享的基础规则
COMMON_RULES = [
    "每条经历要点用强动词开头（主导 / 设计 / 重构 / 搭建 / 优化），不要用「负责」「参与」「协助」。",
    "要点结构固定为「动作 + 对象 + 方法 + 量化结果」；没有百分比就写规模、频次或范围。",
    "需要用户补数字的地方先留占位（如 [补充：日均调用量]），不要编造数字 —— 面试会逐条追问。",
    "同一能力只写一次，用目标 JD 的原词表达（JD 写「服务治理」就不要只写「微服务管理」）。",
    "删掉与目标岗位无关的经历与技能；简历控制在 1–2 页。",
    "不要写性别、年龄、婚育、户籍、身高体重、照片等与岗位能力无关的信息。",
    "不要用表格 / 文本框 / 双栏排版正文 —— 这是 ATS 解析失败的头号原因。",
]


TEMPLATES: list[dict] = [
    # ------------------------------------------------------------------ #
    {
        "code": "general_experienced",
        "name": "通用社招模板",
        "description": "适合有 2 年以上正式工作经验的社招投递，重心放在成果与量化",
        "target": "experienced",
        "role_family": "general",
        # 旧模板：保留 code 供老简历回查，但不在画廊展示（visible=False）。
        "style": "business",
        "layout": "single",
        "design": "ivory",
        "accent": "#B08A63",
        "tags": ["社招", "通用", "单栏"],
        "visible": False,
        "intro": (
            "社招看的是「你解决过什么问题、拿到什么结果」。填写顺序建议："
            "先把最近两段工作经历写到有数字，再回头写个人优势与技能 —— "
            "优势应当是经历的结论，而不是先想出来的形容词。"
        ),
        "writing_rules": [
            "工作经历倒序排列，最近 3 年的经历占全文一半以上篇幅。",
            "每段工作写 2–4 条要点，其中至少 2 条含数字。",
            "个人优势控制在 3 行以内：几年经验 + 什么领域 + 最硬的一个成果。",
            *COMMON_RULES,
        ],
        "sections": [
            _sec(
                "basics",
                "个人信息",
                "只写姓名、目标岗位、手机、邮箱、现居城市与作品集链接。目标岗位要与投递的 JD 一致。",
                ["张三 | 后端开发工程师 | 138-0000-0000 | zhangsan@example.com | 现居杭州 | github.com/zhangsan"],
                required=True,
            ),
            _sec(
                "summary",
                "个人优势",
                "判断标准：把这段文字换成任何一个同岗位求职者，如果依然成立，那它就是废话。"
                "只写别人验证得了的事实。",
                ["3 年后端开发经验，专注交易链路高并发场景。主导过日均 200 万次调用的订单服务重构，P99 延迟下降 75%。"],
                max_items=3,
            ),
            _sec(
                "work",
                "工作经历",
                "公司 + 职位 + 起止时间必须齐全，缺时间会直接触发 HR 对空窗期的怀疑。"
                "每条要点补上规模或结果指标。",
                ["主导订单核心接口重构，引入本地缓存与批量查询，支撑日均 200 万次调用，P99 延迟从 480ms 降至 120ms"],
            ),
            _sec(
                "projects",
                "项目经历",
                "挑 2–3 个与目标岗位最相关的项目。写清背景（要解决什么问题）、你的角色、技术方案与最终结果。",
                ["主导容量评估与限流方案设计，支撑大促峰值 8 万 QPS，零故障"],
            ),
            _sec(
                "education",
                "教育经历",
                "学校 + 专业 + 学历 + 起止时间。应届生可补主修课程与绩点排名；社招简单带过即可。",
                ["某某大学 | 计算机科学与技术 | 本科 | 2018.09 - 2022.06"],
            ),
            _sec(
                "skills",
                "专业技能",
                "分组列出（语言 / 框架 / 存储 / 工具），只写经得起追问的。写「熟练使用」而不是「精通」。",
                ["Java、Spring Boot、MySQL、Redis、Kafka、Docker、Kubernetes"],
            ),
            _sec("certificates", "证书", "英语等级、职业资格等可验证的第三方证明。", ["CET-6、软件设计师（中级）"]),
        ],
        "example_content": {
            "basics": {
                "name": "王强",
                "target_title": "后端开发工程师",
                "phone": "137-5555-6666",
                "email": "wangqiang@example.com",
                "city": "杭州",
                "links": ["github.com/wangqiang"],
                # avatar 必须显式给出：`EditorBasics` 有默认值 `""`，
                # 序列化回显时会出现这个键 —— 模板缺了它，"回显内容 == 提交内容"
                # 这条往返断言就永远不成立（契约 §2.3 要求 editor_content 原样回显）。
                "avatar": "",
            },
            "summary": "6 年后端开发经验，专注高并发交易系统与微服务治理。主导过日均 500 万次调用的订单服务重构，P99 延迟下降 78%。",
            "work": [
                {
                    "company": "杭州某科技集团",
                    "title": "高级后端开发工程师",
                    "start": "2021.06",
                    "end": "至今",
                    "location": "杭州",
                    "highlights": [
                        "主导订单核心服务从单体到微服务的拆分，落地 6 个领域服务，部署效率提升 3 倍",
                        "设计并实现分布式事务补偿方案，跨服务数据不一致工单从每月 40 单降至 2 单",
                        "推动 Kubernetes 容器化改造，资源利用率从 25% 提升至 62%，年度服务器成本下降 38 万元",
                    ],
                },
                {
                    "company": "上海某互联网公司",
                    "title": "后端开发工程师",
                    "start": "2018.07",
                    "end": "2021.05",
                    "location": "上海",
                    "highlights": [
                        "负责支付网关开发，日均处理 120 万笔交易，资金差错率低于百万分之一",
                        "优化对账任务，将 4 小时的对账窗口压缩至 25 分钟",
                    ],
                },
            ],
            "projects": [
                {
                    "name": "交易链路稳定性治理",
                    "role": "负责人",
                    "start": "2022.03",
                    "end": "2022.12",
                    "tech": ["Java", "Spring Cloud", "Kubernetes", "Kafka"],
                    "highlights": [
                        "主导容量评估与限流方案设计，支撑大促峰值 8 万 QPS，零故障",
                        "建立故障演练机制，MTTR 从 35 分钟降至 9 分钟",
                    ],
                }
            ],
            "education": [
                {"school": "某某大学", "degree": "硕士", "major": "计算机科学与技术", "start": "2015.09", "end": "2018.06"}
            ],
            "skills": ["Java", "Go", "Spring Boot", "Spring Cloud", "MySQL", "Redis", "Kafka", "Docker", "Kubernetes", "微服务", "高并发", "分布式"],
            "certificates": ["软件设计师（中级）", "CET-6"],
            "awards": [],
            "custom_sections": [
                {"title": "开源贡献", "items": ["xxx-gateway：负责限流模块设计与维护，GitHub 480 star"]}
            ],
        },
    },
    # ------------------------------------------------------------------ #
    {
        "code": "campus_fresh",
        "name": "应届生 / 校招模板",
        "description": "适合无正式工作经验的应届生，用实习、项目、竞赛与课程填补经历空白",
        "target": "campus",
        "role_family": "general",
        "style": "fresh",
        "layout": "single",
        "design": "cobalt",
        "accent": "#1F4E9C",
        "tags": ["校招", "实习", "单栏"],
        "visible": False,
        "intro": (
            "校招 HR 清楚你没有工作经历，他们看的是「学习能力与动手证据」。"
            "把教育经历提到最前，然后用实习、课程项目、竞赛、开源把版面填满 —— "
            "哪怕是一个课程设计，只要写清了你的具体贡献与结果，就比「熟悉 XX 技术」有说服力。"
        ),
        "writing_rules": [
            "教育经历放在最前面，补上主修课程与绩点排名（若排名靠前）。",
            "没有实习就用课程项目、竞赛、开源、社团活动顶上，重点是「你做了什么」而不是「项目是什么」。",
            "不要写「熟悉 Office」「学习能力强」这类无法验证的内容。",
            "如果实习内容偏杂，挑与目标岗位最相关的 2 件事写透，其余一笔带过。",
            *COMMON_RULES,
        ],
        "sections": [
            _sec(
                "basics",
                "个人信息",
                "应届生建议在目标岗位后补上求职意向城市与到岗时间，HR 很在意这一点。",
                ["李娜 | 数据分析师 | 139-0000-0000 | lina@example.com | 现居杭州 | 2026 年 6 月毕业"],
                required=True,
            ),
            _sec(
                "education",
                "教育经历",
                "学校 + 专业 + 学历 + 时间，再补主修课程与绩点排名。这是校招的第一权重项。",
                ["某某大学 | 统计学 | 本科 | 2022.09 - 2026.06 | 绩点 3.7/4.0（专业前 15%）| 主修：概率论、回归分析、Python 数据分析"],
                required=True,
            ),
            _sec(
                "summary",
                "个人优势",
                "用一句话把自己最强的证据说出来：实习做了什么 + 一个具体成果。",
                ["统计学本科，专业前 15%。在电商公司实习期间独立完成用户流失归因分析，输出的挽回策略使次月流失率下降 4.2 个百分点。"],
                max_items=3,
            ),
            _sec(
                "work",
                "实习经历",
                "即使是打杂的实习，也要写出你负责的具体环节与产出。没有产出就写清处理的数据量或覆盖范围。",
                ["独立完成 12 万条用户行为数据清洗与标签构建，将分析前期准备时间从 2 天压缩至 3 小时"],
            ),
            _sec(
                "projects",
                "项目 / 竞赛经历",
                "课程设计、竞赛、开源都算。写清你的角色、用了什么方法、最终拿到什么排名或效果。",
                ["全国大学生数学建模竞赛 | 队长 | 2024.09 | 负责模型构建与论文撰写，获省级二等奖"],
            ),
            _sec(
                "skills",
                "专业技能",
                "按「工具 + 熟练度 + 使用场景」写，不要只罗列名词。",
                ["Python（pandas / numpy，能独立完成数据清洗与建模）、SQL（熟练编写多表关联与窗口函数）、Tableau"],
            ),
            _sec(
                "awards",
                "荣誉奖项",
                "奖学金、竞赛名次、荣誉称号。写明获奖比例（如前 5%），否则含金量看不出来。",
                ["校级一等奖学金（2023、2024，专业前 5%）"],
            ),
            _sec("certificates", "证书", "英语等级、职业资格、技能认证。", ["CET-6（562 分）"]),
        ],
        "example_content": {
            "basics": {
                "name": "李娜",
                "target_title": "数据分析师",
                "phone": "139-0000-0000",
                "email": "lina@example.com",
                "city": "杭州",
                "links": [],
                "avatar": "",
            },
            "summary": "统计学本科，专业前 15%。在电商公司实习期间独立完成用户流失归因分析，输出的挽回策略使次月流失率下降 4.2 个百分点。",
            "work": [
                {
                    "company": "杭州某电商有限公司",
                    "title": "数据分析实习生",
                    "start": "2025.07",
                    "end": "2025.12",
                    "location": "杭州",
                    "highlights": [
                        "独立完成 12 万条用户行为数据清洗与标签构建，将分析前期准备时间从 2 天压缩至 3 小时",
                        "搭建用户流失归因模型（逻辑回归 + 特征重要性），定位出 3 个关键流失节点，挽回策略上线后次月流失率下降 4.2 个百分点",
                    ],
                }
            ],
            "projects": [
                {
                    "name": "全国大学生数学建模竞赛",
                    "role": "队长",
                    "start": "2024.09",
                    "end": "2024.09",
                    "tech": ["Python", "MATLAB"],
                    "highlights": ["负责模型构建与论文撰写，在 3 天内完成选题建模与 20 页论文，获省级二等奖"],
                }
            ],
            "education": [
                {
                    "school": "某某大学",
                    "degree": "本科",
                    "major": "统计学",
                    "start": "2022.09",
                    "end": "2026.06",
                }
            ],
            "skills": ["Python（pandas / numpy）", "SQL（多表关联、窗口函数）", "Tableau", "回归分析", "A/B 测试"],
            "certificates": ["CET-6（562 分）"],
            "awards": ["校级一等奖学金（2023、2024，专业前 5%）"],
            "custom_sections": [
                {"title": "校园经历", "items": ["院学生会学习部干事，组织 3 场专业技能分享会，累计参与 200 余人次"]}
            ],
        },
    },
    # ------------------------------------------------------------------ #
    {
        "code": "career_switch",
        "name": "转行求职模板",
        "description": "适合跨行业 / 跨岗位转行，把可迁移能力提到最前，并正面处理转行动机",
        "target": "transition",
        "role_family": "general",
        "style": "business",
        "layout": "single",
        "design": "navy",
        "accent": "#17324D",
        "tags": ["转行", "通用", "单栏"],
        "visible": False,
        "intro": (
            "转行最大的风险不是「没有相关经验」，而是 HR 看不出你已经为新岗位做过什么准备。"
            "因此这份模板把「技能」与「项目 / 作品」提到工作经历之前：先用自学成果与作品证明能力，"
            "再倒序写原行业经历，并在个人优势里用一句话回应转行动机。"
        ),
        "writing_rules": [
            "个人优势里必须有一句正面回应「为什么转行」，不要回避。",
            "把新岗位相关的能力、作品、证书放在靠前位置，抓住 HR 前 30 秒的注意力。",
            "原行业经历要用新岗位的语言重写：同样是「管门店」，对运营岗就是「0-1 搭建单店经营模型」。",
            "不要否定自己的过去，把原行业经验写成新岗位的独特优势（行业理解、资源、抗压）。",
            "主动补一个「项目 / 作品」区块：转行者最有说服力的是拿得出手的作品。",
            *COMMON_RULES,
        ],
        "sections": [
            _sec(
                "basics",
                "个人信息",
                "目标岗位要写得非常具体。转行者最忌目标模糊（「想找运营相关工作」）。",
                ["赵敏 | 用户运营（从线下零售转行） | 136-0000-0000 | zhaomin@example.com | 现居上海"],
                required=True,
            ),
            _sec(
                "summary",
                "个人优势",
                "三段式：原行业积累 → 转入新岗位的证据 → 为什么是你。转行动机要写得诚实且积极。",
                ["5 年线下连锁零售经验，管理过 3 家门店、12 人团队。近 1 年系统自学用户运营与数据分析，独立搭建的会员分层运营方案使用户月复购率提升 18%。转行方向明确，希望把门店经营经验用于线上用户运营。"],
                max_items=3,
            ),
            _sec(
                "skills",
                "相关技能",
                "只列与目标岗位直接相关的技能，并用「使用场景」证明，不要罗列名词。",
                ["用户分层与生命周期管理（独立搭建过 RFM 模型）、SQL（能写多表关联查询）、Excel 数据透视与可视化"],
                required=True,
            ),
            _sec(
                "projects",
                "项目 / 作品",
                "转行者的核心说服力所在。哪怕是自学的个人项目也要写清目标、方法、结果。",
                ["搭建会员分层运营方案（个人项目）：用 RFM 模型将 2000 名会员分为 5 层，针对高价值流失层设计挽回策略，试点用户月复购率提升 18%"],
                required=True,
            ),
            _sec(
                "work",
                "工作经历",
                "用新岗位的语言重写旧经历，突出可迁移能力；不要只描述原来的岗位职责。",
                ["管理 3 家门店共 12 人团队，搭建单店经营分析表，将月度盘点耗时从 2 天缩短至 4 小时"],
            ),
            _sec(
                "education",
                "教育经历",
                "写学校、专业、学历与时间即可。",
                ["某某学院 | 市场营销 | 大专 | 2015.09 - 2018.06"],
            ),
            _sec(
                "certificates",
                "证书 / 学习经历",
                "转行者要把「为新岗位做过的准备」写出来：课程、认证、训练营都算证据。",
                ["Google 数据分析专业证书（Coursera，2025）、某训练营收官项目优秀学员（前 10%）"],
            ),
        ],
        "example_content": {
            "basics": {
                "name": "赵敏",
                "target_title": "用户运营",
                "phone": "136-0000-0000",
                "email": "zhaomin@example.com",
                "city": "上海",
                "links": [],
                "avatar": "",
            },
            "summary": "5 年线下连锁零售经验，管理过 3 家门店、12 人团队。近 1 年系统自学用户运营与数据分析，独立搭建的会员分层方案使用户月复购率提升 18%。转行方向明确，希望把门店经营经验用于线上用户运营。",
            "work": [
                {
                    "company": "某某连锁零售有限公司",
                    "title": "门店店长",
                    "start": "2020.03",
                    "end": "至今",
                    "location": "上海",
                    "highlights": [
                        "管理 3 家门店共 12 人团队，搭建单店经营分析表，将月度盘点耗时从 2 天缩短至 4 小时",
                        "负责 2000 名会员的线下维护，复购率长期高于区域平均水平 8 个百分点",
                    ],
                },
                {
                    "company": "某某商贸有限公司",
                    "title": "门店运营专员",
                    "start": "2018.07",
                    "end": "2020.02",
                    "location": "上海",
                    "highlights": ["负责 2 家门店的日常运营与库存管理，年度损耗率控制在 1.2% 以内"],
                },
            ],
            "projects": [
                {
                    "name": "会员分层运营方案（个人项目）",
                    "role": "独立完成",
                    "start": "2025.03",
                    "end": "2025.09",
                    "tech": ["Excel", "SQL", "RFM 模型"],
                    "highlights": [
                        "用 RFM 模型将 2000 名会员分为 5 层，针对高价值流失层设计挽回策略，试点用户月复购率提升 18%",
                    ],
                }
            ],
            "education": [{"school": "某某学院", "degree": "大专", "major": "市场营销", "start": "2015.09", "end": "2018.06"}],
            "skills": ["用户分层与生命周期管理", "RFM 模型", "SQL", "Excel 数据透视", "活动策划与执行"],
            "certificates": ["Google 数据分析专业证书（Coursera，2025）"],
            "awards": [],
            "custom_sections": [],
        },
    },
    # ------------------------------------------------------------------ #
    {
        "code": "tech_engineer",
        "name": "技术岗强化模板",
        "description": "适合研发 / 算法 / 测试 / 运维等技术岗，技能前置、突出技术深度与开源",
        "target": "experienced",
        "role_family": "tech",
        "style": "minimal",
        "layout": "single",
        "design": "celadon",
        "accent": "#5D7F6B",
        "tags": ["社招", "技术", "单栏"],
        "visible": False,
        "intro": (
            "技术岗的简历要能扛住面试官的追问。写上去的每一项技术，都要能说清「用它解决了什么问题、"
            "遇到什么坑、怎么选的型」。因此这份模板把技能栏前置，并单独留出「开源与技术输出」区块 —— "
            "可验证的第三方证据（star 数、线上文章、专利）在技术岗筛选里权重很高。"
        ),
        "writing_rules": [
            "技能栏前置，只写经得起追问的技术；写「熟练使用」而不是「精通」。",
            "技术岗的量化维度优先选：QPS / P99 / 可用性 / 资源成本 / 构建耗时 / 覆盖率。",
            "项目经历必须写清技术选型理由与遇到的难点，不要只写业务功能。",
            "不要堆砌技术名词。技能栏里出现的每一项，都应在项目描述中有对应的使用场景。",
            *COMMON_RULES,
        ],
        "sections": [
            _sec(
                "basics",
                "个人信息",
                "技术岗建议附上 GitHub / 博客 / 掘金等可验证的技术主页。",
                ["陈磊 | 高级后端开发工程师 | 135-0000-0000 | chenlei@example.com | 现居北京 | github.com/chenlei"],
                required=True,
            ),
            _sec(
                "skills",
                "专业技能",
                "按「语言 / 框架 / 存储 / 中间件 / 工具」分组，与项目描述保持一致。",
                ["语言：Java、Go｜框架：Spring Boot、Spring Cloud｜存储：MySQL、Redis、Elasticsearch｜中间件：Kafka、RocketMQ｜工具：Docker、Kubernetes、Jenkins"],
                required=True,
            ),
            _sec(
                "summary",
                "个人优势",
                "一句话说清技术方向与最硬的技术成果，避免「热爱技术」这类无信息量的表述。",
                ["7 年 Java 后端经验，专注高并发交易系统与稳定性建设。主导过支撑 8 万 QPS 峰值的大促保障方案，连续两年零故障。"],
                max_items=3,
            ),
            _sec(
                "work",
                "工作经历",
                "每段经历挑 2–4 个技术含量最高的事情写，附上技术指标与你的具体贡献。",
                ["主导订单服务拆分，引入本地缓存 + 批量查询 + 异步化，接口 P99 从 480ms 降至 120ms"],
            ),
            _sec(
                "projects",
                "项目经历",
                "写清背景、技术选型理由、你的角色、遇到的难点与最终结果。难点是面试的抓手。",
                ["重建全链路压测体系：用影子表隔离压测流量，定位出 3 个容量瓶颈，大促前完成扩容，峰值 8 万 QPS 零故障"],
            ),
            _sec(
                "education",
                "教育经历",
                "学校 + 专业 + 学历 + 时间。",
                ["某某大学 | 软件工程 | 本科 | 2014.09 - 2018.06"],
            ),
            _sec(
                "awards",
                "开源与技术输出",
                "可验证的第三方证据：开源项目、技术文章、专利、内部分享。写明你的具体贡献与量化结果。",
                ["开源项目 xxx-gateway：个人项目，负责限流与熔断模块，GitHub 620 star"],
            ),
            _sec("certificates", "证书", "云厂商认证、职业资格等。", ["阿里云 ACP 认证（2023）"]),
        ],
        "example_content": {
            "basics": {
                "name": "陈磊",
                "target_title": "高级后端开发工程师",
                "phone": "135-0000-0000",
                "email": "chenlei@example.com",
                "city": "北京",
                "links": ["github.com/chenlei"],
                "avatar": "",
            },
            "summary": "7 年 Java 后端经验，专注高并发交易系统与稳定性建设。主导过支撑 8 万 QPS 峰值的大促保障方案，连续两年零故障。",
            "work": [
                {
                    "company": "北京某科技有限公司",
                    "title": "高级后端开发工程师",
                    "start": "2020.04",
                    "end": "至今",
                    "location": "北京",
                    "highlights": [
                        "主导订单服务拆分，引入本地缓存 + 批量查询 + 异步化，接口 P99 从 480ms 降至 120ms",
                        "重建全链路压测体系，用影子表隔离压测流量，定位 3 个容量瓶颈并完成扩容，大促峰值 8 万 QPS 零故障",
                        "推动服务容器化与 CI/CD 改造，发布耗时从 25 分钟降至 6 分钟",
                    ],
                },
                {
                    "company": "北京某网络科技有限公司",
                    "title": "Java 开发工程师",
                    "start": "2018.07",
                    "end": "2020.03",
                    "location": "北京",
                    "highlights": ["负责用户中心服务开发，服务 500 万注册用户，接口可用性 99.98%"],
                },
            ],
            "projects": [
                {
                    "name": "xxx-gateway 开源网关",
                    "role": "作者",
                    "start": "2022.01",
                    "end": "至今",
                    "tech": ["Go", "Redis", "Kubernetes"],
                    "highlights": [
                        "实现基于令牌桶的分布式限流与熔断模块，GitHub 620 star，被 3 家公司用于生产环境",
                    ],
                }
            ],
            "education": [{"school": "某某大学", "degree": "本科", "major": "软件工程", "start": "2014.09", "end": "2018.06"}],
            "skills": ["Java", "Go", "Spring Boot", "Spring Cloud", "MySQL", "Redis", "Elasticsearch", "Kafka", "RocketMQ", "Docker", "Kubernetes", "Jenkins"],
            "certificates": ["阿里云 ACP 认证（2023）"],
            "awards": ["公司年度技术之星（2023，获奖比例 2%）"],
            "custom_sections": [
                {"title": "技术输出", "items": ["掘金专栏《高并发交易系统实战》12 篇，累计阅读 8 万+"]}
            ],
        },
    },
]

TEMPLATE_MAP: dict[str, dict] = {t["code"]: t for t in TEMPLATES}


# --------------------------------------------------------------------------- #
# 第一批真实版式模板
# --------------------------------------------------------------------------- #
# 这 4 套与旧 4 套是「同一套写作方法论的不同版式」：内容骨架（sections /
# writing_rules / example_content）直接 deepcopy 自对应 base，只覆盖面向用户的
# 文案与版式元数据。这样写作提示只有一处维护点，改了 base 不会漏掉衍生模板。
def _derive_template(
    base_code: str,
    *,
    code: str,
    name: str,
    description: str,
    intro: str,
    style: str,
    layout: str,
    design: str,
    accent: str,
    tags: list[str],
    target: str,
    role_family: str,
) -> dict:
    """从基础模板派生一套画廊可见的新版式模板。"""
    if style not in STYLES or layout not in LAYOUTS or design not in DESIGNS:
        raise ValueError(f"非法的版式元数据：{code} style={style} layout={layout} design={design}")

    template = copy.deepcopy(TEMPLATE_MAP[base_code])
    template.update(
        {
            "code": code,
            "name": name,
            "description": description,
            "intro": intro,
            "target": target,
            "role_family": role_family,
            "style": style,
            "layout": layout,
            "design": design,
            "accent": accent,
            "tags": list(tags),
            "visible": True,
        }
    )

    # 契约 §2.3：example_content 必须原样回显。`EditorBasics.avatar` 有默认值 `""`，
    # 序列化回显会带上这个键 —— 模板缺了它，"回显内容 == 提交内容" 的往返断言就
    # 永远不成立。旧模板都显式带了这个键，这里再做一次兜底（deepcopy 会一并带过来）。
    example = template.get("example_content") or {}
    basics = example.get("basics") or {}
    if "avatar" not in basics:
        basics["avatar"] = ""
        example["basics"] = basics
        template["example_content"] = example

    return template


VISIBLE_TEMPLATES: list[dict] = [
    _derive_template(
        "tech_engineer",
        code="celadon_line",
        name="青简细线",
        description="细线单栏版式：技能前置、留白克制，适合社招研发 / 算法 / 测试 / 运维投递",
        intro=(
            "青灰细线纵贯全页，没有色块也没有多余的装饰，整份简历像一份装订干净的实验记录。"
            "技术岗的成果本来就足够密，这套版式只负责让视线顺着细线一路落到你的指标上。"
        ),
        style="minimal",
        layout="single",
        design="celadon",
        accent="#5D7F6B",
        tags=["社招", "技术", "单栏"],
        target="experienced",
        role_family="tech",
    ),
    _derive_template(
        "general_experienced",
        code="navy_chapter",
        name="深蓝序章",
        description="深蓝单栏版式：突出商业判断与协作边界，适合社招产品 / 项目岗投递",
        intro=(
            "深蓝色的标题像章节起首，把每一段经历压成一页可读的章节。"
            "产品与项目岗需要让面试官看清背景、判断与结果之间的因果，这套版式把节奏留给段落之间的呼吸。"
        ),
        style="business",
        layout="single",
        design="navy",
        accent="#17324D",
        tags=["社招", "产品", "单栏"],
        target="experienced",
        role_family="general",
    ),
    _derive_template(
        "campus_fresh",
        code="ivory_page",
        name="象牙书页",
        description="象牙单栏版式：版面舒展、轻装上阵，适合校招 / 实习投递",
        intro=(
            "象牙底色衬着细衬线的标题，翻开像一页校园手账。"
            "实习、课程与竞赛本就零散，这套版式用充足的留白把它们收拢成从容的一页，不让任何一段积累显得单薄。"
        ),
        style="fresh",
        layout="single",
        design="ivory",
        accent="#B08A63",
        tags=["校招", "实习", "单栏"],
        target="campus",
        role_family="general",
    ),
    _derive_template(
        "career_switch",
        code="cobalt_sail",
        name="钴蓝横帆",
        description="钴蓝单栏版式：用横向节奏理清转行叙事，适合跨行业 / 跨岗位转行",
        intro=(
            "钴蓝横线贯穿整页，像一张被风撑满的帆。"
            "转行简历最怕叙事散乱，这套版式用同一条横线把「旧行业积累 → 新岗位证据 → 转行动机」串成一条看得懂的航线。"
        ),
        style="business",
        layout="single",
        design="cobalt",
        accent="#1F4E9C",
        tags=["社招", "运营", "单栏"],
        target="transition",
        role_family="general",
    ),
]

TEMPLATES.extend(VISIBLE_TEMPLATES)
TEMPLATE_MAP.update({t["code"]: t for t in VISIBLE_TEMPLATES})


def visible_templates() -> list[dict]:
    """画廊展示的模板。

    旧 4 套（general_experienced / campus_fresh / career_switch / tech_engineer）
    `visible=False`：仍然保留在 `TEMPLATE_MAP` 里，老简历按 code 打开时样式不丢，
    但不再出现在列表接口中。
    """
    return [t for t in TEMPLATES if t.get("visible")]


def get_template(code: str) -> dict | None:
    return TEMPLATE_MAP.get(code)


def summarize(template: dict) -> dict:
    """列表接口用的摘要（不带 sections / example_content，避免响应过大）。"""
    return {
        "code": template["code"],
        "name": template["name"],
        "description": template["description"],
        "target": template["target"],
        "role_family": template["role_family"],
        "section_count": len(template["sections"]),
        "style": template["style"],
        "layout": template["layout"],
        "design": template["design"],
        "accent": template["accent"],
        "tags": list(template["tags"]),
    }


def detail(template: dict) -> dict:
    """详情接口用的完整结构。"""
    return {
        **summarize(template),
        "intro": template["intro"],
        "writing_rules": template["writing_rules"],
        "sections": template["sections"],
        "example_content": template["example_content"],
    }
