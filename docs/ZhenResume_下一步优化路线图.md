# ZhenResume 下一步优化路线图：接下来实现什么、修改什么

> 前提：**基本功能已完成** —— `python -m scripts.verify` 实测 **174/174 全绿**（mock 模式）。
> 本文只回答一个问题：**在这个基线上，下一步做什么最值。**
> 排序原则：先让"已经坏了/从没被验证过"的变成可靠，再让"用户能感知的质量"变好，最后才是工程化与体验。

> ### 决策更新（本轮）
> 1. **收费相关整体暂缓**：`P0-3 支付验签与幂等`、`P3-1 真实登录`、`P3-3 分享权限` 从当前排期移除，等基本功能完善后再做。原 P0-3 降级为「已知未实现」，不再占用 Sprint；**但支付是唯一会挡"对外收钱"的项，一旦要上线收费，它必须回到 P0**。
> 2. **LLM 多供应商降级链已落地并实测通过**（见 §0.3）——不再需要"以后接多模型"这个占位项。
> 3. **embedding 模型已探明可用**（阿里云百炼 `text-embedding-v4` / `v3` / `v2` / `v1` 全部可用），所以 P1-1「换真实语义向量」从"设想"变成"只差实施"。

---

## 0. 先做一次"真实性体检"（半天到 1 天，必须先做）

> ### ✅ 体检已完成，H0 的三个失败已修（2026-10-15 实测）
> **结果：live 模式 174/174 通过**（此前 171/174）；mock 仍 174/174；LangGraph 串行 / 并行+checkpoint 也都是 174/174。
> 本轮 live 数据：**平均 127.6 秒/份**（此前 147s）、**¥0.064/份**、token 29,241。
>
> **修了什么（对应下面 §0.4 的三个失败）**
> 1. **评分与展示解耦**：新增 `analyze.rule_baseline_findings()` + `compute_scores_deterministic()` ——
>    评分只吃**规则层基线（纯函数）**，LLM 的增删再也影响不到分数。展示仍走完整流水线。
>    → 「同一简历两次诊断分数一致」在 live 下成立。
> 2. **关键词引文校正**：模型自己"发明"的关键词缺失条目，引文常写成"已覆盖技能列表"
>    （实测：标题「缺失核心关键词 Java 及 Spring 生态」而引文是 `Python、Go、MySQL…`）。
>    现在按标题里的缺失关键词**校正引文**（取最长匹配，避免 Java/JavaScript 误判）。
>    → 「关键词类发现项的引文是关键词本身」在 live 下成立。
> 3. **规则层条目一票保留**：`suggestion` 原先"LLM 为主 + 只抢救红色"，导致黄色的
>    「时间线空窗」、绿色的「已主动说明空窗期」被挤掉。现在规则层条目**全部保留且保留原文措辞**，
>    LLM 只补充不重复的条目。
>    → 「该样本触发时间线空窗检测」「已说明空窗期转为正面条目（green）」成立。
> 4. `datetime.now()` 收口到 `analyze.now_utc()`（可注入），跨天复跑可验证。
>
> **过程中挖出两个只在真实模型下才暴露的严重缺陷（mock 永远发现不了）**
> | 缺陷 | 现象 | 修法 |
> |---|---|---|
> | qc 的 LLM 把**所有 JD 引文的红色条目删掉**，理由"引文『Java』不在简历原文里" | 报告一条红色都不剩（「存在红色风险」当场失败） | 规则层已判定引文可在**简历/JD/知识库**任一处定位的条目，**模型不得 drop**（驳回并记录 `llm_vetoed`）；同时把 JD 原文也放进质检提示词 |
> | qc 的 LLM 把**所有红色条目降级**，理由"改写句是 `[补充：…]` 占位符，不可直接复制" | 红色全变黄色 | 占位符是**设计要求**（缺数据禁止编造），提示词里写明；并且**规则层条目的严重度不由模型改** |
>
> ⚠️ 需要产品上确认的一条：现在 **LLM 对规则层条目只能"补充"，不能"删除/降级"**。
> 这是可复现性与回归稳定性的代价 —— 代价是模型不能再纠正规则层的误判（规则层的误判只能靠改规则解决）。


### 0.1 一个容易被忽略的事实

174 项全绿，但那次跑出来的是：

```
token 0 ｜ 总成本 ¥0.0 ｜ 单份 ¥0.0 ｜ 平均耗时 25ms/步
```

因为它跑在 **mock 模式**（没有 `DEEPSEEK_API_KEY`）。于是 `aaa.md §5` 里三条最关键的验收项**实际上从未被验证过**：

| 验收项 | 现状 | 为什么是空的 |
|---|---|---|
| 单份报告 API 成本 ≤ 0.5 元 | `verify.py:310` PASS | mock 模式下 tokens 恒为 0，成本恒为 0，断言恒成立 |
| 上传后 60 秒内出报告 | 看板 `avg_latency_ms` | mock 模式 25ms/步，与真实 LLM 延迟无关 |
| 同一简历两次诊断分数一致 | `verify.py:243` PASS | 同一天、同一进程、无 LLM 方差 |

**结论：在没有一份真实数据之前，任何"优化优先级"都是猜的。** 所以第一步是：

```powershell
# 1) 配好真实 Key，跑一次完整的端到端
cd E:\ZhenResume\backend
Copy-Item .env.example .env   # 填 DEEPSEEK_API_KEY=sk-xxxx
.\.venv\Scripts\python.exe -m scripts.verify
```

```sql
-- 2) 把真实成本/延迟按步骤挖出来（workflow_steps 里本来就有，不需要额外埋点）
SELECT step_name,
       COUNT(*)               AS calls,
       ROUND(AVG(latency_ms)) AS avg_ms,
       MAX(latency_ms)        AS max_ms,
       SUM(tokens)            AS tokens,
       ROUND(SUM(cost), 4)    AS cost
FROM workflow_steps
GROUP BY step_name
ORDER BY avg_ms DESC;

-- 3) 端到端真实耗时（SQLite 口径）
SELECT ROUND(AVG((julianday(completed_at) - julianday(created_at)) * 86400), 1) AS avg_seconds
FROM analysis_tasks WHERE completed_at IS NOT NULL;
```

### 0.2 三种可能的结果与对应动作

| 体检结果 | 说明 | 立即动作 |
|---|---|---|
| **A. 端到端 > 60 秒** | 8 个节点串行 8 次 LLM 调用是主要耗时 | **并行 ats/impact/risk 从"优化项"升级为"验收必需项"** |
| **B. 单份成本 > 0.5 元** | prompt 偏长（`impact`/`suggestion` 会把简历摘要塞进 prompt） | 压缩 prompt、按需检索、降级到更便宜的模型分层 |
| **C. 同一简历连测 3 次分数不一致** | LLM 产出的 findings 会影响 `compute_scores` → 卖点"改完再测对比分数"失真 | 见 §2.4（分离"确定性发现项"与"LLM 补充发现项"） |

> 建议把这一次体检的结果连同 SQL 输出归档到 `docs/`，作为后续所有优化的基线。**没有基线的优化等于没做。**

### 0.3 已完成：多供应商降级链落地 + 连通性实测

代码已改完（`config.py` 的供应商链、`services/llm.py` 的两层降级、`services/cost.py` 的分层单价、`health.llm_chain`、`scripts/probe_llm.py` 体检脚本）。`python -m scripts.probe_llm --embeddings` 的实测结果：

| 层 | 模型 | 可用 | 探针延迟 | 备注 |
|---|---|---|---|---|
| aliyun（第一层） | `qwen3.8-max` | ✅ | 3529 ms | **链头，线上默认用它** |
| aliyun | `deepseek-v4-pro-0813` | ✅ | 1903 ms | 同层模型阶梯（第 1 备） |
| aliyun | `qwen3.7-flash` | ✅ | 5668 ms | 同层模型阶梯（第 2 备） |
| aliyun | `qwen3.7-flash-2026-07-15` | ✅ | 6490 ms | 同层模型阶梯（第 3 备） |
| deepseek（第二层） | `deepseek-flash` | ✅ | 2839 ms | **整个阿里云层挂了才会用它** |

- 5/5 可用，`response_format=json_object` 全部返回合法 JSON（`_extract_json` 容错层未被触发）。
- 探针单次延迟 **平均 4.1 秒**（提示词只有几十 token；真实节点是几千 token，只会更慢）。
- **关键推论**：按"8 节点串行 × 4.1 秒"外推下限 ≈ **33 秒**，而真实提示词更大 —— **60 秒验收项的余量并不充裕**。这把"三路并行"从"优化项"提升为"保险项"。
- **embedding 探测**：`text-embedding-v4`(1024 维)、`text-embedding-v3`(1024)、`text-embedding-v2`(1536)、`text-embedding-v1`(1536) **全部可用**。现有 `embedding_dim=1536` 与 `v2/v1` 同维，`v4` 需要同步改 `EMBEDDING_DIM=1024` 并重建索引。
- **代码侧也已就绪**：`EMBEDDING_PROVIDER=http` 的批量向量化已实现（默认仍是 `hash`，所以不影响现有 174 项），并且**维度不一致会直接报错并提示正确维度**，不会静默回落成"检索全 0 分"。

#### 换真实语义向量到底值多少：实测对照

同一对词在新旧两种向量下的余弦相似度（`min_score=0.05`，低于它直接被丢弃）：

| 词对 | 现在的哈希向量 | `text-embedding-v4` | 结论 |
|---|---|---|---|
| 优化慢查询 vs 性能优化 | 0.378 | **0.674** | 哈希能勉强召回 |
| 高并发 vs 大流量支撑 | **0.000** | **0.626** | 哈希**完全召不回**（无共同字/词） |
| 课程项目 vs 实习经历 | **0.000** | 0.346 | 哈希**完全召不回** |

> 这是"P1-1 检索升级"值得做的**硬证据**：只要 JD 与简历用了不同说法（这正是真实求职场景的常态），本地哈希向量给出的分数是 **0.0**，低于阈值被直接丢弃 —— 知识库再全，也永远检索不到。换成语义向量后同样的词对是 0.35–0.67，能正常进入候选。

> ⚠️ **成本数字还不可信**：上面每个模型的单价按通用值 `¥2 / ¥8`（每百万 token）折算，而阿里云各型号真实单价不同。**要把「单份 ≤ 0.5 元」变成可信数字，必须先在 `.env` 里按型号填 `ALIYUN_PRICE_*` / `DEEPSEEK_PRICE_*`**，否则看板算的是假账。

### 0.4 体检结果（已实测）—— 本文最有价值的一节

**结论先说：mock 模式 174/174 全绿，live 模式实测 171/174。失败的 3 项，正好就是 §0.1 里"从没被验证过"的那几条假设。**

实测环境：阿里云百炼 `qwen3.8-max` 为链头，DeepSeek 兜底，思考已关闭（下面有为什么）。

#### (1) 最狠的一个发现：模型的"思考"吃掉了 80% 的时间和成本

用**真实 parse 节点提示词**逐模型基准（`python -m scripts.probe_llm --agent parse`，样本简历 449 字符）：

| 模型 | 开思考：延迟 / 输出 token / 成本 | **关思考**（`{"enable_thinking": false}`） | 变化 |
|---|---|---|---|
| `qwen3.8-max` | 60.1 s / 2483 / ¥0.0218 | **9.5 s / 404 / ¥0.0051** | **快 6.3×，省 77%** |
| `deepseek-v4-pro-0813` | 32.2 s / 2745 / ¥0.0239 | **5.3 s / 541 / ¥0.0061** | 快 6.1×，省 74% |
| `qwen3.7-flash` | 46.7 s / 4187 / ¥0.0354 | **6.2 s / 686 / ¥0.0074** | 快 7.5×，省 79% |
| `qwen3.7-flash-2026-07-15` | 53.4 s / 4441 / — | （未测） | — |

三个模型的**解析结果完全一致**（工作 2 / 项目 1 / 技能 6）。所以这不是"用质量换速度"，是**纯粹的白烧钱**。
已修：`ALIYUN_EXTRA_BODY={"enable_thinking": false}`（代码默认值 + `.env` + `.env.example` 三处都写了原因）。

> 这也解释了为什么第一次 live 全量验证跑了 1 小时还没结束：`parse` 一个节点就 98.7 s > 60 s 超时 → 触发 3 次重试 × 5 个模型的重试风暴。

#### (2) 一个只在真实模型下暴露的**硬 Bug**：提示词里没有 "json" 就整节点降级

```
aliyun 400: InternalError.Algo.InvalidParameter:
'messages' must contain the word 'json' in some form, to use 'response_format' of type 'json_object'.
```

阿里云百炼与 DeepSeek **都强制**这条规则。而 `verify.py:320` 为了测 Prompt 版本自增，把 `ats_agent` 覆盖成 `"（验证用）请以 ATS 视角审查简历。"` —— 里面没有 json 字样，于是**该节点之后在全部 5 个模型上都 400**，一路重试后静默降级为规则引擎。

这**不只是测试数据的问题**：`POST /admin/prompts` 是正式功能，管理员在后台把提示词改得没有 json 字样，就会让那个节点在所有模型上失效，而看板上只看到 `degraded`。
已修：在 `services/llm.py` 的适配层统一兜住 —— messages 里没有 json 就自动补一句，**并打 warning**（让"提示词被改坏"可见，而不是静默降级）。

#### (3) live 模式 3 项失败（mock 下都是 PASS）

| 失败项 | 实测证据 | 根因 | 修法 |
|---|---|---|---|
| 「关键词类发现项的引文是关键词本身」 | 发现项原文是 `'缺失核心关键词 Java 及 Spring 生态'`，引文却是 `'Python、Go、MySQL、Redis、Kafka、Docker'` | live 下该 finding 由 LLM 产出，`original_text` 变成了**标题**而不是关键词本身；规则层原本是对的 | `jd_match_agent` 组装 gap finding 时强制 `original_text = keyword`；或在 `finding_utils.coerce_findings` 对该 category 做归一化 |
| 「该样本触发时间线空窗检测」 | 未检测到空窗期发现项 | 空窗检测是规则层产出的**黄色**条目；`suggestion_agent` 以 LLM 结果为主，只**抢救红色**条目，黄色被丢掉 | 抢救规则层条目时放宽到"所有 `category=risk` 且能被定位的条目"，或让 suggestion 的 prompt 明确要求保留规则层的 time-line 结论 |
| 「同一简历两次诊断分数一致（可回归）」 | `ats 62→58`、`impact 38→36`、**`risk 78→95`** | **正是 §0.2-C 预测的风险**：`compute_scores` 吃 findings，而 live 下 LLM 会增删 findings，两次运行结果不同 | 给 finding 打 `deterministic` 标记，**只有规则层产出参与扣分**；LLM 补充项只进报告展示。改 `analyze.compute_scores` + `suggestion_agent._select_final` |

> 第 3 条是**产品级问题**：「改完简历重新诊断、对比分数变化」是这个项目的核心卖点，而真实模型下 **risk 分会在 78 与 95 之间跳动**。这一条必须优先修，否则产品定位不成立。

#### (4) 真实性能与成本（live，2 份报告）

```
任务 2（成功 2 / 失败 0）｜报告 2｜平均耗时 147026ms
token 29233｜总成本 ¥0.1298｜单份 ¥0.0649｜评分 5.0｜公平性 1.0
```

- **端到端 147 秒/份** → 「60 秒内出报告」**当前不达标（差 2.5×）**。
- **¥0.0649/份** → 「≤ 0.5 元」通过，且有 7.7× 余量（**但单价是假设值，换成真实单价后要重算**）。

逐节点耗时（live，关思考后）：

| 节点 | 耗时 | 输出 token | 说明 |
|---|---|---|---|
| parse | 7.4 s | 400 | 关思考后从 98.7 s 降下来 |
| jd_match | 14.9 s | 577 | |
| ats / impact / risk | **23.0 / 17–23 / 15–25 s** | 736–1023 | 三者互不依赖 → **可并行** |
| suggestion | **46.4 s** | **2212** | 最大瓶颈：要求它产出 ≤8 条含改写句的建议，输出天然最长 |
| report | 3.8 s | 118 | |
| qc | （约 10–20 s） | — | |

**要让 147 s 进 60 s，只有三条路（按性价比排序）：**

1. **三路并行**（ats/impact/risk）：60 s → 20 s，总时长 ≈ **107 s**。已设计好（见 [LangGraph 接入文档 §3.1](ZhenResume_LangGraph接入与并发实现.md)）。
2. **给输出瘦身**（最大头是 suggestion 的 2212 token）：提示词里写清"findings 最多 5 条、每条 `rewritten_text` ≤ 60 字、不要复述原文"，并把 `LLM_MAX_TOKENS` 从 4096 降到 ~1200。suggestion 46 s → ~20 s，总时长 ≈ **81 s**。
3. **按节点路由模型**：`deepseek-v4-pro-0813` 在 parse 上比 `qwen3.8-max` 快 1.8×（5.3 s vs 9.5 s）。把"重输出/低推理"的节点（parse / jd_match / ats）路由到快模型，把"重判断"的节点（suggestion / qc）留给强模型 —— 这正是 `aaa.md §四` 写的"按任务与成本路由"。

> 诚实结论：**60 秒这个验收线，靠上面三条能压到 80–100 秒；要真正进 60 秒，还得再砍输出或把节点数从 8 降下来。** 建议要么把目标改成"90 秒内"，要么接受"首屏先出关键结论、建议异步补齐"的产品形态。

---

## 1. 总表：25 件事，按"值不值"排序

**优先级口径**：P0 = 已经坏了或从未被验证（不做会出事）；P1 = 用户能直接感知的质量；P2 = 工程化（决定后续迭代速度）；P3 = 产品与体验。

| # | 类别 | 要做什么 | 改动位置 | 验收方式 | 人日 |
|---|---|---|---|---|---|
| **P0-1** | 安全 | `/admin/*` **零鉴权**：任意设备可读写 prompt、看全站指标与全部反馈 | `api/admin.py`、`api/deps.py` | 非管理员请求 `/admin/metrics` 返回 403 | 1 |
| **P0-2** | 合规 | `AuditLog` 表**已建但从未写入**；`aaa.md §4` 要求审计日志 | `models.py:283`、新增 `services/audit.py` | 改 prompt / 删简历后 `audit_logs` 有新行 | 1 |
| **P0-3** | 支付 | ~~回调验签、幂等~~ **暂缓**（本轮决策：等基本功能完善后再做收费） | `api/billing.py` | 恢复时：伪造回调 400；重复回调只入账一次 | — |
| **P0-4** | 正确性 | 评分含 `datetime.now()` → **跨天复跑可能变分** | `services/analyze.py:418, 562` | 注入固定"当前时间"后，跨天跑分数一致 | 1 |
| **P0-5** | 正确性 | `workflow_steps` 无唯一索引 → 重放/重试会插入重复行 → **成本虚高** | `models.py` | 同一 `(task_id, step_name)` 只有 1 行 | 0.5 |
| **P0-6** | 稳定性 | **无限流**；HR 批量**不校验配额**（代码注释自己承认） | `api/tasks.py`、`api/hr.py` | 超过阈值的请求返回 429/402 | 1.5 |
| **P0-7** | 正确性 | **live 实测失败**：两次诊断分数不一致（risk 78↔95） | `services/analyze.py`、`agents/suggestion_agent.py` | live 连测 3 次，五维分完全一致 | 1–2 |
| **P0-8** | 正确性 | **live 实测失败**：关键词类发现项的 `original_text` 变成标题而非关键词，引文指向错误 | `agents/jd_match_agent.py`、`agents/finding_utils.py` | 该断言在 live 下也通过 | 0.5 |
| **P0-9** | 质量 | **live 实测失败**：时间线空窗检测（黄色规则条目）在 live 下被丢 | `agents/suggestion_agent.py` | 该断言在 live 下也通过 | 0.5 |
| **P0-10** | 性能 | **live 实测 147s/份**，60 秒验收不达标 | 编排并行 + 输出瘦身 + 模型路由（见 §0.4） | 端到端 ≤ 90s（或重新定义验收线） | 5–8 |
| **P0-11** | 提示词 | 后台 prompt 若不含 "json" 字样，该节点在**所有模型**上 400 并静默降级 | `services/llm.py`（**已修**，并打 warning） | 用无 json 字样的 prompt 调用仍成功 | ✅ 0.2 |
| **P1-1** | 质量 | **检索升级：真实 embedding + pgvector（+ 可选 rerank）** | `services/embeddings.py`、`services/rag.py`、DDL | 20 条 query 的 top-5 人工命中率提升 | 2 |
| **P1-2** | 质量 | **`clarity_findings` 是死代码**（定义了从未被调用）→ 清晰度维度实际不可控 | `services/analyze.py:299`、`agents/` | 弱结构样本能触发 clarity 维度发现项 | 1 |
| **P1-3** | 质量 | 建议升级为 STAR + 具体指标占位 + JD 术语对齐 | `agents/suggestion_agent.py`、`services/prompts.py` | 抽 10 条建议人工评估"可直接粘贴率" | 2–3 |
| **P1-4** | 质量 | 逐条经历**并行分析**（`Send`），替代"整篇一次调用被摘要截断" | `agents/impact_agent.py`、`workflow/` | 长简历（≥6 段经历）每条都有建议 | 2 |
| **P1-5** | 质量 | mock 模式加**同义归并**（现在字面匹配，`性能优化` vs `优化慢查询` 误报） | `services/analyze.py`、`services/parse_rules.py` | 同义样本不再报"关键词缺失" | 1.5 |
| **P1-6** | 质量 | 多栏排版简历解析错位（README 自列边界） | `services/parse_rules.py`、`services/extract.py` | 双栏样本解析出正确段数 | 2 |
| **P1-7** | 质量 | 报告 PDF 导出（现在只有 Markdown） | `services/report_render.py`、`api/reports.py` | 导出 PDF 返回 200 且内容完整 | 1 |
| **P2-1** | 工程 | **零单测、零 CI** → 建测试金字塔（金标快照） | 新增 `backend/tests/`、`.github/workflows` | `pytest` 全绿 + PR 自动跑 verify | 2–3 |
| **P2-2** | 工程 | **LangGraph 接入**：断点续跑 + 三路并行 + 节点级重试 | 见 `ZhenResume_LangGraph接入与并发实现.md` | 双引擎下 174/174；分数逐位相同 | 12–18 |
| **P2-3** | 工程 | **Alembic 迁移**（现在 `create_all`，字段变更要手工） | 新增 `alembic/` | `alembic upgrade head` 从空库建出全部表 | 1.5 |
| **P2-4** | 工程 | 可观测：结构化日志 + Sentry +（可选）Langfuse | `main.py`、`services/llm.py` | 一次失败请求能在 Sentry 里定位到节点 | 2 |
| **P2-5** | 工程 | 独立 worker/队列（Celery/arq）—— **注意 `verify.py` 依赖同步后台任务** | `workflow/`、`api/tasks.py` | 改造后 verify 仍全绿（需先给它加轮询等待） | 3–5 |
| **P2-6** | 工程 | 数据库切 PostgreSQL（含 pgvector） | `config.py`、`db.py` | PG 下 verify 全绿 + 并发无写锁 | 2 |
| **P3-1** | 产品 | 真实登录（手机号 / OAuth），替代可伪造的 `X-Device-Id` | `api/deps.py`、前端 | 换设备后历史记录仍可见 | 3–5 |
| **P3-2** | 产品 | HR 批量并发 + 配额闸门（`aaa.md` 要求 100 份 ≤10 分钟） | `api/hr.py` | 100 份 ≤10 分钟 | 2 |
| **P3-3** | 产品 | 分享链接权限与过期 | `api/reports.py` | 过期链接返回 410 | 1 |
| **P3-4** | 产品 | 编辑器瘦身（`editor-workspace.tsx` 1939 行） | `frontend/components/editor/` | 拆分后视觉与交互零回归 | 3–5 |
| **P3-5** | 产品 | 埋点：二次诊断率 ≥30%（`aaa.md` 成功指标） | 前端 + 新增事件表 | 看板能看到二次诊断率 | 2 |

---

## 2. P0 逐条展开（为什么这些必须现在做）

### 2.1 `/admin/*` 零鉴权（最严重）

```python
# api/admin.py —— 四处端点全都是这一行
user: User = Depends(get_current_user),
```

`get_current_user`（`api/deps.py:18-29`）只做一件事：**从 `X-Device-Id` 请求头自动建用户**。也就是说：

- 任何人带任意 `X-Device-Id` 就能**读取全站成本、任务数、公平性通过率**（`/admin/metrics`）；
- 能**读取并覆盖 8 个 Agent 的 prompt**（`POST /admin/prompts` 版本自增，下一次工作流立即生效）——这是一条**直接的线上提示词注入通道**；
- 能读取**全部用户反馈**（`/admin/feedback`）。

`User.role` 字段早就存在（`models.py:59`，默认 `jobseeker`），但**全仓没有任何地方读过它**。修法很小：

```python
# api/deps.py
def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise AdminForbidden()          # errors.py 新增，契约 §8 加一条错误码
    return user
```

配套：加一个 `scripts/grant_admin.py --device-id xxx`（或环境变量 `ADMIN_DEVICE_IDS`）用于授予管理员，避免为了改 prompt 先做一整套登录。
**验收**：普通设备请求 `/admin/metrics` → 403；管理员设备 → 200。

> 顺带说明为什么这条排第一：它同时是安全问题、合规问题（`aaa.md §4` 的权限隔离）和**生产事故风险**（prompt 可被任意改写）。

### 2.2 `AuditLog` 从未写入

表已经建好了（`models.py:283`），`aaa.md §4` 明确要求"审计日志"，但全仓没有一处写入。最小落地：

```
写审计的动作（4 个）：改 prompt、删简历、删 JD、管理员查看敏感指标
字段：actor（device_id/user_id）、action、target_type、target_id、detail(JSON)、created_at
```

**验收**：`POST /admin/prompts` 后 `audit_logs` 出现一行，且能查到是谁改的。

### 2.3 支付回调不验签、无幂等 —— **本轮暂缓**

`api/billing.py:4` 的模块注释自己写着"接真实渠道时只需替换 `_create_order` 里的 pay_url 生成与回调验签"——即现在**回调是假的**，`verify.py` 里"支付回调返回 200"和"回调幂等"两个断言在 mock 渠道下是自证式的。

**按本轮决策暂缓**（不做收费就不做验签）。恢复开发时的做法：签名校验（HMAC/渠道公钥）+ 以 `order_no` 为唯一键的幂等写入 + 金额与订单比对。
**验收**：伪造签名 → 400；同订单回调两次 → 只入账一次、报告只解锁一次。

> 注意一个连带影响：**免费额度闸门依赖"是否成功支付过"**（`api/tasks.py:21-26` 的 `_has_paid_access`）。暂缓支付意味着线上只能靠 `FREE_QUOTA` 放量；调大 `FREE_QUOTA` 就等于免费开放，调小则用户体验受限。这是"暂缓收费"必须一并决定的一个运营参数。

### 2.4 评分的两处不确定性（P0-4 / P0-7）

**(a) 跨天漂移**：`analyze.py:418` 与 `:562` 直接调用 `datetime.now()` 计算"至今"，所以同一份简历在 12 月 31 日和 1 月 1 日跑，时间线空窗/工作年限的判断可能不同。

修法：把"当前时间"变成入参（`compute_scores(..., now: datetime | None = None)`，默认取 `datetime.now(timezone.utc)`），测试里传固定值。
**验收**：注入 2026-01-01 与 2026-06-01 两个"当前时间"，同一份简历分数一致（或差异可解释）。

**(b) LLM 方差导致的分数漂移（需先实测）**：`compute_scores` 的入参包含 `findings`，而 live 模式下 `suggestion_agent` / `qc_agent` 会用 LLM 增删发现项 → **同一份简历两次诊断可能得到不同的五维分**。这直接威胁"改完再测、对比分数变化"这个核心卖点。

修法（按推荐度）：

1. **分离两类发现项**：给每条 finding 打 `deterministic: bool`，只有规则层产出的（ats/impact/risk/gap 的规则结果 + 时间线）参与扣分；LLM 补充项只进报告展示，`_penalty` 跳过。改动集中在 `analyze.compute_scores` + `suggestion_agent._select_final`。
2. 或**给分数设抖动上限**：LLM-only 条目对任一分数的贡献封顶（现状是 `_penalty(cap=2)`，可把 LLM-only 的权重设成 0）。
3. 兜底：同一 `resume.raw_text` + `jd.raw_text` 的诊断结果做**内容指纹缓存**，N 天内直接复用（顺带省成本）。

**验收**：live 模式下同一简历连续诊断 3 次，五维分**完全一致**；这条要写成 `verify.py` 的新断言。

### 2.5 `workflow_steps` 唯一索引

现在同一 `(task_id, step_name)` 理论上可以有多行。一旦引入重试/断点续跑/并发（下一阶段马上要做），就会插入重复行，而 `/admin/metrics` 是 `SUM(tokens)`、`SUM(cost)` 聚合 → **成本看板虚高、`cost_per_report` 失真**。

修法：`UniqueConstraint("task_id", "step_name")` + 写入改成 upsert。**0.5 人日，是后面所有并发/重试工作的前置条件。**

### 2.6 无限流 + HR 批量不校验配额

- `api/hr.py` 的模块注释明确写着"批量不消耗免费额度也不校验 HR 套餐配额"→ 免费用户可以直接绕过单份配额闸门批量跑 100 份；
- 全仓没有任何速率限制 → 单个 IP/设备可以高频打 `/tasks`、`/resumes`，直接烧 LLM 成本。

修法：任务创建接口加"每设备每分钟 N 次 + 同时在跑 M 个"的闸门（内存或 Redis 计数即可，MVP 用内存 + `slowapi` 类中间件）；HR 批量按 `hr_batches.resume_ids` 长度扣配额。
**验收**：超过阈值的请求返回 429；免费用户建批量 → 402。

---

## 3. P1 质量（用户能直接感知）

### 3.1 检索升级（收益最大的一项）

README §八 自己把"换真实语义向量"列为下一步第一条；`embeddings.py:1-12` 也写着"DeepSeek 不提供 embedding 接口，所以现在用本地确定性哈希向量"。三件套：

| 步骤 | 改动 | 说明 |
|---|---|---|
| 1. 换 embedding provider | `embeddings.py` 加 `provider=hash|http` | 任何 OpenAI 兼容的 embedding 接口（bge-m3 / text-embedding-3-small / 本地 Ollama） |
| 2. 上 pgvector | `knowledge_chunks.embedding` → `Vector(1536)` + HNSW | `rag.py` 从 Python 全量点积改成 `ORDER BY embedding <=> :q` |
| 3. 可选 rerank | 新增 `services/rerank.py` | 召回 20 → 复排到 5，是"检索准不准"的关键一跳 |

**降级**：嵌入服务不可用时回落哈希向量，报告照出（保持"零 Key 可跑通"这条红线）。
**验收**：从 `samples/` 的 JD 里抽 20 条 query，人工评估 top-5 命中率，做一个前后对比表。

### 3.2 清晰度维度其实是"死的"

`analyze.py:299` 定义了 `clarity_findings(...)`，但**全仓没有调用点**；8 个 Agent 里也没有 clarity 节点。它进入分数的唯一路径是 live 模式下 LLM 自己产出 `category="clarity"` 的发现项 → **mock 模式下"清晰度"这一维几乎不动**。

修法二选一：① 把 `clarity_findings` 接进 `suggestion_agent` 的候选池（最小改动）；② 新增一个 `clarity` 节点——**但注意契约 §4.2 固定 8 个步名，加节点会破契约**，所以推荐 ①。
**验收**：结构混乱的样本能产出 clarity 类发现项，验证项里有专门断言。

### 3.3 让建议"能直接粘贴"

现状 `suggestion_agent` 已经保证了"≥5 条 + 每条有 `rewritten_text` + 缺数据用 `[补充指标：…]` 占位"，这已经比多数同类产品好。再往上走：

1. **指标占位要具体**：`[补充：优化后 P99 从 __ms 降到 __ms]` 比 `[补充指标：…]` 有效得多；
2. **STAR 化**：用 `analyze.py` 现成的强/弱动词表 + `QUANT_SIGNAL_RE` 生成结构化改写句；
3. **与 JD 术语对齐**：`jd_match_agent` 已算出 `missing_keywords` / `false_gaps`，直接产出"把『性能优化』改成 JD 里的『高并发性能调优』"这类逐词建议；
4. **预计提分**：用 `compute_scores` 在"应用这条建议后的模拟输入"上重跑，给出"+X 分"——这是确定性评分的第二个红利（第一个是可对比）。

### 3.4 逐条经历并行分析（`Send`）

`impact_agent` 现在是**整篇一次 LLM 调用**，长简历会被 `_bullets_digest`（`impact_agent.py:70-77`）截断 → 后半段经历没有建议。改成"每条要点一个子任务"，既提质量也提速度（实现见 `ZhenResume_LangGraph接入与并发实现.md` §3.2），**但必须配并发上限与成本校验**。

### 3.5 mock 模式的同义归并

现在 mock 的关键词匹配是**字面**的：JD 写"性能优化"、简历写"优化慢查询" → 判为缺失（README 已列为边界）。加一层**同义词/词表映射**（`性能优化 ↔ 慢查询优化 ↔ QPS 提升`）能显著降低 mock 模式的误报，让无 Key 演示的体验接近 live。

---

## 4. P2 工程化（决定后续迭代速度）

### 4.1 零测试、零 CI（当前最大的工程债）

- `backend/` 下**没有 `tests/` 目录**，`requirements.txt` 里没有 `pytest`，仓库里**没有 `.github/`**；
- 唯一的保护是 174 项**端到端**断言——它证明"主链路能跑"，但**不能定位**是哪一行坏了，也不能保护那些不被端到端覆盖的确定性逻辑。

先补四份**金标快照测试**（都是纯函数，最容易做）：

```
tests/test_scores.py       同一输入 → 分数与 findings 快照完全一致
tests/test_evidence.py     locate_quote 的命中/空白折叠/前缀退化/失败四类用例
tests/test_parse_rules.py  samples/*.txt → ParsedResume 结构快照
tests/test_rag.py          检索排序快照（固定 provider，避免 embedding 一换就挂）
```

再加一条 CI：`pytest` + `python -m scripts.verify`，PR 必须双绿。**这一步是后面所有重构的安全网**，所以排在 LangGraph 之前。

### 4.2 Alembic

现在 `init_db()` 直接 `create_all`，README 坦承"字段变更需手工处理"——这也是为什么编辑器原文只能塞进 `parsed_json.editor_content` 而不是新增列。上 Alembic 之后，加列/加索引（比如上面 P0-5 的唯一索引、P1-1 的 `Vector` 列）才有正规路径。

### 4.3 LangGraph（断点续跑 + 并发）

完整方案已交付：[ZhenResume_LangGraph接入与并发实现.md](ZhenResume_LangGraph接入与并发实现.md)。为什么排在这里而不是更前：

- 它对**质量**没有直接贡献（评分、证据、检索都不变）；
- 它的收益是**可用性**（崩了能续跑）与**延迟**（三路并行）；
- 而 §0.1 的体检结果决定它是"必需"（端到端 >60s 或经常失败重跑）还是"锦上添花"。

**先做完 4.1 的测试，再动它**——否则"替换前后行为等价"没有判据。

### 4.4 队列与 PostgreSQL

- 队列：`BackgroundTasks` 在当前规模够用，但 HR 批量与高并发下需要真队列。**注意 `verify.py:140` 依赖"TestClient 在响应前跑完后臺任务"**，上队列必须同步改造 `verify.py`（加轮询等待），否则成批断言会挂。
- PostgreSQL：SQLite 只有单写者。**并发（无论 LangGraph 并行还是 HR 批量）都会撞写锁**，`connect_args={"timeout": 30}` 只能缓解。真正要并发就得换 PG——这一步与 P1-1 的 pgvector 是同一次迁移，**建议合并做**。

### 4.5 可观测

现在已经有的：`workflow_steps`（tokens/cost/latency/notes）+ `/admin/metrics`。缺的是**错误与异常的可追溯性**：一个 500 现在只有 `traceback.print_exc()`。建议：结构化日志（JSON，带 `task_id`/`step_name`/`device_id`）+ Sentry；LLM 侧可选接 Langfuse（`services/llm.py` 是单一入口，加一个 callback 即可）。

---

## 5. P3 产品与体验

| 项 | 现状 | 目标 |
|---|---|---|
| 登录 | `X-Device-Id` 匿名隔离，**可伪造** | 手机号/OAuth 登录，历史记录跟账号走 |
| HR 批量性能 | 串行（代码里自己标注"应换成并发"） | 并发 + 配额，100 份 ≤10 分钟 |
| 报告 PDF | 只有 Markdown（浏览器打印） | 服务端出 PDF，与 DOCX 版式一致 |
| 分享页 | 匿名可读，永不过期 | 时效 + 可撤销 |
| 编辑器 | `editor-workspace.tsx` 1939 行 | 拆 hooks/子组件，状态机化 |
| 成功指标埋点 | 无 | 二次诊断率 ≥30%、报告完成率、建议采纳率 |

---

## 6. 推荐排期（4 个 sprint，每个 1–2 周）

### Sprint 1（1 周）：把"没被验证的"变成"有数据的"
1. ~~LLM 多供应商链 + 连通性体检~~ **已完成**（§0.3）；`scripts/probe_llm.py` 可随时重跑；
2. §0 真实性体检（真实 Key 跑一遍 + 两条 SQL）—— 用真实 prompt 的成本/延迟替换探针外推值；
3. P0-1 管理后台鉴权 + P0-2 审计日志；
4. P0-5 唯一索引（0.5 天，后面都要用）；
5. 按实测**真实单价**填 `ALIYUN_PRICE_*` / `DEEPSEEK_PRICE_*`，让成本看板可信。

**验收**：`verify.py` 在 live 模式下全绿（或明确列出挂在哪几条）；`/admin/metrics` 对普通设备返回 403；`audit_logs` 有数据；真实成本/延迟入库且单价已按型号校准。

### Sprint 2（1–2 周）：把"不达标的"补齐
1. ~~P0-3 支付验签~~ **暂缓**；做 P0-4 时间可注入；P0-6 限流与 HR 配额；
2. P0-7（按体检结果）分离确定性发现项 → **live 连测 3 次同分**；
3. 开始 P2-1 的测试（先做 `test_scores.py` + `test_evidence.py`）。

**验收**：跨天分数一致；live 三次诊断同分；`pytest` 至少 4 个文件全绿。

### Sprint 3（1–2 周）：检索质量 + 数据库
1. P1-1 embedding provider + pgvector（+ rerank）；
2. P2-6 切 PostgreSQL（与上面合并成一次迁移，用 P2-3 的 Alembic）；
3. P2-1 补齐其余测试 + 加 CI。

**验收**：20 条 query 的 top-5 命中率对比表；PG 下 `verify.py` 全绿；CI 双绿。

### Sprint 4（1–2 周）：编排与并发（按体检结果决定做多少）
1. P2-2 LangGraph 串行接管（行为等价）；
2. 三路并行（形态一）；
3. P1-4 逐条经历 `Send` 并行（形态二）；
4. P3-2 HR 批量并发（形态三）。

**验收**：双引擎分数逐位相同；端到端 ≤60 秒（live）；100 份 ≤10 分钟；单份成本 ≤0.5 元。

> **顺序不可颠倒的两点**：① 测试（4.1）必须在 LangGraph 之前；② 唯一索引 + PostgreSQL 必须在真并发之前。

---

## 7. 现在**不要**做的（过度设计清单）

| 别做 | 原因 |
|---|---|
| 微服务拆分 / K8s | 单体 FastAPI 完全够用；现在最大的瓶颈是"没有测试"和"没有真实数据"，不是架构 |
| 多模型路由（Claude/GPT/DeepSeek 三家全接） | `services/llm.py` 已是 OpenAI 兼容协议，需要时加一个 provider 分支即可；现在接三家只会让成本核算变复杂 |
| 引入低代码/Agent 平台（Dify 类） | 已评估并否决（确定性资产外移、run 不幂等、业务状态与日志割裂） |
| 向量库集群（Milvus 等） | 知识库是十几篇文档，pgvector 足够 |
| Human-in-the-loop / 时间旅行调试 | 还没有需要人工介入的业务场景；先有需求再上机制 |
| i18n / 多语言 | 目标用户是大陆求职者，先把中文质量做到位 |
| 重构"上帝文件"（`analyze.py` 704 行等） | `docs/代码拆分与生产化建议.md` 已给出方案，但**必须先有单测再拆**——所以它排在 Sprint 2 之后，且应作为"顺手做"而不是目标 |

---

## 8. 一页速查

**如果只做三件事**：① 真实 Key 跑一遍拿到真实成本/延迟；② 管理后台加鉴权 + 审计日志；③ 检索换真实 embedding（pgvector）。

**如果只做一件事**：**真实 Key 体检**。因为它决定后面所有事的优先级——包括"并发到底是不是必需"。

**判断任何新需求是否该做的三个问题**：
1. 它是否让 `aaa.md §5` 的某条验收从"形式满足"变成"真实满足"？
2. 它是否有可执行的验收方式（一条命令 / 一个断言 / 一张对比表）？
3. 它是否会让 `verify.py` 的那 174 项更容易挂？如果是，先补测试再做。
