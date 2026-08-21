from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..accounts import get_user, user_key_sanitize
from ..agent.loop import run_agent_stream
from ..config import COOKIE_SECURE, SYSTEM_NAME
from ..db import authenticate, create_session, delete_session, get_token_record
from ..schemas import ChatRequest
from ..security import get_actor, get_session_token

router = APIRouter(prefix="/api/chat", tags=["chat"])

COOKIE_NAME = "sanyi_session"


class LoginRequest(BaseModel):
    token: str
    rememberHours: Optional[int] = 12


def _sanitize(record: dict) -> dict:
    return {
        "id": record["id"],
        "name": record["name"],
        "quotaTotal": record["quota_total"],
        "quotaUsed": record["quota_used"],
    }


def set_session_cookie(response: Response, session_raw: str, remember_hours: int = 12) -> None:
    # 原型用 HttpOnly cookie；生产环境必须同时设置 secure=True + HTTPS。
    response.set_cookie(
        COOKIE_NAME,
        session_raw,
        max_age=(remember_hours or 12) * 3600,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )


@router.get("/session")
async def session(actor: dict = Depends(get_actor)):
    data: dict = {"system": SYSTEM_NAME}
    if actor.get("_table") == "user_keys":
        user = get_user(actor["_user_id"])
        data["user"] = {
            "id": user["id"],
            "phoneMasked": user["phone_masked"],
            "status": user["status"],
        } if user else None
        data["key"] = user_key_sanitize(actor)
    else:
        data["token"] = _sanitize(actor)
    return {"code": 0, "message": "ok", "data": data}


@router.post("/login")
async def login(payload: LoginRequest, response: Response):
    record = authenticate(payload.token)
    if record is None:
        raise HTTPException(
            status_code=401,
            detail={"code": 401, "message": "令牌无效、已过期或额度已用尽", "data": None},
        )
    session_raw = create_session(record["id"], ttl_hours=payload.rememberHours or 12)
    set_session_cookie(response, session_raw, payload.rememberHours or 12)
    return {"code": 0, "message": "ok", "data": {"token": _sanitize(record)}}


@router.post("/logout")
async def logout(request: Request, response: Response, token: dict = Depends(get_session_token)):
    raw = request.cookies.get(COOKIE_NAME, "")
    delete_session(raw)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"code": 0, "message": "ok", "data": None}


@router.post("")
async def chat(payload: ChatRequest, actor: dict = Depends(get_actor)):
    """SSE 流式聊天；页面走用户会话 Cookie，外部 Agent 走 X-API-Token。"""
    return StreamingResponse(
        run_agent_stream(
            messages=[m.model_dump() for m in payload.messages],
            token_record=actor,
            request_id=payload.requestId or "",
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
