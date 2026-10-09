"""API 依赖：MVP 阶段的轻量用户识别。

aaa.md 技术栈里写的是 Clerk / Supabase Auth，MVP 先不做登录：
用 `X-Device-Id` 请求头做匿名用户隔离，配额与历史记录都挂在这个用户上。
接真实认证时，把 `get_current_user` 换成校验 JWT 即可，业务代码不用改。
"""

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User


def get_current_user(
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    db: Session = Depends(get_db),
) -> User:
    device_id = (x_device_id or "anonymous").strip()[:64] or "anonymous"
    user = db.execute(select(User).where(User.device_id == device_id)).scalar_one_or_none()
    if user is None:
        user = User(device_id=device_id, role="jobseeker", free_quota_used=0)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user
