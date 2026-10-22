"""账号与鉴权 —— 密码哈希、JWT 签发/校验、短信验证码。

设计取舍
--------------------------------------------------------------------------
1. **密码哈希只依赖标准库**：`hashlib.scrypt`（Python 3.6+ 内置，内存硬，抗 GPU 爆破）。
   不引入 bcrypt / passlib，少一个部署依赖；哈希串格式
   `scrypt$n$r$p$<salt_b64>$<hash_b64>`，自带参数，将来调参也能校验老密码。
2. **JWT 用 PyJWT**：HS256 对称签名，`sub` = 用户 id，`jti` = 会话 id。
   `jti` 的作用是「改密 / 退出后让旧 token 立刻失效」——服务端记一个
   `token_version`（用户级）+ 黑名单（会话级）即可，不必上 Redis。
3. **短信验证码存数据库**（`sms_codes` 表）：重启不丢、多进程一致、天然支持限流查询。
   码本身**只存 sha256 摘要**，库里泄露也拿不到明文验证码。
4. 生产环境必须配置 `AUTH_SECRET`；未配置时启动会打警告并按「开发默认值」运行。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import AppError, TooManyRequests, Unauthorized

logger = logging.getLogger(__name__)

JWT_ALG = "HS256"

# ---- 校验规则（与前端 lib/auth.ts 的 PATTERNS 保持一致）----
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
# 至少 8 位，且同时包含字母和数字
PASSWORD_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,}$")

# 短信验证码策略
SMS_CODE_TTL_SECONDS = 300
SMS_RESEND_SECONDS = 60
SMS_DAILY_LIMIT = 10


def normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[:11]


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def is_valid_phone(phone: str) -> bool:
    return bool(PHONE_RE.match(normalize_phone(phone)))


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(normalize_email(email)))


def is_valid_password(password: str) -> bool:
    return bool(PASSWORD_RE.match(password or ""))


# --------------------------------------------------------------------------- #
# 密码哈希（scrypt）
# --------------------------------------------------------------------------- #
_SCRYPT_N = 16384  # CPU/内存成本约 16MB，单次 ~40ms，登录场景可接受
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str | None) -> bool:
    """常量时间比较；格式不认识时一律视为不匹配（不抛异常）。"""
    if not stored or not stored.startswith("scrypt$"):
        return False
    try:
        _, n, r, p, salt_b64, hash_b64 = stored.split("$", 5)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.scrypt(
            (password or "").encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #
def _secret() -> str:
    secret = (settings.auth_secret or "").strip()
    if not secret:
        logger.warning(
            "AUTH_SECRET 未配置：正在使用开发默认密钥，请勿在生产环境运行。"
            "（在 backend/.env 里设置 AUTH_SECRET=<随机长字符串>）"
        )
        return "dev-insecure-secret-change-me"
    return secret


def create_access_token(user_id: str, token_version: int = 0) -> tuple[str, str]:
    """返回 (token, jti)。jti 用于服务端登出黑名单。"""
    now = datetime.now(tz=timezone.utc)
    jti = secrets.token_urlsafe(16)
    payload = {
        "sub": user_id,
        "jti": jti,
        "ver": token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
    }
    token = jwt.encode(payload, _secret(), algorithm=JWT_ALG)
    return token, jti


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, _secret(), algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("登录已过期，请重新登录", code="AUTH_EXPIRED") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("登录已失效，请重新登录", code="AUTH_INVALID") from exc


# --------------------------------------------------------------------------- #
# 短信验证码
# --------------------------------------------------------------------------- #
def _code_digest(phone: str, purpose: str, code: str) -> str:
    """绑定手机号 + 用途 + 码一起摘要：登录码不能拿去注册，也不能跨号复用。"""
    raw = f"{phone}:{purpose}:{code}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def issue_sms_code(db: Session, phone: str, purpose: str, code: str | None = None) -> tuple[str, int]:
    """生成并落库一条验证码；返回 (code, expires_in_seconds)。

    - 同号同用途 60 秒内重复发送 → 429；
    - 同一手机号 24 小时内最多 SMS_DAILY_LIMIT 条 → 429。
    """
    from sqlalchemy import func

    from ..models import SmsCode

    now = datetime.now(tz=timezone.utc)
    # 限流按**手机号**算，不分用途：否则同一个人可以「登录码 + 注册码」交替刷短信。
    latest = db.execute(
        select(SmsCode).where(SmsCode.phone == phone).order_by(SmsCode.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if latest is not None:
        latest_at = latest.created_at
        if latest_at.tzinfo is None:
            latest_at = latest_at.replace(tzinfo=timezone.utc)
        if (now - latest_at).total_seconds() < SMS_RESEND_SECONDS:
            raise TooManyRequests(
                f"验证码已发送，请 {SMS_RESEND_SECONDS} 秒后再试", code="SMS_TOO_FREQUENT"
            )

    if settings.sms_dev_mode:
        sent_today = db.execute(
            select(func.count())
            .select_from(SmsCode)
            .where(SmsCode.phone == phone, SmsCode.created_at >= now - timedelta(days=1))
        ).scalar_one()
        if sent_today >= SMS_DAILY_LIMIT:
            raise TooManyRequests("该手机号今日验证码发送次数已达上限", code="SMS_DAILY_LIMIT")

    code = (code or (_sms_fixed_code() or "")).strip() or f"{secrets.randbelow(10**6):06d}"
    db.add(
        SmsCode(
            phone=phone,
            purpose=purpose,
            code_hash=_code_digest(phone, purpose, code),
            expires_at=now + timedelta(seconds=SMS_CODE_TTL_SECONDS),
        )
    )
    db.commit()
    return code, SMS_CODE_TTL_SECONDS


def _sms_fixed_code() -> str | None:
    """开发模式（SMS_DEV_MODE）下固定验证码，并在接口响应里返回，便于联调。"""
    if settings.sms_dev_mode:
        return settings.sms_dev_code or "123456"
    return None


def consume_sms_code(db: Session, phone: str, purpose: str, code: str) -> None:
    """校验并作废一条验证码；任何一条不满足都抛 401。

    只接受最新一条未使用的记录，且必须是同号同用途：登录码不能拿去注册/改密。
    """
    from ..models import SmsCode

    record = db.execute(
        select(SmsCode)
        .where(SmsCode.phone == phone, SmsCode.purpose == purpose, SmsCode.used_at.is_(None))
        .order_by(SmsCode.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if record is None:
        raise Unauthorized("验证码不正确或已过期", code="SMS_CODE_INVALID")

    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(tz=timezone.utc):
        raise Unauthorized("验证码不正确或已过期", code="SMS_CODE_INVALID")

    if not hmac.compare_digest(record.code_hash, _code_digest(phone, purpose, (code or "").strip())):
        # 记一次失败次数，便于后续做「错 5 次锁 15 分钟」
        record.attempts = (record.attempts or 0) + 1
        db.commit()
        raise Unauthorized("验证码不正确或已过期", code="SMS_CODE_INVALID")

    record.used_at = datetime.now(tz=timezone.utc)
    db.commit()


__all__ = [
    "AppError",
    "SMS_CODE_TTL_SECONDS",
    "SMS_RESEND_SECONDS",
    "consume_sms_code",
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "is_valid_email",
    "is_valid_password",
    "is_valid_phone",
    "issue_sms_code",
    "normalize_email",
    "normalize_phone",
    "verify_password",
]
