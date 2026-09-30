"""币圈走法 × 破诀组合因子（影子模式专用，当前快照版）。

数据源复用 crypto_market 的东京币圈诀方向接口，组合口径与期货
wave_jue_combo 完全一致：走2·破20诀 / 走B·破80诀 / 走C·破20诀。
"""
from typing import Any, Dict

from .base import FactorContext, FactorSpec
from .crypto_market import _cell_to_detail, _fetch_payload
from .wave_jue_combo import (
    COMBOS,
    _PARAMS_SCHEMA,
    _OUTPUT_SCHEMA,
    _evaluate_with_fetch,
)

FACTOR_KEY = "crypto_wave_jue_combo"


async def _evaluate(params: Dict[str, Any], ctx: FactorContext) -> Dict[str, Any]:
    return await _evaluate_with_fetch(params, ctx, _fetch_payload, _cell_to_detail)


crypto_wave_jue_combo = FactorSpec(
    factor_key=FACTOR_KEY,
    name="币圈走法×破诀组合（影子模式）",
    description=(
        "影子模式专用：读取东京币圈服务器的最新快照，组合走2·破20诀、"
        "走B·破80诀、走C·破20诀；输出口径与期货 wave_jue_combo 一致。"
        "注意：当前数据源只给最新状态，不区分破诀与走法发生的先后顺序。"
    ),
    params_schema=_PARAMS_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    cost=0,
    cache_seconds=30,
    risk_note="币圈走法×破诀组合为当前快照口径，不区分破诀与走法发生先后；仅用于研究观察，不构成投资建议。",
    handler=_evaluate,
    tags=["crypto", "wave", "jue", "combo", "walk2", "walkB", "walkC", "shadow"],
    shadow_only=True,
    domain="crypto",
    event_based=False,
)
