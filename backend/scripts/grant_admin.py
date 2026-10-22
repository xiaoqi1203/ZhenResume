"""管理员授权 / 撤销 / 查看（一次性运维脚本，不通过 HTTP 接口暴露）。

为什么需要它：`/api/v1/admin/*` 背后是成本看板、全站 Prompt 覆盖、全部用户反馈，
必须是「管理员」才能访问。`users.role` 字段早就存在，但**没有注册接口能把角色改成
admin**（故意如此：否则任何登录用户都能把自己提权）。本脚本直接操作数据库，是唯一
的授权入口；生产环境请用有数据库权限的人执行。

用法（在 backend 目录下；Docker 里执行时前面加
`docker exec zhenresume-api`）：

    # 授权（账号必须已存在；三选一）
    python -m scripts.grant_admin --email owner@example.com
    python -m scripts.grant_admin --phone 13800138000
    python -m scripts.grant_admin --user-id <uuid>

    # 撤销
    python -m scripts.grant_admin --email owner@example.com --revoke

    # 查看当前所有管理员
    python -m scripts.grant_admin --list

授权后**不需要重新登录**也能生效吗？——需要重新登录：角色写在服务端 DB，但前端导航和
`/auth/me` 的展示缓存来自登录时返回的 account；重新登录（或刷新并重新拉 /auth/me）后
前端才会知道自己是管理员。API 鉴权本身是每次请求都查 DB 的，所以旧 token 立即拥有
admin 权限。

退出码：0 = 成功，1 = 参数/账号问题，2 = 有多个账号命中（请改用 --user-id）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.models import User  # noqa: E402
from app.services.security import normalize_email, normalize_phone  # noqa: E402


def _describe(user: User) -> str:
    email = user.email or "-"
    phone = user.phone or "-"
    return f"{user.id}  role={user.role:<9} email={email}  phone={phone}"


def _find_users(db, args: argparse.Namespace) -> list[User]:
    query = select(User)
    if args.user_id:
        query = query.where(User.id == args.user_id.strip())
    elif args.email:
        query = query.where(User.email == normalize_email(args.email))
    elif args.phone:
        query = query.where(User.phone == normalize_phone(args.phone))
    return list(db.execute(query.order_by(User.created_at.asc())).scalars().all())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="授予 / 撤销 / 查看 ZhenResume 管理员（直接改 users.role，不经过 HTTP）"
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--email", help="按邮箱定位账号（大小写不敏感）")
    group.add_argument("--phone", help="按手机号定位账号")
    group.add_argument("--user-id", help="按 user_id 定位账号（多个账号命中时用它）")
    group.add_argument("--list", action="store_true", help="列出当前所有管理员")
    parser.add_argument("--revoke", action="store_true", help="撤销管理员（降回 jobseeker）")
    args = parser.parse_args()

    if not any([args.email, args.phone, args.user_id, args.list]):
        parser.print_help()
        print("\n[ERR] 请给出 --email / --phone / --user-id / --list 之一。", file=sys.stderr)
        return 1

    init_db()
    with session_scope() as db:
        if args.list:
            admins = list(
                db.execute(select(User).where(User.role == "admin").order_by(User.created_at.asc()))
                .scalars()
                .all()
            )
            if not admins:
                print("[INFO] 当前没有管理员。用 --email / --phone / --user-id 授权一个。")
                return 0
            print(f"[OK] 当前管理员 {len(admins)} 个：")
            for user in admins:
                print(f"  {_describe(user)}")
            return 0

        matches = _find_users(db, args)
        if not matches:
            print("[ERR] 没有找到匹配的账号。先让这个账号登录/注册一次，再授权。", file=sys.stderr)
            return 1
        if len(matches) > 1:
            print(f"[ERR] 命中 {len(matches)} 个账号，请改用 --user-id 精确指定：", file=sys.stderr)
            for user in matches:
                print(f"  {_describe(user)}", file=sys.stderr)
            return 2

        user = matches[0]
        target_role = "jobseeker" if args.revoke else "admin"
        if user.role == target_role:
            print(f"[SKIP] 账号已经是 {target_role}，未做改动：{_describe(user)}")
            return 0

        user.role = target_role
        print(f"[OK] 已把账号角色改为 {target_role}：{_describe(user)}")
        if target_role == "admin":
            print("      重新登录后前端导航会出现「管理后台」；API 鉴权立即生效。")
            if not settings.admin_email_set and not settings.admin_phone_set:
                print(
                    "      提示：也可在 backend/.env 配置 ADMIN_EMAILS / ADMIN_PHONES，"
                    "让命中白名单的账号在登录时自动提升（适合本地开发）。"
                )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())