from fastapi import APIRouter, Depends, Request

from ..db import issue_token, list_usage
from ..schemas import ApiResponse, TokenIssueRequest
from ..security import require_admin, require_token

router = APIRouter(prefix="/api/v1", tags=["tokens"])


def _sanitize(record: dict) -> dict:
    return {
        "id": record["id"],
        "name": record["name"],
        "status": record["status"],
        "quotaTotal": record["quota_total"],
        "quotaUsed": record["quota_used"],
        "rateLimitPerMin": record["rate_limit_per_min"],
        "expiresAt": record["expires_at"],
        "createdAt": record["created_at"],
    }


@router.post("/tokens/issue", response_model=ApiResponse)
async def issue(payload: TokenIssueRequest, request: Request, _: None = Depends(require_admin)):
    """原型期签发令牌。默认关闭；正式版换成 New API 的用户申领流程。"""
    created = issue_token(
        name=payload.name,
        quota_total=payload.quotaTotal,
        rate_limit_per_min=payload.rateLimitPerMin,
        expires_in_days=payload.expiresInDays,
    )
    return ApiResponse(data=created)


@router.get("/me", response_model=ApiResponse)
async def me(token: dict = Depends(require_token)):
    return ApiResponse(data={"token": _sanitize(token)})


@router.get("/me/usage", response_model=ApiResponse)
async def usage(token: dict = Depends(require_token)):
    rows = list_usage(token["id"], limit=50)
    return ApiResponse(data={"usage": rows})
