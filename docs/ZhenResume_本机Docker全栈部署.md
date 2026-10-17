# 全栈本机 Docker 部署

> 目标形态：**PostgreSQL(pgvector) + Redis + API + Celery worker + 前端 全部在本机 Docker Desktop 里跑**，不依赖虚拟机。
> 落地文件：[docker-compose.local.yml](../docker-compose.local.yml)、[.env.local.example](../.env.local.example)
> 前身：本文档原为"VM 跑中间件 + 本机 Docker 跑应用"（SSH 隧道方案），已归档到 §6。

---

## 1. 架构

```
┌──────────────── 本机（Windows + Docker Desktop）────────────────┐
│  network: zhenresume-local（compose 默认网络，服务名互联）        │
│                                                                 │
│  zhenresume-db      pgvector/pgvector:pg16   127.0.0.1:5432     │
│      └ 卷 zhenresume-pgdata（业务数据 + 知识库向量 + checkpoint） │
│  zhenresume-redis   redis:7-alpine           127.0.0.1:6379     │
│      └ 卷 zhenresume-redisdata（Celery broker/result）           │
│  zhenresume-api     zhenresume-backend:local :8000              │
│      └ 卷 zhenresume-appdata:/app/data（上传简历原件）            │
│  zhenresume-worker  同镜像，celery -Q default                   │
│      └ 同一个 appdata 卷（必须共享，否则 worker 找不到上传文件）    │
│  zhenresume-frontend zhenresume-frontend:local :3000            │
│                                                                 │
│  浏览器：http://localhost:3000  →  页面内联 API http://localhost:8000 │
└─────────────────────────────────────────────────────────────────┘
```

**镜像复用**：`api` 与 `worker` 用同一个 `zhenresume-backend:local` 镜像，只是启动命令不同。

---

## 2. 前置条件

| # | 项 | 检查方式 |
|---|---|---|
| 1 | **Docker Desktop 已启动** | `docker version` 的 Server 段不为空（实测当前为空 → 需先启动） |
| 2 | 端口 3000 / 8000 / 5432 / 6379 空闲 | 实测**当前都空闲** ✅ |
| 3 | `backend/.env` 已配 LLM Key | 本项目已配（`aliyun` 主模型 `qwen3.8-flash`） |
| 4 | 出网可用 | 构建要拉 pip/npm 包；运行时 embedding 要调百炼 |

---

## 3. 启动

```powershell
cd E:\ZhenResume

# 1) 准备变量（含 PG/Redis 密码）
Copy-Item .env.local.example .env.local
notepad .env.local          # 改 POSTGRES_PASSWORD / REDIS_PASSWORD，别用 change-me-*

# 2) 构建并启动（首次约 5–15 分钟：pip 装依赖 + npm build）
docker compose -f docker-compose.local.yml --env-file .env.local up -d --build

# 3) 看状态（api 应为 healthy，worker 无健康检查属正常）
docker compose -f docker-compose.local.yml ps

# 4) 首次建库：lifespan 会自动建表 + 知识库为空则灌种子；
#    也可以显式重建知识库（换 embedding 模型后必须重建）
docker exec -it zhenresume-api python -m scripts.seed_knowledge --force

# 5) 验收
curl http://localhost:8000/api/v1/health
docker exec -it zhenresume-api python -m scripts.verify      # 期望 174/174
```

打开 http://localhost:3000 即可。

### 常用命令

| 目的 | 命令 |
|---|---|
| 跟随日志 | `docker compose -f docker-compose.local.yml logs -f api worker` |
| 重启后端 | `docker compose -f docker-compose.local.yml restart api worker` |
| 改代码后重建 | `docker compose -f docker-compose.local.yml up -d --build api worker` |
| 进容器排查 | `docker exec -it zhenresume-api bash` |
| 只停不删数据 | `docker compose -f docker-compose.local.yml down` |
| ⚠️ 连数据一起删 | `docker compose -f docker-compose.local.yml down -v` |
| 连宿主 psql | `docker exec -it zhenresume-db psql -U zhenresume -d zhenresume` |

---

## 4. 关键设计点（都对应已踩过的坑）

| 点 | 原因 |
|---|---|
| `DATABASE_URL` 用 `db:5432` | 容器间用**服务名**互联；`127.0.0.1` 在容器里指容器自己 |
| `NEXT_PUBLIC_API_BASE=http://localhost:8000` | Next 的 `NEXT_PUBLIC_*` 是**构建期内联**，必须填浏览器能访问的地址（填 `api:8000` 必失） |
| `CORS_ORIGINS` 含 `localhost:3000` | 浏览器访问的是宿主端口；不对齐就是 CORS 错误 |
| `LANGGRAPH_CHECKPOINT=postgres` | 容器里用 sqlite 文件，`--force-recreate` 就丢断点；PG 才持久 |
| `WORKFLOW_EXECUTOR=celery` | inline 会在 HTTP 请求内跑完整工作流（HR 批次会超时） |
| `UPLOAD_DIR=/app/data/uploads` + 共享卷 | api 与 worker 必须看到同一份上传文件 |
| `depends_on: condition: service_healthy` | 等 PG/Redis 真就绪再起应用，避免首启建表失败 |
| worker `healthcheck: disable` | 镜像继承的 HEALTHCHECK 探 HTTP，worker 不提供 HTTP，否则永远 unhealthy |
| `api` 的 `healthcheck` 用镜像自带的 curl | `python:3.12-slim` 本身不带 curl，Dockerfile 已装 |
| 传给构建的 `PIP_INDEX_URL` | 国内直连 PyPI 实测 37–74 kB/s，`psycopg-binary` 会读超时 |

---

## 5. 故障排查

| 现象 | 排查 |
|---|---|
| `api` 起不来，日志报连不上 db | `docker compose logs db`；确认 `pg_isready` 通过、密码与 `.env.local` 一致 |
| 健康检查失败 | `docker exec zhenresume-api curl -fsS http://127.0.0.1:8000/api/v1/health` |
| 任务一直 pending | worker 没起来或队列名不符：`docker compose logs worker`（队列固定 `default`） |
| 前端能开但接口全失败 | 看浏览器 Network：多半是 `NEXT_PUBLIC_API_BASE` 构建期填错 → 改 `.env.local` 后**重新 build frontend** |
| CORS 报错 | `CORS_ORIGINS` 与实际访问地址不一致（含 `127.0.0.1` 与 `localhost` 两种写法） |
| 上传后报告为空 | api/worker 未共享 `zhenresume-appdata` 卷 |
| embedding 报错 | 容器需出网；回退层若指向宿主机 Ollama 要用 `host.docker.internal` |
| `verify.py` 报公平性 173/174 | **已知问题**：见 §7，非部署故障 |
| 端口被占 | 改 `.env.local` 的 `API_PORT` / `FRONTEND_PORT` / `*_HOST_PORT` |

---

## 6. 备选：中间件留在 VM（已归档）

若以后想回到"VM 跑中间件、本机跑应用"，做法是：

1. 把 `db`/`redis` 两个 service 从 compose 里去掉；
2. compose 里把 `DATABASE_URL` / `CELERY_*` 指向 `host.docker.internal:5432/6379`；
3. 在本机建 SSH 隧道（VM 的 compose 只绑了 `127.0.0.1`，直连不通）：
   `ssh -N -L 5432:127.0.0.1:5432 -L 6379:127.0.0.1:6379 <vm-user>@192.168.10.128`
   —— 用隧道而不是把 VM 端口改成 `0.0.0.0`，是因为 **Docker 的端口发布会绕过 ufw**，配错就等于整个局域网可连数据库。

隧道管理脚本保留在 [`scripts/vm-tunnel.ps1`](../scripts/vm-tunnel.ps1)（含免密检查、端口占用检查、连通性自检）。

---

## 7. 两个与部署无关的已知问题

1. **`verify.py` 的「公平性通过率 100%」约 1/3 概率失败**（173/174）。
   根因：`fairness = 1 − 歧视性拦截数 / 评估总数`，只要模型在任一条里写出受保护属性词
   （连"不要写婚育状况"这类**合规提醒本身**也会命中正则），921 项里 1 次就让指标变 0.9989。
   **安全行为是对的**（该条被正确拦截），脆的是这条**验收标准**。
2. **语义判定层暂不改排序**：实测其排序相关性 +0.838，低于纯规则的 +0.859，oracle 上界 +0.980。
   当前定位是"修正绝对分的系统性低估"，排序仍以规则口径为准。详见
   [P2-b 排序验证夹具与结论](P2-b_排序验证夹具与结论.md)。

---

## 8. 回退到"VM 跑全套"

```bash
# VM 上
sudo systemctl enable --now zhenresume.service      # 拉起 db+redis+api+worker
```
```powershell
# 本机
docker compose -f docker-compose.local.yml down     # 保留卷，便于再切回来
```
