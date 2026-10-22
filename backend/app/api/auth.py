"""账号接口（/api/v1/auth）：注册、邮箱登录、手机验证码登录、会话、改密、登出。

约定与其余接口一致：
- 请求体用 pydantic 模型，响应用 dict（字段与前端 lib/auth.ts 逐字对齐）；
- 错误统一走 `{"detail": {"code", "message"}}`，401 带 `WWW-Authenticate: Bearer`；
- 短信验证码**只存摘要**，开发模式下接口会回传 `dev_code` 便于联调（SMS_DEV_MODE=false 时不回传）。
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..errors import AppError, Conflict, Unauthorized, ValidationError as BadRequest
from ..models import Payment, User
from ..schemas import ChangePassword, LoginEmail, LoginSms, Register, SendSmsCode, UpdateProfile
from ..services.claim import claim_device_data
from ..services.security import (
    SMS_CODE_TTL_SECONDS,
    consume_sms_code,
    create_access_token,
    decode_access_token,
    hash_password,
    is_valid_email,
    is_valid_password,
    is_valid_phone,
    issue_sms_code,
    normalize_email,
    normalize_phone,
    verify_password,
)
from .deps import get_required_account, revoke_token

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 序列化
# --------------------------------------------------------------------------- #
def serialize_account(user: User) -> dict:
    """账号信息（**不含密码哈希**）。"""
    return {
        "user_id": user.id,
        "email": user.email,
        "phone": user.phone,
        "role": user.role,
        "auto_created": bool(user.auto_created),
        "free_quota_used": int(user.free_quota_used or 0),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _issue_session(user: User) -> dict:
    token, jti = create_access_token(user.id, int(user.token_version or 0))
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.jwt_expire_minutes * 60,
        "jti": jti,
        "account": serialize_account(user),
        # 该账号是否是本次请求「刚刚自动创建」的（前端据此弹出「请完善账号」提示）
        "account_created": False,
    }


def _placeholder_credentials() -> tuple[str, str]:
    """自动建号时的占位邮箱与随机密码。

    邮箱用 `.local` 顶级域：一眼看出不是真实邮箱，也不会误发到真实域名；
    密码 16 位随机串，同样一眼能看出是系统生成的。
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    password = "".join(secrets.choice(alphabet) for _ in range(16))
    email = f"user_{secrets.token_hex(6)}.auto@zhenresume.local"
    return email, password


def _find_by_phone(db: Session, phone: str) -> User | None:
    return db.execute(select(User).where(User.phone == phone)).scalar_one_or_none()


def _find_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


def _apply_admin_allowlist(db: Session, user: User) -> None:
    """把命中 `ADMIN_EMAILS` / `ADMIN_PHONES` 白名单的账号提升为管理员。

    只在登录 / 注册这两个「身份刚被证明」的时刻执行，不在每个请求里改角色：
    - 手机号在注册/验证码登录时已经过短信验证，是最可靠的通道；
    - 邮箱目前**没有做发送验证**，`ADMIN_EMAILS` 只适合「部署者自己控制注册入口 /
      本机部署」的场景。公网部署应优先用手机号白名单，或账号建好后运行
      `python -m scripts.grant_admin --email <邮箱>` 显式授权（不会自动回退角色）。
    """
    if user.role == "admin":
        return
    email = normalize_email(user.email or "")
    phone = normalize_phone(user.phone or "")
    hit_email = bool(email) and email in settings.admin_email_set
    hit_phone = bool(phone) and phone in settings.admin_phone_set
    if not (hit_email or hit_phone):
        return
    user.role = "admin"
    db.commit()
    db.refresh(user)
    logger.info("账号 %s 命中管理员白名单，已提升为 admin", user.id)


def _claim_after_login(db: Session, device_id: str | None, user: User) -> None:
    """登录/注册成功后的匿名数据认领（「登录前的简历不见了」的修复点）。

    放在 `_apply_admin_allowlist` 之后、签 token 之前：
    - 角色提升先落库，后续日志/响应里的 account 才是最终状态；
    - 认领失败**不应该**让用户看到「登录失败」——账号已经建好/验证通过，
      吞掉异常并记日志，用户仍能正常进入，数据问题留给运维排查。
    """
    if not (device_id or "").strip():
        return
    try:
        claim_device_data(db, device_id, user)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("认领匿名设备数据失败（不影响本次登录）：device_id=%s", device_id)


# --------------------------------------------------------------------------- #
# 接口
# --------------------------------------------------------------------------- #
@router.post("/sms-code", status_code=201)
def send_sms_code(
    payload: SendSmsCode,
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
) -> dict:
    """发送短信验证码。

    purpose：
    - `login`    手机验证码登录（手机号不存在时会自动建号，所以这里不拦）
    - `register` 创建账号（手机号已注册时直接拦下，省得用户填完五栏才报错）
    - `reset`    预留：绑定邮箱后修改密码时使用
    """
    phone = normalize_phone(payload.phone)
    if not is_valid_phone(phone):
        raise BadRequest("请输入正确的 11 位手机号", code="PHONE_INVALID")

    if payload.purpose == "register" and _find_by_phone(db, phone) is not None:
        raise Conflict("该手机号已注册，请直接登录", code="PHONE_TAKEN")

    code, expires_in = issue_sms_code(db, phone, payload.purpose)
    if not settings.sms_dev_mode:
        # 生产环境在这里接入真实短信服务（阿里云 / 腾讯云），失败时抛 502。
        _send_sms_via_provider(phone, code)

    # 开发模式：把验证码回传给前端方便联调；生产模式**绝不**回传。
    return {
        "phone": phone,
        "purpose": payload.purpose,
        "expires_in": expires_in,
        "dev_code": code if settings.sms_dev_mode else None,
    }


def _send_sms_via_provider(phone: str, code: str) -> None:  # pragma: no cover - 未接入供应商
    """真实短信发送的挂载点。

    当前项目没有短信供应商配置，所以这里**显式报错**而不是静默丢弃：
    生产上忘记关闭 SMS_DEV_MODE 时，调用方会立刻看到 502，而不是「验证码永远收不到」。
    接入阿里云 / 腾讯云短信后，把下面的 raise 换成真实调用即可。
    """
    raise AppError(
        "短信服务未配置，无法发送验证码（SMS_DEV_MODE 已关闭）",
        code="SMS_PROVIDER_UNAVAILABLE",
        status_code=503,
    )


@router.post("/register", status_code=201)
def register(
    payload: Register,
    response: Response,
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
) -> dict:
    """创建账号：邮箱、手机号、手机验证码、密码（确认密码在前端校验）。"""
    email = normalize_email(payload.email)
    phone = normalize_phone(payload.phone)

    if not is_valid_email(email):
        raise BadRequest("请输入正确的邮箱地址", code="EMAIL_INVALID")
    if not is_valid_phone(phone):
        raise BadRequest("请输入正确的 11 位手机号", code="PHONE_INVALID")
    if not is_valid_password(payload.password):
        raise BadRequest("密码至少 8 位，且同时包含字母和数字", code="PASSWORD_WEAK")
    if _find_by_phone(db, phone) is not None:
        raise Conflict("该手机号已注册，请直接登录", code="PHONE_TAKEN")
    if _find_by_email(db, email) is not None:
        raise Conflict("该邮箱已被使用，请换一个或直接登录", code="EMAIL_TAKEN")

    consume_sms_code(db, phone, "register", payload.code)

    user = User(
        email=email,
        phone=phone,
        password_hash=hash_password(payload.password),
        role="jobseeker",
        free_quota_used=0,
        auto_created=False,
        token_version=0,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:  # 并发注册撞唯一索引
        db.rollback()
        raise Conflict("邮箱或手机号已被占用，请直接登录", code="ACCOUNT_TAKEN") from exc
    db.refresh(user)

    _apply_admin_allowlist(db, user)
    # 把匿名期间（同一个 X-Device-Id）建的数据搬到刚注册的账号上
    _claim_after_login(db, x_device_id, user)
    response.headers["WWW-Authenticate"] = "Bearer"
    return _issue_session(user)


@router.post("/login")
def login(
    payload: LoginEmail,
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
) -> dict:
    """邮箱 + 密码登录。"""
    email = normalize_email(payload.email)
    user = _find_by_email(db, email)
    # 统一提示，不暴露「邮箱是否存在」
    if user is None or not verify_password(payload.password, user.password_hash):
        raise Unauthorized("邮箱或密码不正确，请重试", code="AUTH_FAILED")
    _apply_admin_allowlist(db, user)
    _claim_after_login(db, x_device_id, user)
    return _issue_session(user)


@router.post("/login-sms")
def login_sms(
    payload: LoginSms,
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
) -> dict:
    """手机验证码登录：手机号不存在时**自动建号**（邮箱/密码为占位乱码）。"""
    phone = normalize_phone(payload.phone)
    if not is_valid_phone(phone):
        raise BadRequest("请输入正确的 11 位手机号", code="PHONE_INVALID")

    consume_sms_code(db, phone, "login", payload.code)

    user = _find_by_phone(db, phone)
    created = False
    if user is None:
        email, password = _placeholder_credentials()
        user = User(
            email=email,
            phone=phone,
            password_hash=hash_password(password),
            role="jobseeker",
            free_quota_used=0,
            auto_created=True,
            token_version=0,
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            # 并发下另一个请求刚建好同一个手机号 → 直接复用
            db.rollback()
            user = _find_by_phone(db, phone)
            if user is None:
                raise
        else:
            created = True
            db.refresh(user)

    _apply_admin_allowlist(db, user)
    _claim_after_login(db, x_device_id, user)
    data = _issue_session(user)
    data["account_created"] = created
    return data


@router.get("/me")
def me(
    user: User = Depends(get_required_account),
    db: Session = Depends(get_db),
) -> dict:
    """当前账号 + 账号中心摘要（额度、是否付过费）。"""
    from sqlalchemy import func

    paid = db.execute(
        select(func.count()).select_from(Payment).where(Payment.user_id == user.id, Payment.status == "paid")
    ).scalar_one()
    return {
        "account": serialize_account(user),
        "quota": {
            "free": settings.free_quota,
            "used": int(user.free_quota_used or 0),
            "enforced": settings.quota_enforced,
        },
        "has_paid": bool(_paid_count(db, user)),
        "orders": int(paid),
    }


def _paid_count(db: Session, user: User) -> int:
    from sqlalchemy import func

    return int(
        db.execute(
            select(func.count()).select_from(Payment).where(Payment.user_id == user.id, Payment.status == "paid")
        ).scalar_one()
    )


@router.post("/change-password")
def change_password(
    payload: ChangePassword,
    user: User = Depends(get_required_account),
    db: Session = Depends(get_db),
) -> dict:
    """修改密码：校验旧密码 → 写新密码 → `token_version + 1` 让其它会话失效。

    自动建号的账号没有「用户知道的旧密码」，允许直接设置：
    此时要求带上当前 Bearer token（= 已经登录），跳过旧密码校验。
    """

    data = payload
    if not is_valid_password(data.new_password):
        raise BadRequest("密码至少 8 位，且同时包含字母和数字", code="PASSWORD_WEAK")
    if data.new_password != data.confirm_password:
        raise BadRequest("两次输入的密码不一致", code="PASSWORD_MISMATCH")

    if not user.auto_created:
        if not verify_password(data.old_password or "", user.password_hash):
            raise Unauthorized("旧密码不正确", code="OLD_PASSWORD_INVALID")

    new_hash = hash_password(data.new_password)
    if verify_password(data.new_password, user.password_hash):
        raise BadRequest("新密码不能与旧密码相同", code="PASSWORD_UNCHANGED")

    user.password_hash = new_hash
    user.auto_created = False  # 用户设置了真实密码，不再是「占位账号」
    user.token_version = int(user.token_version or 0) + 1
    db.commit()
    db.refresh(user)

    # 旧 token 全部失效，直接返回一份新的，前端无感续上
    return _issue_session(user)


@router.patch("/me")
def update_profile(
    payload: UpdateProfile,
    user: User = Depends(get_required_account),
    db: Session = Depends(get_db),
) -> dict:
    """更新账号资料：目前只允许改邮箱（改手机号要走验证码流程）。"""
    if payload.email is not None:
        email = normalize_email(payload.email)
        if not is_valid_email(email):
            raise BadRequest("请输入正确的邮箱地址", code="EMAIL_INVALID")
        other = _find_by_email(db, email)
        if other is not None and other.id != user.id:
            raise Conflict("该邮箱已被使用", code="EMAIL_TAKEN")
        user.email = email
    db.commit()
    db.refresh(user)
    return {"account": serialize_account(user)}


@router.post("/logout", status_code=204)
def logout(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Response:
    """登出：把当前 token 的 jti 写进黑名单（其余设备不受影响）。"""
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            try:
                payload = decode_access_token(value.strip())
            except Unauthorized:
                payload = None
            if payload:
                revoke_token(db, payload)
    return Response(status_code=204)


@router.get("/_debug/accounts")
def debug_accounts(db: Session = Depends(get_db)) -> dict:  # pragma: no cover - 仅开发模式
    """开发模式下的账号清单（不含密码），方便本地联调；生产模式直接 404。"""

    if not settings.sms_dev_mode:
        raise AppError("接口不存在", code="NOT_FOUND", status_code=404)
    rows = db.execute(select(User).where(or_(User.email.is_not(None), User.phone.is_not(None)))).scalars()
    return {
        "now": datetime.now(tz=timezone.utc).isoformat(),
        "accounts": [serialize_account(u) for u in rows],
        "sms_code_ttl": SMS_CODE_TTL_SECONDS,
    }
