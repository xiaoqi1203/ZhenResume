#!/usr/bin/env bash
# ZhenResume 虚拟机一键落地：中间件（PostgreSQL + Redis）装进 Docker，应用仍跑在宿主机。
#
# 用法（Xshell 里，仓库已同步到 VM）：
#     cd ~/ZhenResume && bash deploy/infra/bootstrap-vm.sh
#
# 幂等：可以反复执行。每个已有配置都会被"删掉同名 key 再追加"，不会重复。
# 默认密码统一为 1203（端口只绑 127.0.0.1，不对局域网开放）。
#
# 脚本只做：装 Docker（如缺）→ 写两份 .env → 起 db/redis → 等健康 → 装依赖
#          → 基础设施自检 → 建表 + 灌知识库 → 端到端契约自检
# 不启动 api/worker —— 那两步在脚本结尾打印给你（PyCharm 或 systemd 都行）。
#
# 可选：OCR_INSTALL=rapidocr|tesseract 时，第 5 步会顺带在**宿主机 venv** 里装本地
#      OCR 引擎（图片简历 / 扫描件用，不需要任何外部 API），并在第 6 步自检。

set -uo pipefail

PW="1203"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND="$REPO/backend"
COMPOSE_FILE="$REPO/deploy/infra/docker-compose.yml"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[x] %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$BACKEND/app/main.py" ] || die "没找到仓库结构，请在 ~/ZhenResume 下执行（当前解析到 REPO=$REPO）"
if [ -f "$BACKEND/app/worker.py" ]; then
  sed -i 's/\r$//' "$BACKEND/app/worker.py" "$BACKEND/app/queue.py" 2>/dev/null || true
fi

# ---------------------------------------------------------------- 0. 行尾
say "0/8 规范化行尾（仓库在 Windows 上编辑过，避免 CRLF 让 shell/python 出错）"
for f in "$COMPOSE_FILE" "$REPO/deploy/infra/.env.example"; do
  [ -f "$f" ] && sed -i 's/\r$//' "$f"
done

# ---------------------------------------------------------------- 1. Docker
say "1/8 检查 Docker"
if ! command -v docker >/dev/null 2>&1; then
  warn "没装 Docker，开始安装（官方脚本；需要 sudo）"
  curl -fsSL https://get.docker.com | sudo sh || die "Docker 安装失败，请手工装好后重跑"
  sudo systemctl enable --now docker
fi
if docker info >/dev/null 2>&1; then
  DOCKER="docker"
else
  warn "当前用户不在 docker 组（或需要 sudo），本次用 sudo 执行 docker"
  DOCKER="sudo docker"
fi
if ! $DOCKER compose version >/dev/null 2>&1; then
  warn "缺 compose 插件，尝试安装 docker-compose-plugin"
  sudo apt-get update -qq && sudo apt-get install -y docker-compose-plugin || die "compose 插件安装失败"
fi
$DOCKER compose version | head -1

# ---------------------------------------------------------------- 2. 中间件 .env
say "2/8 写 deploy/infra/.env（compose 用的密码与端口）"
cat > "$REPO/deploy/infra/.env" <<EOF
POSTGRES_USER=zhenresume
POSTGRES_PASSWORD=$PW
POSTGRES_DB=zhenresume
POSTGRES_PORT=5432
REDIS_PASSWORD=$PW
REDIS_PORT=6379
API_PORT=8000
EMBEDDING_FALLBACK_BASE_URL=http://host.docker.internal:11434/v1
EOF

# ---------------------------------------------------------------- 3. 应用 .env
say "3/8 写 backend/.env 里的存储与执行配置（保留你已有的 LLM Key）"
[ -f "$BACKEND/.env" ] || { warn "backend/.env 不存在，先按 .env.example 建一份"; cp "$BACKEND/.env.example" "$BACKEND/.env"; }
cp "$BACKEND/.env" "$BACKEND/.env.bak.$(date +%Y%m%d-%H%M%S)"
sed -i '/^DATABASE_URL=/d; /^WORKFLOW_EXECUTOR=/d; /^CELERY_BROKER_URL=/d; /^CELERY_RESULT_BACKEND=/d; /^WORKFLOW_ENGINE=/d; /^WORKFLOW_PARALLEL=/d; /^HR_SCREENING=/d; /^HR_BATCH_CONCURRENCY=/d; /^EMBEDDING_FALLBACK_BASE_URL=/d' "$BACKEND/.env"
cat >> "$BACKEND/.env" <<EOF

# ---- 以下由 deploy/infra/bootstrap-vm.sh 写入（密码统一 $PW）----
# 存储：PostgreSQL（容器，端口只绑 127.0.0.1）
DATABASE_URL=postgresql+psycopg://zhenresume:$PW@127.0.0.1:5432/zhenresume
# 执行：Celery + Redis（broker 用 /1，结果后端用 /2，别混库）
WORKFLOW_EXECUTOR=celery
CELERY_BROKER_URL=redis://:$PW@127.0.0.1:6379/1
CELERY_RESULT_BACKEND=redis://:$PW@127.0.0.1:6379/2
# 编排：LangGraph + 并行 + HR 排序表
WORKFLOW_ENGINE=langgraph
WORKFLOW_PARALLEL=true
HR_SCREENING=true
HR_BATCH_CONCURRENCY=50
# embedding 回退层（应用在宿主机，直接连本机 Ollama）
EMBEDDING_FALLBACK_BASE_URL=http://127.0.0.1:11434/v1
EOF
echo "    备份在 $BACKEND/.env.bak.*（出问题可回滚）"

# ---------------------------------------------------------------- 4. 起中间件
say "4/8 启动 PostgreSQL 与 Redis 容器"
$DOCKER compose -f "$COMPOSE_FILE" up -d db redis || die "容器启动失败，看 $DOCKER compose -f $COMPOSE_FILE logs"

say "     等待两个容器 healthy（最多 120s）"
for i in $(seq 1 60); do
  states="$($DOCKER compose -f "$COMPOSE_FILE" ps --format '{{.Service}}:{{.Health}}' 2>/dev/null | tr '\n' ' ')"
  case "$states" in
    *"db:healthy"*"redis:healthy"*) echo "     $states"; break ;;
  esac
  [ "$i" = "60" ] && { $DOCKER compose -f "$COMPOSE_FILE" ps; warn "健康检查未通过，先看 logs：$DOCKER compose -f $COMPOSE_FILE logs --tail=50"; }
  sleep 2
done

# ---------------------------------------------------------------- 5. 依赖
say "5/8 安装 / 校验 Python 依赖（celery / redis / psycopg 是这次新增的）"
PY="$BACKEND/.venv/bin/python"
[ -x "$PY" ] || die "找不到 $PY —— 先在 VM 里建好 venv（python3 -m venv .venv）"
"$PY" -m pip install -q -r "$BACKEND/requirements.txt" || die "依赖安装失败"

# 可选：本地 OCR（图片简历 / 扫描版 PDF）。**不需要任何外部 API/Key**，纯本机推理。
# 默认不装：不装时这两类输入会明确降级（空文本 + extract_note，前端拦下来提示换格式）。
#   OCR_INSTALL=rapidocr  bash deploy/infra/bootstrap-vm.sh   # 纯 pip，中文效果好（推荐）
#   OCR_INSTALL=tesseract bash deploy/infra/bootstrap-vm.sh   # 更轻，走 apt 装二进制
case "${OCR_INSTALL:-none}" in
  rapidocr)
    say "     安装本地 OCR：rapidocr（onnxruntime 本地推理）"
    "$PY" -m pip install -q rapidocr-onnxruntime==1.4.4 || die "rapidocr 安装失败"
    ;;
  tesseract)
    say "     安装本地 OCR：tesseract（apt 二进制 + pytesseract 封装）"
    if ! command -v tesseract >/dev/null 2>&1; then
      sudo apt-get update -qq && sudo apt-get install -y tesseract-ocr tesseract-ocr-chi-sim \
        || warn "tesseract 安装失败，可稍后手工装（apt install tesseract-ocr tesseract-ocr-chi-sim）"
    fi
    "$PY" -m pip install -q pytesseract==0.3.13 || warn "pytesseract 安装失败"
    ;;
  none|"")
    echo "     （未装 OCR 引擎：图片/扫描件会明确降级；需要时用 OCR_INSTALL=rapidocr 重跑）"
    ;;
  *)
    warn "OCR_INSTALL=$OCR_INSTALL 不认识（可选 rapidocr / tesseract / none），已跳过"
    ;;
esac

# ---------------------------------------------------------------- 6. 自检
say "6/8 基础设施自检（真实连接 PG / Redis / Celery）"
( cd "$BACKEND" && "$PY" -m scripts.check_infra ) || die "自检未通过 —— 按上面提示修好再重跑本脚本"

if [ "${OCR_INSTALL:-none}" != "none" ]; then
  say "     本地 OCR 自检（真跑一遍合成图片与扫描件）"
  ( cd "$BACKEND" && "$PY" -m scripts.check_ocr ) || die "OCR 自检未通过 —— 图片简历会解析失败"
fi

# ---------------------------------------------------------------- 7. 建表 + 知识库
say "7/8 建表（PG 上是空库，create_all 会建全部表 + 唯一索引）并灌知识库种子"
( cd "$BACKEND" && "$PY" -c "from app.db import init_db; init_db(); print('    建表完成')" ) || die "建表失败"
( cd "$BACKEND" && "$PY" -m scripts.seed_knowledge --force ) || die "知识库灌入失败"

# ---------------------------------------------------------------- 8. 契约自检
say "8/8 端到端契约自检（executor=celery 时会自动轮询等 worker）"
warn "注意：这一步需要 Celery worker 在跑，否则任务只会排队。"
warn "如果还没起 worker，先开另一个 Xshell 窗口执行："
echo "    cd $BACKEND && ./.venv/bin/celery -A app.worker worker -l INFO -Q default --concurrency=4"
echo
read -r -p "worker 已经起了吗？起了就回车继续，没起就 Ctrl+C 起完再重跑 8/8：" _ || true
( cd "$BACKEND" && "$PY" -m scripts.verify ) || die "契约自检未通过 —— 期望 174/174"

cat <<EOF

==========================================================================
 全部通过。接下来：
==========================================================================

1) 起 API（宿主机；也可以在 PyCharm 里跑，二选一）
   cd $BACKEND && ./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

2) 起 Celery worker（另一个窗口，或做成 systemd 常驻，见 docs/vm-infra-pg-redis-celery.md §4.2）
   cd $BACKEND && ./.venv/bin/celery -A app.worker worker -l INFO -Q default --concurrency=4

3) 健康检查
   curl -s http://127.0.0.1:8000/api/v1/health

4) 常用排查
   队列积压：docker exec zhenresume-redis redis-cli -a $PW -n 1 llen default
   容器状态：$DOCKER compose -f $COMPOSE_FILE ps
   备份数据库：docker exec zhenresume-db pg_dump -U zhenresume zhenresume > backup-\$(date +%F).sql

回退（任何一步想退回原状）：把 backend/.env 里的 WORKFLOW_EXECUTOR 改回 inline、
注释掉 DATABASE_URL，再重启服务即可 —— 业务语义完全一致。
EOF
