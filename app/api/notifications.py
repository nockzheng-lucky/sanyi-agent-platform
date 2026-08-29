"""用户级 Pushplus 通知通道配置。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..push_channels import delete_pushplus_token, get_pushplus_channel, set_pushplus_token
from ..security import get_user_session

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class PushplusBindRequest(BaseModel):
    token: str


@router.get("/pushplus")
async def pushplus_status(user: dict = Depends(get_user_session)):
    channel = get_pushplus_channel(user["id"])
    return {
        "code": 0,
        "message": "ok",
        "data": {"pushplus": channel},
    }


@router.post("/pushplus")
async def pushplus_bind(payload: PushplusBindRequest, user: dict = Depends(get_user_session)):
    try:
        channel = set_pushplus_token(user["id"], payload.token)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": 422, "message": str(exc), "data": None})
    return {"code": 0, "message": "Pushplus 已绑定", "data": {"pushplus": channel}}


@router.delete("/pushplus")
async def pushplus_unbind(user: dict = Depends(get_user_session)):
    delete_pushplus_token(user["id"])
    return {"code": 0, "message": "Pushplus 已解绑", "data": None}
