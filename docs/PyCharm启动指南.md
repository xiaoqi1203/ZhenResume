# 在 PyCharm 里启动 ZhenResume

> 适用：Windows 宿主机 + `E:\ZhenResume` 代码，PyCharm 2026.2.3 专业版（本机实际环境）。
> 三套环境完全独立，**选一套，不要混用**（混用最常见的症状是"页面能开但一直 pending / 数据不见了"）。

| 方案 | 后端跑在哪 | 依赖 | 适合 |
|---|---|---|---|
| **A. 本机原生（推荐日常改代码）** | PyCharm 里 `uvicorn` | `backend/.venv`（已建好） | 打断点调试、改代码热重载 |
| **B. 本机 Docker 全栈（你当前正在跑的就是它）** | Docker 容器 | Docker Desktop | 复现"完整生产链路"：PG + Redis + Celery + langgraph |
| **C. 虚拟机 192.168.10.128** | Ubuntu VM | SSH 远程解释器 | 部署验收，不用于日常开发 |

---

## 方案 A：本机原生启动（最直接的"在 PyCharm 里点运行"）

### A-0. 先确认端口没被占用（关键）

你**现在** 8000 / 3000 端口已经被 Docker 全栈占着：

```
zhenresume-api        0.0.0.0:8000->8000      Up (healthy)
zhenresume-frontend   0.0.0.0:3000->3000      Up (healthy)
```

所以本机原生启动前必须先停掉它们，否则 PyCharm 里点运行会报
`[Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000)`。

PyCharm 里停：右侧 **Services** 工具窗 → 选中 `zhenresume` compose → **Down**（保留数据卷）；
或者终端：

```powershell
cd E:\ZhenResume
docker compose -f docker-compose.local.yml down      # 保留卷，下次 up -d 直接回来
```

> 只想停应用、保留 PG/Redis 也行：`docker compose -f docker-compose.local.yml stop api worker frontend`。
> 但要连 PG/Redis，方案 A 的配置得改成走 Docker 那两个容器，见 A-4 第 2 种跑法。

> 8000 端口被谁占着也能随时确认：
> `Get-NetTCPConnection -State Listen -LocalPort 8000 | Select LocalPort,OwningProcess`
> （进程名是 `com.docker.backend` / `wslrelay` 就是 Docker 容器；是 `python` 就是上次没关干净的后端）。

### A-1. 解释器（SDK）

`Settings → Project: ZhenResume → Python Interpreter` → 选择 **`E:\ZhenResume\backend\.venv`**（Python 3.10.8）。

- 若下拉里没有：`Add Interpreter → Add Local Interpreter → Existing` → 浏览到
  `E:\ZhenResume\backend\.venv\Scripts\python.exe`。
- 不要选 `D:\PythonVersion\python.exe`（那是基座，没装依赖）。
- ⚠️ 项目里 `misc.xml` 当前记的是 `Docker Compose (backend)` 这个特殊 SDK，所以下拉可能显示成
  "Docker Compose (backend)"；把 Python Interpreter 显式改成 venv 那条即可。

### A-2. 新建运行配置（Uvicorn）

`Run → Edit Configurations… → + → Python`

| 字段 | 值 |
|---|---|
| Name | `Backend (uvicorn :8000)` |
| Module name（**别选 Script**） | `uvicorn` |
| Parameters | `app.main:app --reload --host 127.0.0.1 --port 8000` |
| Working directory | `E:\ZhenResume\backend` |
| Python interpreter | `backend\.venv` 的 python |
| Environment variables | `PYTHONUTF8=1` |
| **Emulate terminal in output console** | ✅ 勾上（Windows 控制台编码问题） |

> 用 Module name 而不是 Script，是为了让 PyCharm 不去解析 `uvicorn.exe` 的启动脚本，
> 同时 `app.main:app` 里的 `app` 包能被正确识别（依赖 working directory = `backend`）。

### A-3. 为什么不直接跑 `app/main.py`

直接右键 Run `main.py` **只会导入模块然后退出**，不会起 HTTP 服务（文件里没有 `if __name__ == "__main__"`）。
必须通过 uvicorn 入口。想保留这个习惯可以在 `main.py` 末尾自己加：

```python
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
```

（加了之后就能用 PyCharm 的 `Python` 配置直接跑 `main.py`，断点依然有效。）

### A-4. 配置文件：本机原生默认读 `backend/.env`

后端**只**从 `backend/.env` 读配置（`config.py` 里 `env_file=BACKEND_DIR / ".env"`，绝对路径。
所以 PyCharm 里工作目录填错也不会"数据不见了"）。当前那份 `.env` 是**本机原生模式**的：
`LLM_PROVIDER` 链走 aliyun → deepseek、`WORKFLOW_EXECUTOR=inline`、`WORKFLOW_ENGINE=legacy`、
SQLite `backend/data/zhenresume.db`、无 Celery。

> ✅ 这份 `.env` 已实测可直接用：`python -m uvicorn app.main:app` 起在 8000 上返回
> `llm_mode: live`，工作流引擎 `legacy`、checkpoint `off`，无需任何额外配置。
> 它与 Docker 全栈那套（PG + Redis + Celery + langgraph，见方案 B）用的是**两个不同的数据库**：
> 本机原生看的是 `backend/data/zhenresume.db`，Docker 看的是 `zhenresume-pgdata` 卷里的 PG。
> 所以"切方案后数据不见了"是正常的，不是丢数据。

三种跑法任选其一：

1. **纯本机（最简单）**：什么都不用改，直接跑。SQLite + inline 工作流，全链路可通。
2. **本机后端 + Docker 里的 PG/Redis**：把 `.env` 末尾追加（密码取 `.env.local`）：

   ```ini
   DATABASE_URL=postgresql+psycopg://zhenresume:1203@127.0.0.1:5432/zhenresume
   WORKFLOW_ENGINE=langgraph
   WORKFLOW_MODE=agentic
   WORKFLOW_EXECUTOR=inline
   CELERY_BROKER_URL=redis://:1203@127.0.0.1:6379/1
   CELERY_RESULT_BACKEND=redis://:1203@127.0.0.1:6379/2
   ```

   注意 `WORKFLOW_EXECUTOR` 保持 `inline`：用 `celery` 就必须再起一个 worker
   （`celery -A app.worker worker -l INFO -Q default`），否则任务会一直 pending。
3. **强制 celery worker 也进 PyCharm**：新建第二个 Python 配置，Module name `celery`，
   Parameters `-A app.worker worker -l INFO -Q default --pool=solo`，working directory `backend`，
   然后把 `.env` 里 `WORKFLOW_EXECUTOR` 改成 `celery`。

### A-5. 前端（Next.js）

前端不是 Python，PyCharm 里两个做法：

**A-5a. 复用 npm 配置**（需要先在 PyCharm 设置里指 Node）：

`Settings → Languages & Frameworks → Node.js` → Node interpreter 选
`C:\Program Files\nodejs\node.exe`；Package manager 那栏 PyCharm 不会自带 pnpm，需要手填
**`%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\pnpm\bin\pnpm.cjs`**
（项目就是用这个 pnpm 装的，README 第十一节有写）。

然后 `Run → Edit Configurations… → + → npm`：

| 字段 | 值 |
|---|---|
| Name | `Frontend dev (:3000)` |
| package.json | `E:\ZhenResume\frontend\package.json` |
| Command | `run` |
| Scripts | `dev` |

**A-5b. 直接用终端跑 pnpm**（本机 PATH 里**没有** pnpm，只能用下面这条完整路径）：

```powershell
cd E:\ZhenResume\frontend
$node = "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
$pnpm = "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\pnpm\bin\pnpm.mjs"
& $node $pnpm dev        # http://127.0.0.1:3000
```

`frontend/node_modules` 和 `.env.local` 都已在位，不用再 `pnpm install`。

> ⚠️ `frontend/.env.local` 当前写的是 `NEXT_PUBLIC_API_BASE=http://192.168.10.128:8000`（指向虚拟机）。
> 本机跑前后端时改成 `http://127.0.0.1:8000` 并**重启** dev server（`NEXT_PUBLIC_*` 是构建期内联的）。
> 不改的话页面能打开，但所有接口请求都会打到 VM 上 / 被 CORS 拒绝，症状是"页面空白、一直转圈"。

### A-6. 冒烟验证

| 地址 | 期望 |
|---|---|
| http://127.0.0.1:8000/ | `{"name":"ZhenResume 简甄",...}` |
| http://127.0.0.1:8000/docs | Swagger 打开，13 组路由 |
| http://127.0.0.1:8000/api/v1/health | `llm_mode` = `live`（`backend/.env` 有 Key） |
| http://127.0.0.1:3000/editor | 简历编辑器 |

---

## 方案 B：Docker 全栈（当前运行中的那套）

在 PyCharm 里把它当普通 Compose 服务管理，**只能启动/停止/看日志，不能打断点**：

1. `Settings → Build, Execution, Deployment → Docker` → 确认 Docker 连接是绿色（本机走
   `E:\Docker-work\resources\com.docker.backend.exe`，`docker-desktop` WSL2 发行版）。
2. 打开 `docker-compose.local.yml` → 编辑器侧栏点 **双三角（Create Docker Compose run configuration）**
   → 自动生成运行配置 → 它会在 Services 工具窗里出现。
3. **必须让 compose 读到 `.env.local`**：这个文件里有 `POSTGRES_PASSWORD` / `REDIS_PASSWORD`
   两个必填变量（`${POSTGRES_PASSWORD:?...}`），拿不到就直接报
   `required variable POSTGRES_PASSWORD is missing a value`。最稳的是在运行配置的
   **Environment variables** 里加一行：

   ```
   COMPOSE_ENV_FILES=.env.local
   ```

   （等价于命令行 `docker compose -f docker-compose.local.yml --env-file .env.local up -d --build`。
   注意 `backend/.env` 是 compose 里 `env_file:` 引用的**容器环境**，和这个 `--env-file`
   是两回事，两者都要在。）
4. `api` / `worker` 两个容器**不挂载源码**（只有 `zhenresume-appdata:/app/data` 一个卷），
   所以改了 `backend/app/**.py` 必须 `up -d --build api worker` 才会生效，容器里没有热重载。
5. ⚠️ 仓库根目录的 `docker-compose.yml` **默认是停用的**（两个服务都在 `legacy` profile 里），
   而且 `.idea/workspace.xml` 里原来那条 `docker-compose.yml: Compose 部署` 配置指向的就是它
   —— 跑起来是**空的**，别用。它存在是为了防止 PyCharm 自动拉起容器抢 8000 端口。
6. 常用动作：Services 窗口里右键容器 → `Logs` / `Restart` / `Down`。
   停止：`docker compose -f docker-compose.local.yml down`（保留卷）。
7. 想改容器里的配置：容器环境变量来自 compose 的 `environment:` + `backend/.env`（`env_file:`），
   **改了 `backend/.env` 要 `up -d` 重建容器**才生效，`restart` 不够。

---

## 方案 C：虚拟机（SSH 远程解释器）

完整步骤见 [vm-deploy.md](vm-deploy.md)。PyCharm 侧要点：

- `Settings → Python Interpreter → Add Interpreter → On SSH` → 宿主 `xiaoqi@192.168.10.128:22`。
- Deployment（`Tools → Deployment`）已在项目 `.idea/deployment.xml` 里配好，映射到
  `/home/xiaoqi/ZhenResume`，且 `myAutoUpload=ALWAYS`。
  ⚠️ **这意味着每保存一个文件就上传**，而且会把宿主机 `backend/.env` 整个推过去覆盖 VM 的
  `CORS_ORIGINS`（VM 那版已把 `192.168.10.128:3000` 也写进去了，所以目前两边一致，别再各自改）。
- 不需要 / 不应该同步：`.venv`、`node_modules`、`.next`（平台相关二进制，VM 里要自己装）。
- 运行配置同 A-2，只是解释器换成 SSH 那条，working directory 换成 `/home/xiaoqi/ZhenResume/backend`。

---

## 五个"点了运行但没起来"的常见原因

| 现象 | 原因 | 处理 |
|---|---|---|
| `error while attempting to bind on address ('127.0.0.1', 8000)` | Docker 容器占了端口 | 先 `docker compose -f docker-compose.local.yml down` |
| `ModuleNotFoundError: No module named 'app'` | 工作目录不是 `backend/` | 运行配置 Working directory 改成 `E:\ZhenResume\backend` |
| 运行一下立刻 `Process finished with exit code 0` | 跑的是 `main.py`（没有 `__main__` 入口） | 改用 A-2 的 Module name `uvicorn` 配置 |
| `ImportError: cannot import name 'Self' from 'typing'` | venv 解释器与已装扩展 ABI 不一致 | 见 README 第十一节，删 `.venv` 重建 |
| 页面能开、任务一直 pending | 前端打的 `/task` 没到后端，或 executor=celery 但没 worker | 检查 `NEXT_PUBLIC_API_BASE` + `WORKFLOW_EXECUTOR` |

---

## 一览：打断点调试该选哪个

- 想**在 `backend/app/**.py` 里下断点** → 必须方案 A（源码在磁盘上，`--reload` 生效）。
- 只想**看整套服务跑起来** → 方案 B，用 `docker logs -f zhenresume-api` 看日志。
- 想**断点又不想改 `backend/.env`** → 方案 A + 运行配置里勾掉 `.env`、用 Environment variables
  面板逐条填配置（PyCharm 的环境变量优先级高于 `pydantic-settings` 读的文件）。
