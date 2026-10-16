# 虚拟机基础设施：PostgreSQL + Redis + Celery

> 目标：把**存储与执行**从"单机 SQLite + web 进程内跑"升级为
> **PostgreSQL（并发写）+ Redis 队列 + 独立 Celery worker（执行）**。
> 适用：VM `192.168.10.128`，代码在 `~/ZhenResume`，venv 在 `~/ZhenResume/backend/.venv`。
> 验证基线：本机（Windows + 本地 Redis）已用 `--pool=solo` 跑通 Celery 路径，
> **`scripts/verify` 174/174 全绿，5 个任务全部由 worker 执行**；PostgreSQL 因本机未安装，只做到"代码就绪 + 自检脚本"。

---

## 0. 先选部署形态：中间件装 Docker，还是直接装系统？**推荐 Docker**

| 形态 | 怎么用 | 适合 | 代价 |
|---|---|---|---|
| **A. 只把中间件放 Docker**（推荐先用） | `docker compose -f deploy/infra/docker-compose.yml up -d db redis` | 应用仍跑在宿主机（PyCharm 工作流不变） | 需把 `127.0.0.1:5432/6379` 写进 `backend/.env` |
| **B. 连应用一起容器化** | `docker compose -f deploy/infra/docker-compose.yml --profile app up -d --build` | 想要"开箱一致"、机器上不装 Python/PG/Redis | 改代码要重建镜像；`.env` 里的地址要换成**服务名** `db` / `redis` |
| C. 全部 `apt install` 装系统（本文 §2） | 见下面几步 | 不想引入 Docker | 版本漂移、升级/回滚麻烦；也是最容易和 PyCharm 环境互相干扰的一种 |

**为什么中间件更适合 Docker**：PG/Redis 的版本与参数是"基础设施"，Docker 里可以一条命令换版本、回滚、重装；
而且不会污染 VM 上系统级 Python / 服务。应用代码在宿主机用 PyCharm 改反而更快（形态 A）。

**形态 A 的配置**（`backend/.env`）：
```ini
DATABASE_URL=postgresql+psycopg://zhenresume:密码@127.0.0.1:5432/zhenresume
WORKFLOW_EXECUTOR=celery
CELERY_BROKER_URL=redis://:密码@127.0.0.1:6379/1
CELERY_RESULT_BACKEND=redis://:密码@127.0.0.1:6379/2
```
**形态 B 的配置**（`deploy/infra/.env.app`，api 与 worker 共用这一份）：
```ini
DATABASE_URL=postgresql+psycopg://zhenresume:密码@db:5432/zhenresume
CELERY_BROKER_URL=redis://:密码@redis:6379/1
CELERY_RESULT_BACKEND=redis://:密码@redis:6379/2
WORKFLOW_ENGINE=langgraph
WORKFLOW_PARALLEL=true
HR_SCREENING=true
HR_BATCH_CONCURRENCY=50
```

### Docker 操作命令（形态 A：只跑中间件）

```bash
cd ~/ZhenResume
cp deploy/infra/.env.example deploy/infra/.env      # 改两个密码
docker compose -f deploy/infra/docker-compose.yml up -d db redis
docker compose -f deploy/infra/docker-compose.yml ps              # 两个都应 healthy

# 应用侧（宿主机）自检：真连一次 PG/Redis
cd backend && ./.venv/bin/python -m scripts.check_infra
./.venv/bin/python -c "from app.db import init_db; init_db()"      # 建表 + 唯一索引
./.venv/bin/python -m scripts.seed_knowledge --force
./.venv/bin/python -m scripts.verify                                # 期望 174/174
```

形态 B 再加一句 `--profile app up -d --build`，然后 `docker compose -f ... logs -f worker` 看 worker 是否 `ready`。

**数据在哪 / 怎么备份**（都在具名卷里，删容器不丢）：
```bash
docker exec zhenresume-db pg_dump -U zhenresume zhenresume > backup-$(date +%F).sql
docker exec zhenresume-redis redis-cli -a 密码 -n 1 llen default     # 队列积压
# 彻底重装（会删数据）：docker compose -f deploy/infra/docker-compose.yml down -v
```

> 前端 `NEXT_PUBLIC_API_BASE` 仍指向 `http://192.168.10.128:8000`；
> 形态 B 下 api 容器已发布 8000 端口，地址不变，前端无需改。



1. **api 与 worker 必须共享同一个数据卷**（`zhenresume-appdata:/app/data`）——
   上传的简历文件在那里，worker 要读它。不共享 = 容器里跑的任务看不到宿主机上传的文件。
2. **容器内不能用 `127.0.0.1` 访问宿主机的 Ollama**。
   compose 里已加 `extra_hosts: host.docker.internal:host-gateway`，
   并把 `EMBEDDING_FALLBACK_BASE_URL` 指到 `http://host.docker.internal:11434/v1`。
   同理：如果 LLM 走的是宿主机上的自建网关，`ALIYUN_BASE_URL` 也要改成这个形式。
3. **`.env` 必须两份分开**：compose 自己读 `deploy/infra/.env`（密码、端口），
   应用读 `backend/.env`（或形态 B 的 `.env.app`）。混用最容易出现
   "worker 连到 A 库、web 查 B 库 → 任务永远 pending"。


---

## 1. 为什么要这三样（对应你定的两个目标）

| 目标 | 现在的瓶颈 | 这一层怎么解决 |
|---|---|---|
| 单份 ≤30s | ~~已解决~~（HR 排序表 29.6s） | —— |
| **100 份 ≤1 分钟** | ① SQLite 单写者，50 并发写会 `database is locked`；② 任务跑在 web 进程里，web 被长任务占住 | ① **PostgreSQL**；② **Celery worker**（加 worker 就加吞吐，重启不丢任务） |

**这两样不改变单份耗时**，改的是"能不能并发跑很多份"和"跑得稳不稳"。

---

## 2. 安装（Ubuntu / Debian）

### 2.1 PostgreSQL

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql

# 建业务库与用户（密码自己改，别用示例值）
sudo -u postgres psql -c "CREATE USER zhenresume WITH PASSWORD '改成你的强密码';"
sudo -u postgres psql -c "CREATE DATABASE zhenresume OWNER zhenresume ENCODING 'UTF8';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE zhenresume TO zhenresume;"

# 允许本机 TCP 连接（默认 peer 认证只允许 unix socket）
sudo sed -i "s/^#\?listen_addresses.*/listen_addresses = 'localhost'/" /etc/postgresql/*/main/postgresql.conf
sudo systemctl restart postgresql
```

> **DSN 必须用 psycopg3 方言**：`postgresql+psycopg://用户:密码@127.0.0.1:5432/zhenresume`
> 写成 `postgresql://` 或 `postgresql+psycopg2://` 都会在 SQLAlchemy 侧报错。
> 表结构不用手工建：`init_db()` 的 `create_all` 会建全部表，唯一索引也会自动补上。

**以后再上 pgvector（知识库涨到几万切片时）**：
```bash
sudo apt install -y postgresql-16-pgvector        # 版本号按实际
sudo -u postgres psql -d zhenresume -c "CREATE EXTENSION IF NOT EXISTS vector;"
pip install pgvector
```

### 2.2 Redis

```bash
sudo apt install -y redis-server
sudo sed -i "s/^#\?requirepass .*/requirepass 改成你的强密码/" /etc/redis/redis.conf
sudo sed -i "s/^#\?bind .*/bind 127.0.0.1/" /etc/redis/redis.conf
sudo systemctl enable --now redis-server
redis-cli -a 改成你的强密码 ping      # 期望 PONG
```

**DB 编号要分开**（Celery 的 broker 与 result backend 混在一个库会互相干扰）：

| 用途 | URL |
|---|---|
| 业务缓存（将来用） | `redis://:密码@127.0.0.1:6379/0` |
| Celery broker | `redis://:密码@127.0.0.1:6379/1` |
| Celery result backend | `redis://:密码@127.0.0.1:6379/2` |

### 2.3 Python 依赖

```bash
cd ~/ZhenResume/backend
./.venv/bin/python -m pip install -r requirements.txt
# 新增了 celery[redis]==5.6.3 / redis==5.3.1 / psycopg[binary]==3.3.6
```

---

## 3. 配置 `backend/.env`

```ini
# ---- 存储：切 PostgreSQL ----
DATABASE_URL=postgresql+psycopg://zhenresume:密码@127.0.0.1:5432/zhenresume

# ---- 执行模型：切 Celery ----
WORKFLOW_EXECUTOR=celery
CELERY_BROKER_URL=redis://:密码@127.0.0.1:6379/1
CELERY_RESULT_BACKEND=redis://:密码@127.0.0.1:6379/2

# 工作流引擎与并发（这两个决定 100 份能不能 1 分钟）
WORKFLOW_ENGINE=langgraph
WORKFLOW_PARALLEL=true
HR_SCREENING=true
HR_BATCH_CONCURRENCY=50
LLM_MAX_CONCURRENCY=4
```

> ⚠️ **web 与 worker 必须读同一份 `.env`**。
> 我在本机踩过这个坑：worker 用旧的 `DATABASE_URL` 启动，任务写进 A 库、web 从 B 库查，
> 表现为"任务永远 pending、worker 却显示 succeeded"，非常难查。
> systemd 里用 `EnvironmentFile` 指向同一个文件即可（见 §4）。

---

## 4. 两个 systemd 服务

### 4.1 web（如果原来是用 PyCharm 跑的，这一步顺手把它变成服务）

```ini
# /etc/systemd/system/zhenresume-api.service
[Unit]
Description=ZhenResume API
After=network.target postgresql.service redis-server.service

[Service]
User=<你的用户>
WorkingDirectory=/home/<你的用户>/ZhenResume/backend
EnvironmentFile=/home/<你的用户>/ZhenResume/backend/.env
Environment=PYTHONUTF8=1
ExecStart=/home/<你的用户>/ZhenResume/backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 4.2 Celery worker

```ini
# /etc/systemd/system/zhenresume-worker.service
[Unit]
Description=ZhenResume Celery worker
After=network.target postgresql.service redis-server.service

[Service]
User=<你的用户>
WorkingDirectory=/home/<你的用户>/ZhenResume/backend
EnvironmentFile=/home/<你的用户>/ZhenResume/backend/.env
Environment=PYTHONUTF8=1
# --concurrency 不用等于候选人数：一个批次是一个任务，批内自己用 asyncio 并发
ExecStart=/home/<你的用户>/ZhenResume/backend/.venv/bin/celery -A app.worker worker -l INFO -Q default --concurrency=4
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zhenresume-api zhenresume-worker
sudo systemctl status zhenresume-worker --no-pager
```

> **队列名必须一致**：`worker.py` 里显式设了 `task_default_queue="default"`，所以 worker 用 `-Q default`。
> 若两边不一致（Celery 默认队列其实叫 `celery`），症状是 worker 显示 ready、任务不执行、
> `redis-cli llen celery` 一直涨 —— 我在本机就是这样踩了一次。

---

## 5. 切换顺序（**不要跳步**）

```bash
cd ~/ZhenResume/backend

# 1) 先自检基础设施（真实连接，不是看配置）
./.venv/bin/python -m scripts.check_infra
#    期望：全部 PASS。有 FAIL 就先修，别往下走。

# 2) 建表 + 灌知识库种子（PG 上是空库）
./.venv/bin/python -c "from app.db import init_db; init_db()"
./.venv/bin/python -m scripts.seed_knowledge --force

# 3) 端到端契约自检（executor=celery 时会自动轮询等 worker 完成）
./.venv/bin/python -m scripts.verify
#    期望：174/174。这一条过了再切生产。

# 4) 起服务
sudo systemctl restart zhenresume-api zhenresume-worker
curl -s http://127.0.0.1:8000/api/v1/health
```

**回退**：`WORKFLOW_EXECUTOR=inline` + 注释掉 `DATABASE_URL`（回到 SQLite）→ 重启；
两条路径业务语义完全一致（同一批 `services/*` 与图），只是"谁来跑、写哪个库"不同。

---

## 6. 上线后怎么盯

| 看什么 | 怎么看 | 正常/异常 |
|---|---|---|
| 队列是否积压 | `redis-cli -n 1 llen default` | 稳态接近 0；持续涨说明 worker 没消费 |
| worker 是否活着 | `systemctl status zhenresume-worker`；日志里应有 `celery@xxx ready` | 不在 = 任务只会排队 |
| 任务是否卡住 | `sqlite`→PG 后：`SELECT status, count(*) FROM analysis_tasks GROUP BY 1;` | 大量 `running` 超过 10 分钟 = 有任务卡住（僵尸回收会在重启时处理） |
| 批次进度 | `GET /api/v1/hr/batches/{id}` 的 `finished/total` | 长时间 `finished` 不动 = 撞 LLM 限流 |
| 写锁/连接 | PG：`SELECT count(*) FROM pg_stat_activity;` | 连接数暴涨 = 漏关会话 |

**并发怎么调**：
- `HR_BATCH_CONCURRENCY`（批次内并发份数，默认 50）——**在飞 LLM 请求 ≈ 本值 × 2**，别超过阿里云配额；
- worker `--concurrency`（默认 4）——一个任务是一份诊断**或整个批次**，不需要等于候选人数；
- 想加吞吐先加 worker 进程，再考虑加 `--concurrency`；两个都加要注意总在飞请求数。

---

## 7. 本机已完成的验证（诚实标注）

| 项 | 状态 |
|---|---|
| Celery 代码接线（`app/worker.py` / `app/queue.py` / 路由分派） | ✅ 已实现 |
| Celery 端到端跑通（本地 Redis + `--pool=solo`） | ✅ **`verify` 174/174，5 个任务由 worker 执行** |
| `verify.py` 在 executor=celery 下自动轮询 | ✅ 已加（4 处任务创建点） |
| inline 模式回归 | ✅ 174/174 不回归 |
| 基础设施自检脚本 `scripts/check_infra.py` | ✅ 本机 6/6 通过（Redis 读写 + Celery broker） |
| **PostgreSQL 实机验证** | ❌ 本机没装 PG、Docker 未启动 → **只做到代码就绪 + 自检脚本，需在 VM 上跑一次 `check_infra --pg` + `verify`** |
