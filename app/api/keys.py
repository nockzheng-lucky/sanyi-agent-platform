from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..accounts import (
    issue_user_key,
    list_user_keys,
    reveal_user_key,
    revoke_user_key,
    rotate_user_key,
    user_key_sanitize,
)
from ..db import get_conn
from ..security import get_user_session

router = APIRouter(prefix="/api/v1/keys", tags=["keys"])


class KeyCreateRequest(BaseModel):
    name: str
    expiresInDays: Optional[int] = None
    allowIps: str = ""


@router.get("")
async def my_keys(user: dict = Depends(get_user_session)):
    keys = [user_key_sanitize(k) for k in list_user_keys(user["id"])]
    return {"code": 0, "message": "ok", "data": {"keys": keys}}


@router.post("")
async def create_key(payload: KeyCreateRequest, user: dict = Depends(get_user_session)):
    active = [k for k in list_user_keys(user["id"]) if k["status"] == "active"]
    if len(active) >= 5:
        raise HTTPException(status_code=400, detail={"code": 400, "message": "最多同时持有 5 个 active Key", "data": None})
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail={"code": 422, "message": "请填写 Key 名称", "data": None})
    created = issue_user_key(
        user["id"],
        payload.name.strip(),
        expires_in_days=payload.expiresInDays,
        allow_ips=payload.allowIps.strip(),
        rate_limit_per_min=60,
        quota_total=-1,
    )
    return {"code": 0, "message": "申请成功，请立即保存", "data": created}


@router.post("/{key_id}/revoke")
async def revoke(key_id: int, user: dict = Depends(get_user_session)):
    if not revoke_user_key(user["id"], key_id):
        raise HTTPException(status_code=404, detail={"code": 404, "message": "Key 不存在", "data": None})
    return {"code": 0, "message": "已撤销", "data": None}


@router.post("/{key_id}/rotate")
async def rotate(key_id: int, user: dict = Depends(get_user_session)):
    created = rotate_user_key(user["id"], key_id)
    if created is None:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "Key 不存在", "data": None})
    return {"code": 0, "message": "轮换成功，请立即保存新 Key", "data": created}


@router.post("/{key_id}/reveal")
async def reveal(key_id: int, user: dict = Depends(get_user_session)):
    raw = reveal_user_key(user["id"], key_id)
    if raw is None:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "Key 不存在或旧 Key 不支持查看，请轮换", "data": None})
    return {"code": 0, "message": "ok", "data": {"token": raw}}


@router.get("/{key_id}/usage")
async def key_usage(key_id: int, user: dict = Depends(get_user_session)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, service, action, factor_key, model, input_tokens, output_tokens,"
        " cost, status, request_id, created_at FROM usage_logs WHERE key_id = ?"
        " ORDER BY id DESC LIMIT 50",
        (key_id,),
    ).fetchall()
    return {"code": 0, "message": "ok", "data": {"usage": [dict(r) for r in rows]}}
