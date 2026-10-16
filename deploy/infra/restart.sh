#!/usr/bin/env bash
# 一键重启整套服务（在**虚拟机**上执行）。
#   bash deploy/infra/restart.sh            # 后端四件套（PG + Redis + API + worker）
#   bash deploy/infra/restart.sh --web      # 连前端容器一起
#   bash deploy/infra/restart.sh --no-build # 不重建镜像（只重启容器，快）
#
# 设计要点：
# 1) 先杀掉宿主机上手工起的 API/worker —— 否则 8000 端口冲突，
#    而且会出现"两个 worker 抢同一个队列、任务被跑两次"。
# 2) `--force-recreate` 才是真"重启"：只 `up -d` 的话配置没变的容器不会被重建。
# 3) 等 db/redis healthy 再起 api/worker（compose 的 depends_on 已声明，这里只做等待与确认）。
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CF="$REPO/deploy/infra/docker-compose.yml"
ENVF="$REPO/deploy/infra/.env"
PROFILES="--profile app"
DO_BUILD="--build"
RECREATE="--force-recreate"

for arg in "$@"; do
  case "$arg" in
    --web)         PROFILES="$PROFILES --profile web" ;;
    --no-build)    DO_BUILD="" ;;
    # 开机自启用它：只拉起停止/缺失的容器，不无谓重建（PG 也不用每次都重启）
    --no-recreate) RECREATE="" ;;
    *) echo "未知参数：$arg"; exit 1 ;;
  esac
done

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

say "1/5 准备密码文件"
[ -f "$ENVF" ] || cp "$REPO/deploy/infra/.env.example" "$ENVF"
grep -q '^POSTGRES_PASSWORD=1203' "$ENVF" || sed -i 's/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=1203/' "$ENVF"
grep -q '^REDIS_PASSWORD=1203'    "$ENVF" || sed -i 's/^REDIS_PASSWORD=.*/REDIS_PASSWORD=1203/'       "$ENVF"
grep -E 'PASSWORD' "$ENVF"

say "2/5 停掉宿主机上手工起的 API / worker（避免抢端口与抢队列）"
pkill -f 'uvicorn app.main:app' 2>/dev/null && echo "  已停 uvicorn" || echo "  没有手工 uvicorn"
pkill -f 'celery -A app.worker' 2>/dev/null && echo "  已停 celery"  || echo "  没有手工 celery"
sleep 1

say "3/5 启动 / 重建容器：$PROFILES $DO_BUILD"
cd "$REPO"
docker compose -f "$CF" $PROFILES up -d $DO_BUILD $RECREATE || exit 1

say "4/5 等待健康（db/redis/api 都要 healthy，最多 180s）"
# ⚠️ 逐个 token 判断，不要用一串通配符按顺序匹配：
# `docker compose ps` 是按**服务名字母序**输出的（api, db, redis, worker），
# 写成一串 `*db:...*redis:...*api:...*` 会因为顺序对不上而永远匹配失败（误报）。
for i in $(seq 1 90); do
  st="$(docker compose -f "$CF" ps --format '{{.Service}}:{{.State}}:{{.Health}}' 2>/dev/null | tr '\n' ' ')"
  ok=true
  for want in "db:running:healthy" "redis:running:healthy" "api:running:healthy"; do
    case "$st" in *"$want"*) ;; *) ok=false ;; esac
  done
  if $ok; then echo "  $st"; break; fi
  if [ "$i" = 90 ]; then
    docker compose -f "$CF" ps
    echo "  ! 180s 内未全部 healthy，看日志：docker compose -f $CF logs --tail=50 api"
  fi
  sleep 2
done

say "5/5 验证"
docker compose -f "$CF" ps
echo "--- API 健康检查 ---"
curl -s --max-time 5 http://127.0.0.1:8000/api/v1/health | head -c 200; echo
echo "--- 流式路由是否注册 ---"
curl -s --max-time 5 http://127.0.0.1:8000/openapi.json | grep -o 'tasks/{task_id}/stream' || echo "  ! 未找到流式路由"
echo "--- worker 日志尾部 ---"
docker compose -f "$CF" logs --tail=8 worker 2>/dev/null | tail -8

cat <<'EOF'

常用命令：
  看日志   docker compose -f deploy/infra/docker-compose.yml logs -f api worker
  只重启   bash deploy/infra/restart.sh --no-build
  全停     docker compose -f deploy/infra/docker-compose.yml --profile app down
  全停+删卷 docker compose -f deploy/infra/docker-compose.yml --profile app down -v   # ⚠️ 会删数据库数据
EOF
