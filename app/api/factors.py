from fastapi import APIRouter, Depends, Request

from ..factor_registry import registry
from ..schemas import ApiResponse, FactorEvaluateRequest
from ..security import get_token_or_user_session, require_token

router = APIRouter(prefix="/api/v1", tags=["factors"])


@router.get("/factors", response_model=ApiResponse)
async def list_factors(token: dict = Depends(get_token_or_user_session)):
    """列出可用因子元信息（免费，不扣额度）。

    外部 Agent 走 X-API-Token/Bearer；页面用户凭登录会话也可读取，
    但执行因子仍需要有效 Key / 订阅。
    """
    return ApiResponse(data={"factors": registry.descriptors()})


@router.post("/factors/evaluate", response_model=ApiResponse)
async def evaluate_factor(
    payload: FactorEvaluateRequest,
    request: Request,
    token: dict = Depends(require_token),
):
    """统一因子执行入口：按 factorKey 路由，成功后扣费。"""
    result = await registry.evaluate(
        token_record=token,
        factor_key=payload.factorKey,
        params=payload.params,
        request_id=payload.requestId,
    )
    return ApiResponse(data=result)
