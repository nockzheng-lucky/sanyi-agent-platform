from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..accounts import (
    create_sms_code,
    create_user,
    create_user_session,
    delete_user_session,
    find_user_by_phone,
    normalize_phone,
    touch_user_login,
    verify_password,
    verify_sms_code,
)
from ..config import COOKIE_SECURE
from ..security import USER_COOKIE_NAME, get_user_session

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class SmsCodeRequest(BaseModel):
    phone: str
    purpose: str = "register"


class RegisterRequest(BaseModel):
    phone: str
    code: str
    password: str
    agree: bool = False


class LoginRequest(BaseModel):
    phone: str
    password: str


def _set_user_cookie(response: Response, session_raw: str, hours: int = 12) -> None:
    response.set_cookie(
        USER_COOKIE_NAME,
        session_raw,
        max_age=hours * 3600,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/sms-code")
async def sms_code(payload: SmsCodeRequest, request: Request):
    phone = normalize_phone(payload.phone)
    if phone is None:
        raise HTTPException(status_code=422, detail={"code": 422, "message": "手机号格式不正确", "data": None})
    if payload.purpose not in ("register",):
        raise HTTPException(status_code=422, detail={"code": 422, "message": "不支持的验证码用途", "data": None})
    try:
        masked, debug_code = create_sms_code(phone, payload.purpose)
    except PermissionError as exc:
        raise HTTPException(status_code=429, detail={"code": 429, "message": str(exc), "data": None})
    return {"code": 0, "message": "验证码已发送", "data": {"phoneMasked": masked, "debugCode": debug_code}}


@router.post("/register")
async def register(payload: RegisterRequest, request: Request, response: Response):
    if not payload.agree:
        raise HTTPException(status_code=422, detail={"code": 422, "message": "请先同意用户协议", "data": None})
    phone = normalize_phone(payload.phone)
    if phone is None:
        raise HTTPException(status_code=422, detail={"code": 422, "message": "手机号格式不正确", "data": None})
    if len(payload.password) < 8:
        raise HTTPException(status_code=422, detail={"code": 422, "message": "密码至少 8 位", "data": None})
    if not verify_sms_code(phone, payload.code, "register"):
        raise HTTPException(status_code=400, detail={"code": 400, "message": "验证码错误或已过期", "data": None})
    if find_user_by_phone(phone) is not None:
        raise HTTPException(status_code=409, detail={"code": 409, "message": "该手机号已注册", "data": None})

    user = create_user(phone, payload.password)
    session_raw = create_user_session(user["id"], request.headers.get("user-agent", ""), _client_ip(request))
    _set_user_cookie(response, session_raw)
    return {
        "code": 0,
        "message": "注册成功",
        "data": {"userId": user["id"], "phoneMasked": user["phoneMasked"]},
    }


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    phone = normalize_phone(payload.phone)
    if phone is None:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "手机号或密码错误", "data": None})
    user = find_user_by_phone(phone)
    if user is None or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail={"code": 401, "message": "手机号或密码错误", "data": None})
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail={"code": 403, "message": "账号不可用", "data": None})
    touch_user_login(user["id"])
    session_raw = create_user_session(user["id"], request.headers.get("user-agent", ""), _client_ip(request))
    _set_user_cookie(response, session_raw)
    return {"code": 0, "message": "登录成功", "data": {"userId": user["id"], "phoneMasked": user["phone_masked"]}}


@router.get("/me")
async def me(user: dict = Depends(get_user_session)):
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "userId": user["id"],
            "phoneMasked": user["phone_masked"],
            "status": user["status"],
            "role": user.get("role", "user"),
            "shadowMode": int(user.get("shadow_mode") or 0) == 1,
        },
    }


@router.post("/logout")
async def logout(request: Request, response: Response, user: dict = Depends(get_user_session)):
    raw = request.cookies.get(USER_COOKIE_NAME, "")
    delete_user_session(raw)
    response.delete_cookie(USER_COOKIE_NAME, path="/")
    return {"code": 0, "message": "ok", "data": None}
