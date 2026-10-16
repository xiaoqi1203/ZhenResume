# ZhenResume 当前状态与待办（决策用一页纸）

> 用途：停下来做功能取舍时看这一页。**只写已验证的事实**，未验证的会明确标注。
> 更新日期：本轮 LangGraph 编排 + PG/Redis/Celery + HR 排序表落地之后。

---

## 一、已完成并**实测验证**的

### 编排与执行

| 能力 | 状态 | 证据 |
|---|---|---|
| LangGraph 编排（内嵌 FastAPI，非 Agent Server） | ✅ | `WORKFLOW_ENGINE=langgraph`，174/174 |
| 引擎双轨（legacy 手写状态机 ⇄ LangGraph） | ✅ | 两条路各自 174/174，业务语义一致 |
| 三路并行（ats / impact / risk） | ✅ | 单份 124.0s → **84.1s** |
| HR 排序表轻量图（parse + jd_match‖risk） | ✅ | **29.6s**，砍掉 impact/suggestion/report |
| HR 批次并发（`asyncio` + 信号量） | ✅ | 10 份并发：总墙钟 **32.8s**，0 失败 |
| Celery 执行（独立 worker + Redis） | ✅ | worker 真执行任务，`verify` 174/174 |
| PostgreSQL 存储 | ✅ | **VM 上 174/174**（建表 + 唯一索引 + 全读写） |
| 僵尸任务回收 / 幂等持久化 / 唯一索引 | ✅ | 代码就绪；**崩溃续跑演练未做** |

### 质量与成本

| 指标 | 实测值 |
|---|---|
| 单份完整报告（求职者） | 串行 124.0s → 并行 **84.1s**；¥0.060/份 |
| 单份 HR 排序表 | **29.6s**；**¥0.0213/份** |
| 100 份外推（并发 50） | ≈ **56s**（10 份实测校准外推，**未做 50/100 份实测**） |
| 输出 token | 完整 13,000/份 → HR **5,124/份**（−60%） |
| LLM 链路 | aliyun `qwen3.8-max` → `deepseek-v4-pro-0813` → `qwen3.7-flash` → `qwen3.7-flash-2026-07-15` → deepseek `deepseek-flash` |
| Embedding | 主 `qwen3.7-text-embedding-flash`(1024) + 回退 Ollama `mxbai-embed-large`(1024)，含**模型签名守卫** |

### 已排掉的坑（都实测过）

- 「关键词类发现项引文是关键词」→ 引文修复
- 「同一简历两次诊断分数不一致」→ 评分只用规则层（`compute_scores_deterministic`）
- 「qc 删掉 JD 引用的红色风险 / 降级全部红色」→ LLM 删改规则层条目一律否决
- `qwen3.8-max` 超时风暴 → 关思考（`enable_thinking:false`）+ 超时跳过重试直接换模型
- `max_tokens` 硬砍导致 JSON 截断重试风暴（239s）→ 加 `finish_reason=length` 截断守卫（降到 74s）
- embedding「建索引时静默回退」导致混入两种向量空间 → 建索引禁止回退 + 检索侧签名守卫
- Celery 队列名不一致（`celery` vs `default`）→ worker 永远不消费

---

## 二、待办（按"该不该现在做"分三档）

### 第 1 档：上线阻塞项（建议优先，约 2–3 天）

| 项 | 现状 | 工作量 |
|---|---|---|
| **管理员鉴权** | `/api/v1/admin/*` 无角色校验 | 0.5 天 |
| **AuditLog 落库** | 表建了，**没有一行代码写它** | 0.5 天 |
| **服务端限流 + HR 配额** | 只有客户端信号量 | 1 天 |
| **真实模型单价** | `ALIYUN_PRICE_*` 是通用估值，成本看板不准 | 0.5 天 |
| **PDF 导出** | VM 上未配 LibreOffice，走降级分支 | 0.5 天（装依赖） |

### 第 2 档：体验与稳健（约 3–4 天）

| 项 | 收益 |
|---|---|
| **流式结果（SSE）** | HR 不用干等 30s；顺手消掉前端 1.5s 轮询这个最热读路径 |
| **批次去重（`file_sha256`）** | 重复上传不重复花钱，排名不失真 |
| **崩溃续跑演练** | 验证 `resume_zombies`：跑到一半 kill → 重启续跑 |
| **checkpoint 清理脚本 + TTL** | 否则 checkpoint 表无限增长 |
| **合规/公平性闸门** | HR 场景刚需；现在只有 risk agent 的歧视性内容检测 |

### 第 3 档：数据规模才需要（现在做收益≈0）

| 项 | 触发条件 |
|---|---|
| pgvector + HNSW | 知识库 > 几千切片（**现在 16 个**） |
| `JSON` → `JSONB` + GIN | 需要按技能/关键词检索候选人时 |
| 索引补齐（5 条） | 10 分钟的事，可随时做 |
| 冷热分离 / 按月分区 | 报告表到百万行级 |
| 任务状态进 Redis | 轮询把 DB 打满时 |
| 求职者完整报告 ≤30s | 现在 84.1s；只有 HR 排序表达标 |

### 已明确延后（你的决策，非遗漏）

收费/支付、登录/账号体系、结果分享。

---

## 三、需要你拍板的决策点

1. **要不要做收费前置？** 不做的话，登录/配额/支付都不必碰。
2. **HR 侧是否要对外的多租户？** 决定 `hr_batches` 是否要引入组织/团队成员模型。
3. **求职者完整报告要不要也压到 30s？** 需要提示词瘦身 + 快模型路由（HR 已达标）。
4. **pgvector 现在是"启用了扩展"还是"真正接入检索"？** 前者一条 SQL，后者要改 6 处代码。
5. **要不要把「50 份满并发压测」补上？** 唯一还没验证的环节（约 ¥1.07）。

---

## 四、基础设施现状（VM `192.168.10.128`）

```
Docker:  zhenresume-db    pgvector/pgvector:pg16    127.0.0.1:5432  (healthy)
         zhenresume-redis redis:7-alpine            127.0.0.1:6379  (healthy)
密码:    统一 1203（端口只绑 127.0.0.1，不对局域网开放）
系统版:  postgresql / redis-server 已 disable（避免抢端口）
配置:    deploy/infra/docker-compose.yml + backend/.env
扩展:    pgvector 扩展**尚未启用**（一条 SQL 即可，见对话记录）
启动:    celery -A app.worker worker -l INFO -Q default --concurrency=4
自检:    python -m scripts.check_infra
回归:    python -m scripts.verify   → 期望 174/174（配 LibreOffice 为 177）
```
