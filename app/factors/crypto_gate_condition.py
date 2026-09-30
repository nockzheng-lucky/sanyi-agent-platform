"""币圈门条件因子（影子模式专用）。

数据源：东京币圈服务器 sanyi-bybit-tokyo-01 的 GET /api/gates?view=fresh（只读）。
与期货 gate_condition 共用筛选/汇总逻辑，差异只在数据源与 SSL 校验开关。
"""
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

from ..config import (
    CRYPTO_GATE_API_URL,
    CRYPTO_GATE_CONNECT_TIMEOUT_SECONDS,
    CRYPTO_GATE_TIMEOUT_SECONDS,
    CRYPTO_VERIFY_SSL,
)
from .base import FactorContext, FactorSpec, now_iso
from .gate_condition import (
    _PARAMS_SCHEMA,
    _OUTPUT_SCHEMA,
    _evaluate_with_fetch,
    normalize_gate_datetimes,
)

FACTOR_KEY = "crypto_gate_condition"

_CACHE: Dict[str, Any] = {"at": 0.0, "payload": None}
_CACHE_TTL_SECONDS = 15.0


async def _fetch_gates(client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    if not CRYPTO_GATE_API_URL:
        raise HTTPException(
            status_code=503,
            detail={"code": 503, "message": "币圈门数据源未配置，请设置 SANYI_CRYPTO_GATE_API_URL", "data": None},
        )
    owns_client = client is None
    if owns_client:
        now = time.monotonic()
        if _CACHE["payload"] is not None and now - _CACHE["at"] < _CACHE_TTL_SECONDS:
            return _CACHE["payload"]
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                CRYPTO_GATE_TIMEOUT_SECONDS,
                connect=min(CRYPTO_GATE_CONNECT_TIMEOUT_SECONDS, CRYPTO_GATE_TIMEOUT_SECONDS),
            ),
            verify=CRYPTO_VERIFY_SSL,
        )
    try:
        try:
            response = await client.get(CRYPTO_GATE_API_URL)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": 502, "message": "币圈门数据源不可用：%s" % exc.__class__.__name__, "data": None},
            )
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail={"code": 502, "message": "币圈门数据源返回 HTTP %s" % response.status_code, "data": None},
            )
        try:
            payload = response.json()
        except ValueError:
            raise HTTPException(
                status_code=502,
                detail={"code": 502, "message": "币圈门数据源返回了非 JSON 内容", "data": None},
            )
    finally:
        if owns_client:
            await client.aclose()
    if not isinstance(payload, dict) or not isinstance(payload.get("gates"), list):
        raise HTTPException(
            status_code=502,
            detail={"code": 502, "message": "币圈门数据源响应结构不正确", "data": None},
        )
    payload = normalize_gate_datetimes(payload, "+00:00")
    if owns_client:
        _CACHE["at"] = time.monotonic()
        _CACHE["payload"] = payload
    return payload


async def _evaluate(params: Dict[str, Any], ctx: FactorContext) -> Dict[str, Any]:
    return await _evaluate_with_fetch(params, ctx, _fetch_gates)


crypto_gate_condition = FactorSpec(
    factor_key=FACTOR_KEY,
    name="币圈门条件（影子模式）",
    description=(
        "影子模式专用：读取东京币圈服务器当前门池，可按币种、周期、天门/地门、"
        "已开 / 已关 / 无动作门上 / 无动作门下等生命周期状态过滤，"
        "并支持门价/现价相对 MA208 的位置过滤；输出口径与期货门条件一致。"
    ),
    params_schema=_PARAMS_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    cost=0,
    cache_seconds=30,
    risk_note="币圈门信号仅用于研究观察，不构成投资建议；加密货币波动极大，需严格控制风险。",
    handler=_evaluate,
    tags=["crypto", "gate", "tian", "di", "open", "close", "formation", "shadow"],
    shadow_only=True,
    domain="crypto",
    event_based=False,
)
