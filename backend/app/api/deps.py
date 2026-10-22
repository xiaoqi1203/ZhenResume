"""API 依赖：账号鉴权 + MVP 期的匿名设备识别。

两套通道并存，切换开关 `AUTH_REQUIRED` 即可整体收紧：

1. **Bearer token（账号）**：`Authorization: Bearer <jwt>` → 校验签名 / 过期 / 会话版本 →
   返回对应 `User`。这是正路。
2. **X-Device-Id（匿名）**：没有 Authorization 时沿用 MVP 的设备识别（配额与历史挂在
   这个匿名用户上），保证老数据与未登录体验不被打断。
   `AUTH_REQUIRED=true` 时这一通道直接拒绝（401 AUTH_REQUIRED）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..errors import Forbidden, Unauthorized
from ..models import RevokedToken, User
from ..services.security import decode_access_token


def get_current_user(
    authorization: str | None = Header(default=None),
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    db: Session = Depends(get_db),
) -> User:
    """当前用户：优先 Bearer token，其次匿名设备号。"""
    token = _bearer_token(authorization)
    if token:
        return _user_from_token(db, token)

    if settings.auth_required:
        raise Unauthorized("请先登录", code="AUTH_REQUIRED")

    # 匿名通道：没有账号时按设备号创建一个 jobseeker（行为与账号体系上线前一致）
    device_id = (x_device_id or "anonymous").strip()[:64] or "anonymous"
    return _get_or_create_device_user(db, device_id)


def _get_or_create_device_user(db: Session, device_id: str) -> User:
    """按 device_id 取用户，没有就创建。

    ⚠️ 两处防御（都是线上真出过的问题）：
    1. **多行容错**：老数据里同一 device_id 可能有多行（并发首请求的竞态写出了重复用户），
       以前用 `scalar_one_or_none()` 会直接抛 MultipleResultsFound → 500。
       现在按创建时间取最早的一条，历史数据自动“愈合”，不需要人工清库。
    2. **并发创建**：两个请求同时发现“没有用户”会各建一条。这里用 SAVEPOINT 兜住
       唯一约束冲突，冲突后重新查询即可拿到对方刚建的那条。
    """
    user = db.execute(
        select(User).where(User.device_id == device_id).order_by(User.created_at.asc()).limit(1)
    ).scalar_one_or_none()
    if user is not None:
        return user

    try:
        with db.begin_nested():
            user = User(device_id=device_id, role="jobseeker", free_quota_used=0)
            db.add(user)
        db.commit()
        db.refresh(user)
        return user
    except IntegrityError:
        # 并发下另一个请求先建好了：回滚后重查
        db.rollback()
        user = db.execute(
            select(User).where(User.device_id == device_id).order_by(User.created_at.asc()).limit(1)
        ).scalar_one_or_none()
        if user is not None:
            return user
        raise


def get_optional_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> tuple[User, dict] | None:
    """可选登录：带了合法 token 就返回 (user, payload)，否则返回 None（不创建匿名用户）。"""
    token = _bearer_token(authorization)
    if not token:
        return None
    payload = decode_access_token(token)
    return _user_from_token(db, token, payload), payload


def get_required_account(user: User = Depends(get_current_user)) -> User:
    """必须**真实账号**（不能是匿名设备用户）：账号中心等接口用它。"""
    if not (user.email or user.phone) or not user.password_hash:
        raise Unauthorized("请先登录", code="AUTH_REQUIRED")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise Forbidden("需要管理员权限", code="ADMIN_REQUIRED")
    return user


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _user_from_token(db: Session, token: str, payload: dict | None = None) -> User:
    payload = payload or decode_access_token(token)

    jti = payload.get("jti") or ""
    if jti and db.get(RevokedToken, jti) is not None:
        raise Unauthorized("登录已失效，请重新登录", code="AUTH_REVOKED")

    user_id = payload.get("sub")
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise Unauthorized("账号不存在，请重新登录", code="AUTH_INVALID")

    # 会话版本：改密 / 强制下线后递增，旧 token 立刻作废
    if int(payload.get("ver") or 0) != int(user.token_version or 0):
        raise Unauthorized("登录状态已更新，请重新登录", code="AUTH_REVOKED")

    return user


def revoke_token(db: Session, payload: dict) -> None:
    """把当前 token 的 jti 记进黑名单（登出用）。"""
    jti = payload.get("jti")
    if not jti:
        return
    exp = payload.get("exp")
    expires_at = (
        datetime.fromtimestamp(int(exp), tz=timezone.utc) if exp else datetime.now(tz=timezone.utc)
    )
    if db.get(RevokedToken, jti) is None:
        db.add(RevokedToken(jti=jti, user_id=payload.get("sub"), expires_at=expires_at))
        db.commit()
