from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..accounts import (
    PLANS,
    create_subscription_request,
    is_subscription_active,
    list_my_subscription_requests,
)
from ..security import get_user_session

router = APIRouter(prefix="/api/v1/subscription", tags=["subscription"])


class SubscriptionApplyRequest(BaseModel):
    plan: str = "monthly"
    paymentNote: str = ""


@router.get("")
async def subscription(user: dict = Depends(get_user_session)):
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "active": is_subscription_active(user),
            "plan": user.get("subscription_plan"),
            "expiresAt": user.get("subscription_expires_at"),
            "plans": PLANS,
        },
    }


@router.get("/requests")
async def my_requests(user: dict = Depends(get_user_session)):
    return {"code": 0, "message": "ok", "data": {"requests": list_my_subscription_requests(user["id"])}}


@router.post("/requests")
async def apply(payload: SubscriptionApplyRequest, user: dict = Depends(get_user_session)):
    try:
        created = create_subscription_request(user["id"], payload.plan, payload.paymentNote)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": 422, "message": str(exc), "data": None})
    return {
        "code": 0,
        "message": "申请已提交，请按约定完成支付，管理员确认后自动开启权益",
        "data": created,
    }
