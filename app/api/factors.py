from fastapi import APIRouter, Depends, Request

from ..factor_registry import registry
from ..schemas import ApiResponse, FactorEvaluateRequest
from ..security import require_token

router = APIRouter(prefix="/api/v1", tags=["factors"])


@router.get("/factors", response_model=ApiResponse)
async def list_factors(token: dict = Depends(require_token)):
    """列出可用因子元信息（免费，不扣额度）。"""
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
