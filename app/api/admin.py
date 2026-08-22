from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..accounts import (
    list_all_keys,
    list_users_summary,
    revoke_any_user_key,
    set_user_status,
    set_user_role,
    user_key_sanitize,
)
from ..db import get_conn
from ..security import require_admin_user

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class UserStatusRequest(BaseModel):
    status: str


class UserRoleRequest(BaseModel):
    role: str


@router.get("/users")
async def users(admin: dict = Depends(require_admin_user)):
    return {"code": 0, "message": "ok", "data": {"users": list_users_summary()}}


@router.post("/users/{user_id}/status")
async def user_status(user_id: int, payload: UserStatusRequest, admin: dict = Depends(require_admin_user)):
    if not set_user_status(user_id, payload.status):
        raise HTTPException(status_code=404, detail={"code": 404, "message": "用户不存在或状态不合法", "data": None})
    return {"code": 0, "message": "ok", "data": None}


@router.post("/users/{user_id}/role")
async def user_role(user_id: int, payload: UserRoleRequest, admin: dict = Depends(require_admin_user)):
    if user_id == admin["id"] and payload.role != "admin":
        raise HTTPException(status_code=400, detail={"code": 400, "message": "不能移除自己的管理员权限", "data": None})
    if not set_user_role(user_id, payload.role):
        raise HTTPException(status_code=404, detail={"code": 404, "message": "用户不存在或角色不合法", "data": None})
    return {"code": 0, "message": "ok", "data": None}


@router.get("/keys")
async def keys(admin: dict = Depends(require_admin_user)):
    rows = list_all_keys()
    return {"code": 0, "message": "ok", "data": {"keys": [user_key_sanitize(r) | {"phoneMasked": r.get("phone_masked")} for r in rows]}}


@router.post("/keys/{key_id}/revoke")
async def revoke_key(key_id: int, admin: dict = Depends(require_admin_user)):
    if not revoke_any_user_key(key_id):
        raise HTTPException(status_code=404, detail={"code": 404, "message": "Key 不存在", "data": None})
    return {"code": 0, "message": "ok", "data": None}


@router.get("/usage")
async def usage(admin: dict = Depends(require_admin_user)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT u.phone_masked, k.name AS key_name, l.service, l.action, l.factor_key,"
        " l.model, l.input_tokens, l.output_tokens, l.cost, l.status, l.created_at"
        " FROM usage_logs l"
        " LEFT JOIN users u ON u.id = l.user_id"
        " LEFT JOIN user_keys k ON k.id = l.key_id"
        " ORDER BY l.id DESC LIMIT 100"
    ).fetchall()
    return {"code": 0, "message": "ok", "data": {"usage": [dict(r) for r in rows]}}
