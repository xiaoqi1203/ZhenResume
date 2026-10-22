# GitHub 发布指南（仓库元数据与上线检查清单）

> 本文件是「把项目放到 GitHub 做作品展示」的操作清单，不属于产品文档。
> 仓库地址：`https://github.com/xiaoqi1203/ZhenResume`

---

## 一、仓库元数据（About 区可直接复制）

**Repository name**：`ZhenResume`

**Description（英文，GitHub About 推荐一行）**：
`60s AI resume diagnosis · FastAPI + LangGraph multi-agent + RAG · evidence-grounded citations · Next.js 14`

**Description（中文备选）**：
`60 秒 AI 简历诊断：FastAPI + LangGraph 多 Agent + RAG，结论可定位、引用可校验；支持 HR 批量初筛与简历编辑器。`

**Topics（逐个粘贴）**：

```text
fastapi  langgraph  langchain  rag  llm  ai-agent  resume  ats
nextjs  typescript  postgresql  redis  celery  docker  python
```

**Website**：留空（暂无线上部署；如后续部署再加）。

**Social preview**：GitHub 建议 1280×640；可截取 `docs/assets/readme/home.png` 的首屏后上传
（Settings → General → Social preview）。

---

## 二、发布前检查清单（已完成项打勾）

- [x] `.gitignore` 覆盖 `backend/.env`、`.env.local`、`backend/data/`、评测缓存与本地截图
- [x] 全仓库密钥扫描：无 `sk-*`、GitHub token、真实手机号/邮箱（仅测试用 `example.com`）
- [x] 删除部署脚本里的默认密码（改为必须通过环境变量传入）
- [x] README 重写：定位 / 截图 / 架构图 / 实测数据 / 快速开始 / 设计取舍 / 边界
- [x] 性能与价格实测报告（2026-10-22）加入仓库
- [x] 简历项目描述与面试问答加入仓库
- [x] 截图去 PII：首页 / 编辑器 / HR / 定价（报告页截图含个人信息，未使用）
- [x] 分支重命名为 `main`，推送到 GitHub

**发布后建议再做**：

- [ ] 在 GitHub 仓库 Settings 中确认 Issues 开启、Discussions 可选开启
- [ ] 添加 `LICENSE`（如决定 MIT/Apache-2.0；当前为「保留所有权利」）
- [ ] 添加 CI（backend `compileall` + `scripts.check_report_consistency`；frontend `typecheck`）
- [ ] 切一个 `v0.1.0` Release，正文可直接用下面的模板
- [ ] 如担心内部过程文档过多，可把 `docs/` 中 P1–P9 评测记录收进 `docs/eval/` 子目录
- [ ] 线上部署后再补 Demo 链接与截图

---

## 三、Release v0.1.0 模板

**Tag**：`v0.1.0`
**Title**：`ZhenResume v0.1.0 — AI 简历诊断 MVP`

**Body**：

```markdown
首个可演示版本（MVP），面向求职者与 HR 的 AI 简历诊断。

## Highlights
- 8-Agent 诊断流水线（LangGraph，三路并行）：单份 38.6s / ¥0.05（真实模型 5 次实测）
- 可定位证据链：红色风险绑定原文 span + quote；知识库引用服务端逐字校验
- HR 批量初筛：100 份 52.7s / 100% 成功 / ¥0.0184 每份
- 账号体系、报告分享、简历编辑器、DOCX/Markdown 导出、Mock 支付闭环
- 契约回归 175/175、账号鉴权 55/55、真实 HTTP 黑盒 74/74
- Docker Compose 全栈：PostgreSQL 17 + Redis 7 + Celery + Next.js 14

## Docs
- 性能与价格实测报告：docs/ZhenResume_性能与价格实测报告_2026-10-22.md
- API 契约：docs/api-contract.md

## Known limitations
- 当前 `.env` 为本地演示配置，公网部署前需完成安全加固（见实测报告 §8.3）
- 支付为 Mock；真实简历质量尚未人工验收；Agentic RAG 默认关闭
```

---

## 四、给面试官/访客的阅读路径

1. `README.md` 顶部截图与「实测数据」表（30 秒建立印象）
2. `docs/ZhenResume_性能与价格实测报告_2026-10-22.md` 的 §0 / §8（工程判断力）
3. `backend/app/agentic/`、`backend/app/services/rag.py`、`backend/app/services/citation.py`（核心代码）
4. `backend/scripts/verify.py`（175 项契约回归怎么写的）
5. `docs/简历项目描述.md`（项目叙事与面试问答）