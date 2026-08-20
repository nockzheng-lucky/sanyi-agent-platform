"""15 分钟地门开·做多信号（第一个因子，占位实现）。

业务含义（从现有 sanyi_core 代码里确认到的基础概念）：
- 地门：MACD 负 DIF 区间内的波段底背离结构；
- 地门开：收盘价严格上穿门价（gate_price）；
- 15 分钟：在 15m K 线序列上计算。

⚠️ 本文件的真实规则、阈值、过滤条件由 Owner 后续填写。
当前返回 MOCK 结果，只用于把“令牌 → 页面 Agent → 因子服务 → 结果”链路跑通。
"""
from datetime import datetime, timezone
from typing import Any, Dict

from .base import FactorContext, FactorSpec

FACTOR_KEY = "dimen_gate_15m_long"

_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        # TODO(Owner)：按最终因子定义收敛参数。这里先留两个最通用的参数。
        "symbols": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。品种或合约代码列表；留空表示扫描当前接入的全部品种。",
        },
        "maxAgeMinutes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1440,
            "default": 30,
            "description": "可选。只返回最近 N 分钟内开门的地门信号；默认 30 分钟。",
        },
    },
    "additionalProperties": False,
}

_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "factorKey": {"type": "string"},
        "signal": {"type": "string", "description": "LONG / NONE"},
        "score": {"type": "number"},
        "summary": {"type": "string"},
        "generatedAt": {"type": "string", "format": "date-time"},
        "details": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "frequency": {"type": "string"},
                "gatePrice": {"type": "number"},
                "openTime": {"type": "string"},
                "openBarTime": {"type": "string"},
                "isMock": {"type": "boolean"},
            },
        },
        "riskNote": {"type": "string"},
    },
    "required": ["factorKey", "signal", "summary", "generatedAt", "details", "riskNote"],
}


async def _evaluate(params: Dict[str, Any], ctx: FactorContext) -> Dict[str, Any]:
    """占位计算。

    TODO(Owner)：把这里替换为三易引擎的真实计算。
    可参考但不限于：
      - sanyi_core.gates.di_men.detect_all_di_men / detect_all_di_men_v2
      - sanyi_core.gates.lifecycle.gate_lifecycle（开门 = close 上穿 gate_price）
      - 15m 数据缓存/行情来源
    注意：不要在这里直连生产数据库或写盘；数据访问应通过 engine adapter 层。
    """
    from ..config import FACTOR_DIMEN_GATE_MOCK

    if not FACTOR_DIMEN_GATE_MOCK:
        raise NotImplementedError("真实因子规则尚未接入：FACTOR_DIMEN_GATE_MOCK 已关闭")

    return {
        "factorKey": FACTOR_KEY,
        "signal": "NONE",
        "score": 0.0,
        "summary": "占位结果：15 分钟地门开·做多信号规则尚未接入（MOCK）。"
        "当前链路已通：令牌鉴权 → Agent 工具调用 → 因子服务 → 返回结构。",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "details": {
            "frequency": "15m",
            "isMock": True,
            "note": "TODO(Owner)：填充 gatePrice / openTime / symbol 等真实字段",
        },
        "riskNote": "本因子仅用于观察与研究，不构成任何投资建议；MOCK 数据不可用于决策。",
    }


dimen_gate_15m_long = FactorSpec(
    factor_key=FACTOR_KEY,
    name="15 分钟地门开·做多信号",
    description=(
        "扫描 15 分钟周期上已经开门的地门（MACD 负 DIF 区间的底背离结构，"
        "收盘价上穿门价确认开门），输出做多观察信号。"
    ),
    params_schema=_PARAMS_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    cost=10,
    cache_seconds=30,
    risk_note=(
        "该因子是技术结构观察信号，不代表未来涨跌，不应单独作为交易依据；"
        "实际决策需结合流动性、仓位、风控与合规要求。"
    ),
    handler=_evaluate,
    tags=["dimen_gate", "15m", "long", "mock"],
)
