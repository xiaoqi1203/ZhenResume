"""定价与支付接口（契约 §6.1）。

MVP 是 Mock 支付：下单返回一个假的 pay_url，回调直接改状态。
接真实渠道时只需替换 `_create_order` 里的 pay_url 生成与回调验签，其余逻辑不变。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import PlanNotFound
from ..models import Payment, User
from ..schemas import OrderCreate, PaymentCallback
from ..services.plans import PLANS, get_plan
from .deps import get_current_user

plans_router = APIRouter(tags=["billing"])
payments_router = APIRouter(prefix="/payments", tags=["billing"])


@plans_router.get("/plans")
def list_plans() -> dict:
    return {"items": PLANS}


@payments_router.post("/orders", status_code=201)
def create_order(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    plan = get_plan(payload.plan_code)
    if plan is None:
        raise PlanNotFound(f"未知套餐：{payload.plan_code}")

    seq = db.execute(select(func.count()).select_from(Payment)).scalar_one() + 1
    order_no = f"ZR{datetime.now(timezone.utc).strftime('%Y%m%d')}{seq:05d}"

    payment = Payment(
        user_id=user.id,
        order_no=order_no,
        amount=plan["price"],
        currency=plan["currency"],
        status="pending",
        provider=payload.provider,
        plan_code=plan["code"],
    )
    db.add(payment)
    db.commit()

    return {
        "order_no": order_no,
        "plan_code": plan["code"],
        "amount": plan["price"],
        "currency": plan["currency"],
        "provider": payload.provider,
        "status": "pending",
        # Mock 支付页：真实接入时换成渠道返回的收银台地址
        "pay_url": f"https://example.com/mock-pay/{order_no}",
    }


@payments_router.post("/callback")
def payment_callback(payload: PaymentCallback, db: Session = Depends(get_db)) -> dict:
    """幂等回调：重复推送同一 order_no 不会产生副作用。"""
    payment = db.execute(select(Payment).where(Payment.order_no == payload.order_no)).scalar_one_or_none()
    if payment is None:
        raise PlanNotFound(f"订单不存在：{payload.order_no}")

    payment.raw_callback = {"order_no": payload.order_no, "status": payload.status}
    if payment.status != "paid":
        payment.status = payload.status
    db.commit()

    return {"ok": True, "order_no": payment.order_no, "status": payment.status}
