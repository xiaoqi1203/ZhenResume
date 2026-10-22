"""登录后认领匿名设备数据（X-Device-Id → 账号）。

背景：未登录时 `api/deps.py` 会按 `X-Device-Id` 建一个匿名 `User`（email/phone 为空），
用户的简历、JD、诊断任务等都挂在这个匿名用户上。等用户注册 / 登录后，
如果什么都不做，浏览器被识别成「另一个账号」，**匿名期间建的数据会凭空消失**。

因此登录成功的那一刻调用 `claim_device_data`：把匿名用户的数据整体搬到当前账号，
然后把两边 `device_id` 绑定关系理顺（匿名侧解除、账号侧为空则补上）。

边界：
- 只迁移「用户内容」表；`payments` / `subscriptions` 是资金凭证，**绝不迁移**
  （匿名用户本来也不会产生订单，但宁可显式排除也不做隐式假设）；
- 迁移按 `user_id` 批量 UPDATE，统计条数写日志，便于排查"数据没过来"；
- 幂等：没带设备头 / 没有匹配的匿名用户时安全返回（全 0 统计），不报错；
- 只认**真正匿名**的 user（email 与 phone 都为 NULL），不碰另一个真实账号的数据。
"""

from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import AnalysisTask, Feedback, HrBatch, JobDescription, Resume, User

logger = logging.getLogger(__name__)

# 需要认领的表（顺序无关，但保持稳定便于对比日志）。
# 注意：**不要**把 Payment / Subscription 加进来 —— 那是资金记录，不属于"用户内容"。
CLAIM_MODELS: tuple[type, ...] = (Resume, JobDescription, AnalysisTask, HrBatch, Feedback)


def claim_device_data(db: Session, device_id: str | None, user: User) -> dict[str, int]:
    """把 `device_id` 对应匿名用户的数据迁移到 `user`，返回 {表名: 迁移行数}。"""
    stats: dict[str, int] = {model.__tablename__: 0 for model in CLAIM_MODELS}

    device_id = (device_id or "").strip()[:64]
    if not device_id:
        return stats

    anon = db.execute(
        select(User)
        .where(
            User.device_id == device_id,
            User.id != user.id,
            User.email.is_(None),
            User.phone.is_(None),
        )
        .order_by(User.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()

    # 没有匿名用户 = 已经认领过 / 这台设备没产生过数据：直接安全返回（幂等）
    if anon is None:
        return stats

    for model in CLAIM_MODELS:
        if not hasattr(model, "user_id"):
            # 防御：将来表结构变了（某表没有 user_id 列）也不该让登录整体失败
            continue
        result = db.execute(
            update(model)
            .where(model.user_id == anon.id)
            .values(user_id=user.id)
            .execution_options(synchronize_session=False)
        )
        stats[model.__tablename__] = max(0, int(result.rowcount or 0))

    # 绑定关系：匿名用户解除设备号（否则下次匿名请求还会命中它），
    # 当前账号没绑过设备号就补上（后续同浏览器的匿名请求能延续到该账号）。
    anon.device_id = None
    if not (user.device_id or "").strip():
        user.device_id = device_id

    db.commit()

    logger.info(
        "已认领匿名设备 %s 的数据到账号 %s：%s",
        device_id,
        user.id,
        ", ".join(f"{name}={count}" for name, count in stats.items()),
    )
    return stats


__all__ = ["CLAIM_MODELS", "claim_device_data"]