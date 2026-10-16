#!/usr/bin/env bash
# 安装 / 更新 ZhenResume 后端全栈的开机自启（在**虚拟机**上执行）。
#
#   bash deploy/infra/install-autostart.sh
#
# 做四件事：
#   1) 确认 Docker 守护进程开机自启（否则容器自启无从谈起）
#   2) 把 zhenresume.service 模板里的 __USER__ / __REPO__ 替换成实际值并安装到 systemd
#   3) daemon-reload + enable --now（立刻生效，且以后每次开机自动拉起）
#   4) 打印状态与常用命令
#
# 幂等：可重复执行；每次改了 compose / .env / .service 都重跑一次即可。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TPL="$REPO/deploy/infra/zhenresume.service"
UNIT="/etc/systemd/system/zhenresume.service"
CUR_USER="$(id -un)"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

[ -f "$TPL" ] || { echo "找不到模板：$TPL"; exit 1; }
[ -f "$REPO/deploy/infra/docker-compose.yml" ] || { echo "找不到 compose 文件"; exit 1; }

say "1/4 让 Docker 守护进程开机自启"
sudo systemctl enable --now docker
systemctl is-enabled docker

say "2/4 安装 systemd 单元（用户=$CUR_USER，仓库=$REPO）"
sed "s|__USER__|$CUR_USER|g; s|__REPO__|$REPO|g" "$TPL" | sudo tee "$UNIT" >/dev/null
echo "  已写入 $UNIT"

say "3/4 启用并立即启动"
sudo systemctl daemon-reload
sudo systemctl enable --now zhenresume.service

say "4/4 状态"
sudo systemctl status zhenresume.service --no-pager --lines=12 || true

cat <<'EOF'

────────────────────────────────────────────────────────────
常用命令（都用 sudo）：

  看状态       sudo systemctl status zhenresume
  手动重启     sudo systemctl restart zhenresume          # = 杀掉手工进程 + 重建容器
  停（留容器）  sudo systemctl stop zhenresume
  取消自启     sudo systemctl disable zhenresume
  看单元日志   sudo journalctl -u zhenresume -n 50 --no-pager
  看容器日志   docker compose -f ~/ZhenResume/deploy/infra/docker-compose.yml logs -f api worker

⚠️ 日常维护约定（很容易踩）：
  · 不要用 `docker compose down` 来"停服务" —— 容器被删后，本单元在下次开机前不会自己回来。
    停服务请用 `sudo systemctl stop zhenresume`（保留容器）。
  · 改了 `.env` / compose / 代码后，必须重建才生效：
        cd ~/ZhenResume && bash deploy/infra/restart.sh          # 带 --build
        sudo systemctl restart zhenresume                        # 或者（不带 build，用现有镜像）
  · 验证自启：sudo reboot，起来后 `curl -s http://127.0.0.1:8000/api/v1/health`
────────────────────────────────────────────────────────────
EOF
