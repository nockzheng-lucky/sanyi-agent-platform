from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..accounts import is_shadow_mode
from ..factor_registry import registry
from ..schemas import ApiResponse, FactorEvaluateRequest
from ..security import get_token_or_user_session, require_token

router = APIRouter(prefix="/api/v1", tags=["factors"])


@router.get("/factors", response_model=ApiResponse)
async def list_factors(
    domain: str = Query(default="", description="futures=期货因子页；crypto=币圈因子页；空=全部可见因子"),
    token: dict = Depends(get_token_or_user_session),
):
    """列出可用因子元信息（免费，不扣额度）。

    页面按 domain 分别请求，期货和币圈因子不会混在一起。
    """
    domain = (domain or "").strip()
    if domain and domain not in ("futures", "crypto"):
        raise HTTPException(status_code=422, detail={"code": 422, "message": "domain 仅支持 futures / crypto", "data": None})
    return ApiResponse(data={"factors": registry.descriptors(for_record=token, domain=domain or None)})


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
