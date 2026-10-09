# 用 Docker 跑 ZhenResume

适用场景：不想在机器上装 Python / Node / pnpm，用容器一条命令起前后端。
本地开发（直接 `uvicorn` + `pnpm dev`）仍然按 [README](../README.md) 第一节走，两者互不影响。

---

## 1. 最短路径

```bash
docker compose up -d --build
```

| 服务 | 地址 |
|---|---|
| 前端 | <http://localhost:3000> |
| 后端 | <http://localhost:8000> |
| 交互式文档 | <http://localhost:8000/docs> |
| 健康检查 | <http://localhost:8000/api/v1/health> |

**不需要 API Key**：未配置 `DEEPSEEK_API_KEY` 时后端自动进入 `mock` 模式，
报告由规则引擎生成，全链路（上传 → 解析 → 8-Agent → 报告 → 支付 → 知识库 → HR 批量）照样跑通。
要接真实模型：

```bash
copy .env.docker.example .env      # Windows
cp .env.docker.example .env        # Linux / macOS
# 编辑 .env：DEEPSEEK_API_KEY=sk-xxxx
docker compose up -d --build
```

常用运维命令：

```bash
docker compose ps                    # 看健康状态
docker compose logs -f backend       # 跟后端日志（首启会看到"知识库已灌入 16 篇种子文档"）
docker compose exec backend python -m scripts.verify   # 容器内跑端到端契约自检
docker compose down                  # 停服务，数据保留在具名卷里
docker compose down -v               # 连数据一起删（慎用）
```

---

## 2. 文件清单

| 文件 | 作用 |
|---|---|
| `docker-compose.yml` | 编排前后端、端口、数据卷、健康检查与启动顺序 |
| `backend/Dockerfile` | 后端镜像：python:3.12-slim，非 root，`/app/data` 数据卷 |
| `backend/.dockerignore` | 挡住 `.venv`（平台相关二进制）与 `data/`（用户简历原文） |
| `frontend/Dockerfile` | 前端镜像：多阶段 deps → builder → runner，只带 standalone 产物 |
| `frontend/.dockerignore` | 挡住 `node_modules` / `.next` |
| `.env.docker.example` | compose 变量模板，复制成 `.env` 使用 |

**唯一为容器改动的源码**：`frontend/next.config.mjs` 增加了 `output: 'standalone'`。
它只影响 `next build` 的产物形状，本地 `dev` / `build` / `start` 行为不变。

---

## 3. 数据持久化

容器里的业务数据只有两处，都在 `/app/data`，由具名卷 `zhenresume-data` 承载：

- `zhenresume.db` —— SQLite 库（表结构对齐 aaa.md 的 DDL 草案）
- `uploads/` —— 用户上传的简历原文件

容器重建、`docker compose down`、升级镜像都不会丢。数据库路径由 compose 显式写成
绝对路径 `/app/data/zhenresume.db`，因此与容器工作目录无关——这是
[backend/.env.example](../backend/.env.example) 第 13-24 行特别强调过的约定。

备份：

```bash
docker compose exec backend sh -c "cp /app/data/zhenresume.db /app/data/backup.db"
docker cp zhenresume-backend:/app/data/zhenresume.db ./zhenresume-backup.db
```

> 别只拷 `-wal` / `-shm`：那是运行中的临时文件（README 与 vm-deploy 都提过）。
> 要连 WAL 一起拿就先 `docker compose stop backend` 再拷。

---

## 4. 网络与 CORS：一个必须理解的限制

前端调后端的地址来自 `NEXT_PUBLIC_API_BASE`，而 `NEXT_PUBLIC_*` 是**构建期内联**的
（见 `frontend/lib/api.ts:53`），不是运行期读取。这带来两个后果：

1. **改了后端地址必须重新 build**，只重启容器无效：
   ```bash
   NEXT_PUBLIC_API_BASE=http://192.168.1.10:8000 docker compose up -d --build
   ```
2. 这个地址是**浏览器**去访问的地址，不是容器之间互相访问的地址。
   容器里的 `http://backend:8000` 对浏览器毫无意义。

所以 compose 用 `docker compose up` 暴露两个端口，前端直连后端。默认组合：

| 项目 | 值 | 说明 |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000` | 浏览器在宿主机上时可用 |
| `CORS_ORIGINS` | `http://127.0.0.1:3000,http://localhost:3000` | `127.0.0.1` 与 `localhost` 算**两个**来源 |
| `FRONTEND_BASE_URL` | `http://localhost:3000` | 分享链接（`/share/<id>`）前缀 |

**换机器 / 换域名时三个值要一起改**，否则会分别表现为：前端请求打到旧地址、
浏览器报 CORS、分享链接指不到前端。

```bash
# 例：部署到 192.168.1.10
NEXT_PUBLIC_API_BASE=http://192.168.1.10:8000
CORS_ORIGINS=http://192.168.1.10:3000
FRONTEND_BASE_URL=http://192.168.1.10:3000
```

改完 `NEXT_PUBLIC_API_BASE` 一定要 `--build`；后两个是后端运行期环境变量，
`docker compose up -d` 重建容器即可生效。

> 想彻底摆脱"构建期写死地址"，标准做法是在前端前面加一层反向代理（Nginx / Caddy），
> 让浏览器只访问一个 origin，由代理把 `/api/v1/*` 转给后端。本项目没做这层，
> 因为那要改前端 API 基址的取值方式，超出"补全 Dockerfile"的范围。需要时再单独加。

---

## 4.5 在 Linux 虚拟机里跑（PyCharm 远程 / SSH）

本机 Windows 与 VM 里的 Linux 是**两个独立的 Docker daemon**，镜像不互通：
在 Windows 上 `docker build` 出来的镜像，VM 里看不到，必须在 VM 侧重新构建一次。
下面几件事按顺序做，否则会卡在很奇怪的地方。

### Step 1 — 先把代码同步进 VM

Dockerfile 是刚加的，**必须让它们出现在 VM 上**，否则 VM 里连构建都开不了头。
PyCharm：`Tools → Deployment → Upload to 'xiaoqi@<VM_IP>'`（全量上传一次），
之后靠 `Options` 里的自动上传保持同步。

跨平台注意两点：

- **换行**：Windows 检出可能是 CRLF。本项目 Dockerfile 里没有需要执行的 shell 脚本
  （只有 `RUN` 行内联命令），CRLF 不影响构建；但如果你以后加了 `entrypoint.sh`，
  记得 `RUN sed -i 's/\r$//' entrypoint.sh` 或在 `.gitattributes` 里钉 `eol=lf`。
- **别把宿主机二进制传上去**：`.venv` / `node_modules` / `.next` 必须排除，
  镜像里会自己装（`docs/vm-deploy.md` 开头就是这条警告）。

### Step 2 — 确认 VM 上的 Docker 能拉镜像（最常卡这里）

```bash
docker version          # 确认 daemon 在跑
docker compose version  # 需要 v2（`docker compose`，不是 `docker-compose`）
```

国内网络下 VM 直连 Docker Hub 大概率超时。给 VM 配镜像加速：

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": ["https://docker.1ms.run", "https://docker.m.daocloud.io"]
}
EOF
sudo systemctl restart docker
docker pull python:3.12-slim   # 验证能拉通
```

构建期的**包源**也要在 VM 里换掉（VM 通常没有宿主机那层代理）：

```bash
cd ~/ZhenResume
cp .env.docker.example .env
# 编辑 .env，填：
#   PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
#   NPM_REGISTRY=https://registry.npmmirror.com
```

### Step 3 — 构建与启动

```bash
cd ~/ZhenResume
docker compose up -d --build
docker compose ps                       # 两个都要 healthy
docker compose exec backend python -m scripts.verify   # 期望 174/174 或 177/177
```

### Step 4 — 浏览器地址要跟 VM 的 IP 对齐

VM 里跑的时候，**宿主机浏览器**访问的是 VM 的 IP，不是 `localhost`。
所以 `.env` 里这三个值都要换成 VM 的 IP（`hostname -I` 拿）：

```ini
NEXT_PUBLIC_API_BASE=http://192.168.10.150:8000
CORS_ORIGINS=http://192.168.10.150:3000
FRONTEND_BASE_URL=http://192.168.10.150:3000
```

改完 `NEXT_PUBLIC_API_BASE` **必须 `--build` 重新构建前端**（它是构建期内联的），
只重启容器无效。

### 在 PyCharm 里把解释器指向 VM 的 Docker

`Settings → Project → Python Interpreter → Add Interpreter → On Docker`，
把 Docker 连接设成 SSH 到 VM：

| 项 | 值 |
|---|---|
| Docker 连接 | `New → SSH`，Host 填 VM IP，User `xiaoqi` |
| 或手动指定 | `DOCKER_HOST=ssh://xiaoqi@192.168.10.150` |

> 注意 PyCharm 的 Docker 解释器只负责"跑 Python"，**不等于** `docker compose up`。
> 起整个应用仍然用上面 Step 3 的命令（或配一个 PyCharm 的 Docker Compose 运行配置）。

---

## 5. 健康检查与启动顺序

- 后端 `HEALTHCHECK` 打 `http://127.0.0.1:8000/api/v1/health`；
  `start-period=30s` 是给"建表 + 灌 16 篇种子文档"留的时间。
- 前端用 `docker compose` 的 `depends_on: backend: condition: service_healthy`，
  等后端就绪才启动，避免首屏就报 `NETWORK_ERROR`。
- 前端健康检查打 `/`（返回 200 即视为就绪）。

容器内的自检（比健康检查严格得多，会跑完整业务链路）：

```bash
docker compose exec backend python -m scripts.verify
```

期望输出 `174/174` 或 `177/177` 全过。差的 3 项是 PDF 导出检查项——
容器默认没装 LibreOffice，脚本会自动降级为"返回 503 + 错误码正确"两项，
所以数量浮动属预期（详见 [vm-deploy.md](./vm-deploy.md) 第 4 节）。

---

## 6. PDF 导出（可选，默认关闭）

简历导出 PDF 的链路是 DOCX → LibreOffice → PDF，`backend/app/services/resume_render.py`
会调外部 LibreOffice Kit 的 CLI。容器默认**不装**它，于是：

- `pdf_enabled = False` → 导出 PDF 返回 `503 PDF_UNAVAILABLE`
- 前端会引导用户改用 DOCX 或浏览器 `Ctrl+P` 打印
- 其余功能完全不受影响

这个降级是产品设计的一部分（宁可明确失败，也不静默产出坏文件），不是配置不全。
确实需要容器内出 PDF，就按 [backend/Dockerfile](../backend/Dockerfile) 末尾的注释
加两段 `RUN`/`ENV`（装 `libreoffice-writer` + `fonts-noto-cjk`，并在 `.env` 里填两个路径）。
镜像会因此增大 500MB~1GB。

---

## 7. 单独构建镜像

前后端镜像彼此独立，各自的构建上下文就是各自的目录：

```bash
docker build -t zhenresume-backend:1.0.0 ./backend
docker build -t zhenresume-frontend:1.0.0 \
  --build-arg NEXT_PUBLIC_API_BASE=http://localhost:8000 ./frontend

# 只跑后端
docker run -d --name zr-backend -p 8000:8000 \
  -v zhenresume-data:/app/data zhenresume-backend:1.0.0
```

内网环境可覆盖 pip 源：

```bash
docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  -t zhenresume-backend:1.0.0 ./backend
```

---

## 8. 已知限制与排查

| 现象 | 原因 / 处理 |
|---|---|
| 前端页面能打开但所有请求报 `NETWORK_ERROR` | `NEXT_PUBLIC_API_BASE` 是构建期写死的，且必须是浏览器能访问的地址。确认 `docker compose config \| findstr NEXT_PUBLIC` 的值，改完要 `--build` |
| 浏览器控制台报 CORS | `CORS_ORIGINS` 要和地址栏里前端的 **origin 完全一致**（协议+主机+端口）；`127.0.0.1` 与 `localhost` 算两个来源 |
| 导出 PDF 返回 503 `PDF_UNAVAILABLE` | **预期行为**，见第 6 节 |
| `docker compose` 提示 `env file .env not found` | 无关紧要：`.env` 可选。想消除提示就 `copy .env.docker.example .env` |
| 改了 `.env` 但不生效 | 分清两个 `.env`：根目录 `.env` 只做 compose 变量替换；`backend/.env` 是本地直跑后端用的，容器不读它。容器内后端配置统一在 `docker-compose.yml` 的 `backend.environment` |
| 页面样式丢失 / 无 CSS | standalone 产物不含 `.next/static`，Dockerfile 里已单独 COPY；若自行改过 runner 阶段，确认这行还在 |
| 数据"不见了" | 检查是否用了 `docker compose down -v`（删卷），或误把数据写到了容器可写层。数据只认 `/app/data` 这个具名卷 |
| Apple Silicon（arm64）上想跑 x86 镜像 | 本项目两个镜像都支持多架构构建，直接 `docker compose up -d --build` 即可；不要用 `--platform linux/amd64` 硬拉，会让 Python 扩展变慢 |
