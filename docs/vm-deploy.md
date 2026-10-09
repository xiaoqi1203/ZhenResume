# 在 Linux 虚拟机里运行 ZhenResume（PyCharm SSH 远程解释器）

适用场景：宿主机是 Windows，代码在 `E:\ZhenResume`；目标是在 **Ubuntu / Debian 虚拟机**里跑后端，
用 PyCharm 的 **SSH 远程解释器**（Professional 功能）连过去开发调试。

> **先记住一句话：不要把宿主机的 `.venv`、`node_modules`、`.next` 同步进虚拟机。**
> 它们是平台/ABI 相关的二进制（本项目已因"3.10 解释器 + cp312 扩展"崩过一次）。
> 虚拟机里必须自己装一份。

---

## 1. 客户机准备（Ubuntu / Debian）

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

# 需要 Python ≥ 3.10（Ubuntu 22.04 是 3.10，24.04 是 3.12，都行）
python3 --version
```

只跑后端的话到这里就够了。若**前端也要在虚拟机里跑**，再装 Node：

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo corepack enable && corepack prepare pnpm@latest --activate
node -v && pnpm -v
```

拿到虚拟机 IP（后面配 CORS 和前端地址要用）：

```bash
hostname -I
```

---

## 2. 把代码送进虚拟机（三选一）

### 方式 A：PyCharm Deployment + SSH 远程解释器（你说的这条路）

**Step 1 — 配置 Deployment（文件同步）**
`Tools → Deployment → Configuration` 新增一个 SFTP：

| 项 | 值 |
|---|---|
| Host / Port | 虚拟机 IP / 22 |
| Root path | `/home/<user>/ZhenResume` |
| Mappings · Local path | `E:\ZhenResume` |
| Mappings · Deployment path | `/`（相对 Root path） |

**⚠️ 关键一步 —— 配排除项**，否则会把 400+ MB 的平台相关二进制传上去，还会覆盖虚拟机自己的环境：
`Tools → Deployment → Options → Exclude items by name` 填：

```
.venv;node_modules;.next;.shots;data;__pycache__;*.pyc;.pytest_cache
```

然后在 `Options` 里把 **Upload changed files automatically to the default server** 设为
`On explicit save action`（或 `Always`）。首次用 `Tools → Deployment → Upload to ...` 全量上传一次。

> 官方文档里还有一个更快的入口：切换到 SSH 解释器后，PyCharm 右下角会弹一个气泡，
> 点 **Sync and auto-upload files** 就会立刻同步一次、并在之后每次保存时自动上传。
> 详见 [Configure an interpreter using SSH](https://www.jetbrains.com/help/pycharm/configuring-remote-interpreters-via-ssh.html)。

**Step 2 — 配置远程解释器**
`Settings → Project → Python Interpreter → Add Interpreter → On SSH`：

- 填 SSH 连接信息 → 选 `System Interpreter` 或 `Virtualenv`
- **推荐让 PyCharm 在虚拟机里新建 venv**：选 `New`，路径填
  `/home/<user>/ZhenResume/backend/.venv`，解释器选 `/usr/bin/python3`
- 建完后 PyCharm 会提示安装 `requirements.txt`，直接装上（等价于 `pip install -r requirements.txt`）

> **如果看不到 `On SSH` 这个选项**：SSH 远程解释器依赖以下插件，先到
> `Settings | Plugins → Installed` 确认它们已启用：`Python`、`FTP/SFTP/WebDAV Connectivity`、
> `SSH Remote Run`、`Remote Execution Agent`。老版本（2024.x 及更早）里这项属于
> Professional 专有功能，社区版没有；若你的版本/授权确实不包含，替代方案是
> **VS Code Remote-SSH**（免费）或直接在虚拟机内开发、宿主机只当终端。
>
> 另有一条官方限制：**不能用 Windows 机器作为 SSH 远程主机**。我们的方向是
> Windows 宿主机 → Linux 客户机，不受影响。

**Step 3 — 运行/调试配置**
`Run → Edit Configurations → Add → Python`：

| 项 | 值 |
|---|---|
| Script / Module | **module** 模式，模块名 `uvicorn` |
| Parameters | `app.main:app --host 0.0.0.0 --reload --port 8000` |
| Working directory | `/home/<user>/ZhenResume/backend`（**必须是这一层**，否则 `app` 包导入不到） |
| Python interpreter | 上面那个远程解释器 |
| Environment variables | `PYTHONUTF8=1`（保险起见，避免非 UTF-8 locale 下中文出错） |

> `--host 0.0.0.0` 是必须的：写 `127.0.0.1` 的话只在虚拟机内部监听，
> 宿主机浏览器访问不到。

### 方式 B：用 git（最省事，天然不会带二进制）

`.gitignore` 已经把 `.venv` / `node_modules` / `.next` / `data` 全部排除，所以走 git 不会有污染问题。

```bash
# 虚拟机里
git clone <你的仓库地址> ~/ZhenResume
```

### 方式 C：tar 管道直接推（不需要 git 服务器）

Windows 自带 `tar` 与 OpenSSH：

```powershell
# 在宿主机执行
tar -czf - --exclude=.venv --exclude=node_modules --exclude=.next --exclude=.shots --exclude=data --exclude=__pycache__ -C E:\ZhenResume backend |
  ssh <user>@<VM_IP> "mkdir -p ~/ZhenResume && tar -xzf - -C ~/ZhenResume"
```

---

## 3. 虚拟机里的 `.env`（唯一需要按环境改的地方）

结论先说：**后端 `.env` 里通常只有两行需要改**（`CORS_ORIGINS` 与 `FRONTEND_BASE_URL`），其余保持原样。

### `.env` 会不会被带过去？

| 传输方式 | `.env` |
|---|---|
| PyCharm Deployment（SFTP 同步） | **会** —— 它就是个普通文件，不在默认排除列表里（除非你自己把它加进排除项） |
| git clone / tar 管道 | **不会** —— `.env` 被 `.gitignore` 排除 |
| 手工 `cp .env.example .env` | 会，但见下面"为什么只写这几项" |

同步/传完之后确认一下：

```bash
ls -l ~/ZhenResume/backend/.env
```

如果不存在，手工建一个最小的就够（不用整份抄 `.env.example`）：

```bash
cat > ~/ZhenResume/backend/.env <<'EOF'
# LLM：留空 = mock 模式（规则引擎，全链路可用）；填 Key 则切真实模型
DEEPSEEK_API_KEY=

# 把 <VM_IP> 换成 `hostname -I` 的结果
FRONTEND_BASE_URL=http://<VM_IP>:3000
CORS_ORIGINS=http://127.0.0.1:3000,http://localhost:3000,http://<VM_IP>:3000
EOF
```

### 为什么只写这几项

- **`DATABASE_URL` / `UPLOAD_DIR` 不要写。** 不写时程序按自身文件位置推导出**绝对路径**
  （`<backend>/data/zhenresume.db`、`<backend>/data/uploads`），与启动目录无关。
  若写成相对路径（`./data/...`），数据库落在哪就取决于进程的工作目录 ——
  PyCharm 换个运行配置的工作目录就会建出第二个空库，表面症状是"数据不见了"。
  `.env.example` 里这两项已改成注释形式并写明了原因。
- **`LIBREOFFICE_NODE` / `LIBREOFFICE_CLI` 在 Linux 上无效，但无害。**
  它们是 Windows 绝对路径，在 Linux 上 `Path(...).exists()` 为 False →
  `pdf_enabled = False` → 导出 PDF 返回 `503 PDF_UNAVAILABLE`，前端会提示改用 DOCX 或浏览器打印，
  其余功能完全不受影响。所以**直接把 Windows 那份 `.env` 拷过去也能跑**，只需顺手改掉 CORS 两项。

**前端在哪跑，决定了 CORS 要写哪些来源**（`127.0.0.1:3000` 与 `localhost:3000` 算**两个**来源）：

| 前端位置 | 浏览器访问前端 | 后端 `CORS_ORIGINS` 需包含 | 前端 `NEXT_PUBLIC_API_BASE` |
|---|---|---|---|
| 也在虚拟机里 | `http://<VM_IP>:3000` | `http://<VM_IP>:3000` | `http://<VM_IP>:8000` |
| 留在 Windows 宿主机 | `http://127.0.0.1:3000` | `http://127.0.0.1:3000`、`http://localhost:3000` | `http://<VM_IP>:8000` |

前端的这个变量放在 `frontend/.env.local`：

```ini
NEXT_PUBLIC_API_BASE=http://<VM_IP>:8000
```

> 改了 `NEXT_PUBLIC_*` 必须**重新 build**，它对构建期内联，dev 模式重启即可。

---

## 4. 验证清单

```bash
cd ~/ZhenResume/backend
./.venv/bin/python -m scripts.verify      # 期望：174/174 项通过（本机未配 PDF 转换，故比全量 177 少 3 项）
```

> **关于 174 与 177 的差异**（这不是环境不全）：检查项总数会随环境浮动 ——
> PDF 导出可用时执行 5 项（导出成功、`%PDF` 魔数、体积、转换后内容未截断、章节完整），
> 不可用时换成 2 项降级检查（返回 503、错误码正确），差 3 项；
> 另外「二次诊断一致性校验」需要额度放行，被拦时会再少 1 项。
> 脚本在汇总处会自己打印这段说明。**174/174 与 177/177 都代表全部通过。**

在虚拟机上自测：

```bash
./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
curl -s http://127.0.0.1:8000/api/v1/health
```

从宿主机浏览器/命令行验证可达：

```powershell
Invoke-RestMethod http://<VM_IP>:8000/api/v1/health
```

前端（若也在虚拟机里）：

```bash
cd ~/ZhenResume/frontend
pnpm install
pnpm build
pnpm start -H 0.0.0.0 --port 3000
```

---

## 5. 已知限制与排查

| 现象 | 原因 / 处理 |
|---|---|
| 宿主机访问 `http://<VM_IP>:8000` 超时 | ① uvicorn 必须绑 `0.0.0.0`；② 虚拟机网络用**桥接**（NAT 需做端口转发）；③ 客户机防火墙：`sudo ufw allow 8000,3000/tcp` |
| 浏览器报 CORS 错误 | `CORS_ORIGINS` 必须与浏览器地址栏的 **origin 完全一致**（协议+主机+端口），`127.0.0.1` 与 `localhost` 算两个来源 |
| 导出 PDF 返回 503 `PDF_UNAVAILABLE` | **预期行为**。当前 `docx_to_pdf` 调的是 DSH 的 LibreOffice Kit CLI，Linux 上不可用；导出 DOCX 后用浏览器 `Ctrl+P` 存 PDF 即可 |
| `ImportError: cannot import name 'Self' from 'typing'` | venv 的解释器与已装扩展 ABI 不一致（多半是把宿主机 `.venv` 传上来了）。删掉 `.venv` 在虚拟机里重建，见 README 第十一节 |
| `ModuleNotFoundError: No module named 'app'` | 工作目录不对，必须是 `backend/`（`app` 包的父目录） |
| 热更新不生效 | 若代码放在 VMware 共享文件夹 / vboxsf / SMB 挂载点上，`inotify` 不可用。把代码放在虚拟机本地磁盘（如 `~/ZhenResume`） |
| 中文写入数据库/导出乱码 | 确认 locale：`locale`；或在运行配置里设 `PYTHONUTF8=1` |

### 想带一份现成的测试数据？

虚拟机首次启动会自动建表并灌入 16 篇知识库种子文档，所以**不搬数据也能直接用**。
如果要带上宿主机那份测试库（含我测试时上传的简历），**在服务停止时**拷单个文件：

```powershell
scp E:\ZhenResume\backend\data\zhenresume.db <user>@<VM_IP>:~/ZhenResume/backend/data/
```

不要连 `-wal` / `-shm` 一起拷（那是运行中的临时文件）。
