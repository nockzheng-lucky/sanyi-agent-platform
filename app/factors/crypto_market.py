"""币圈行情因子（影子模式专用）。

数据源：Gate.io 现货 24h tickers 公共接口（可用 SANYI_CRYPTO_TICKERS_URL 替换）。
只开放给 shadow_mode=1 的账号；普通用户看不到也无法调用。
"""
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from ..config import CRYPTO_TIMEOUT_SECONDS, CRYPTO_TICKERS_URL
from .base import FactorContext, FactorSpec, now_iso

FACTOR_KEY = "crypto_market"

_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "symbols": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。只返回这些交易对，例如 [\"BTC_USDT\", \"ETH_USDT\"]。",
        },
        "quotes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。只保留这些计价币，默认 [\"USDT\"]。",
        },
        "directions": {
            "type": "array",
            "items": {"type": "string", "enum": ["up", "down", "flat"]},
            "description": "可选。按 24h 涨跌方向过滤，默认全部。",
        },
        "minChangePercent": {
            "type": "number",
            "minimum": -100,
            "maximum": 100,
            "description": "可选。最小 24h 涨跌幅（%），例如 3 表示只看涨超 3%。",
        },
        "maxChangePercent": {
            "type": "number",
            "minimum": -100,
            "maximum": 100,
            "description": "可选。最大 24h 涨跌幅（%），例如 -3 表示只看跌超 3%。",
        },
        "minQuoteVolume": {
            "type": "number",
            "minimum": 0,
            "description": "可选。最小计价币成交额，用于过滤低流动性交易对。",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 20,
            "description": "可选。最多返回多少个交易对，默认 20。",
        },
    },
    "additionalProperties": False,
}

_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "factorKey": {"type": "string"},
        "signal": {
            "type": "string",
            "enum": ["UP", "DOWN", "FLAT", "NONE"],
            "description": "按筛选范围内平均涨跌幅给出的市场方向。",
        },
        "score": {"type": "number"},
        "summary": {"type": "string"},
        "generatedAt": {"type": "string", "format": "date-time"},
        "details": {
            "type": "object",
            "properties": {
                "updatedAt": {"type": ["string", "null"]},
                "matchedSymbols": {"type": "integer"},
                "returnedSymbols": {"type": "integer"},
                "counts": {
                    "type": "object",
                    "properties": {
                        "up": {"type": "integer"},
                        "down": {"type": "integer"},
                        "flat": {"type": "integer"},
                        "averageChangePercent": {"type": ["number", "null"]},
                    },
                },
                "coins": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "base": {"type": "string"},
                            "quote": {"type": "string"},
                            "direction": {"type": "string", "enum": ["up", "down", "flat"]},
                            "last": {"type": ["number", "null"]},
                            "changePercent": {"type": ["number", "null"]},
                            "high24h": {"type": ["number", "null"]},
                            "low24h": {"type": ["number", "null"]},
                            "baseVolume": {"type": ["number", "null"]},
                            "quoteVolume": {"type": ["number", "null"]},
                            "highestBid": {"type": ["number", "null"]},
                            "lowestAsk": {"type": ["number", "null"]},
                            "updatedAt": {"type": ["string", "null"]},
                            "generatedAt": {"type": ["string", "null"]},
                        },
                    },
                },
            },
            "required": ["updatedAt", "matchedSymbols", "returnedSymbols", "counts", "coins"],
        },
        "riskNote": {"type": "string"},
    },
    "required": ["factorKey", "signal", "summary", "generatedAt", "details", "riskNote"],
}


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


async def _fetch_tickers(client: Optional[httpx.AsyncClient] = None) -> List[Dict[str, Any]]:
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(CRYPTO_TIMEOUT_SECONDS, connect=5.0))
    try:
        try:
            response = await client.get(CRYPTO_TICKERS_URL)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": 502, "message": "币圈数据源不可用：%s" % exc.__class__.__name__, "data": None},
            )
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail={"code": 502, "message": "币圈数据源返回 HTTP %s" % response.status_code, "data": None},
            )
        try:
            payload = response.json()
        except ValueError:
            raise HTTPException(
                status_code=502,
                detail={"code": 502, "message": "币圈数据源返回了非 JSON 内容", "data": None},
            )
    finally:
        if owns_client:
            await client.aclose()
    if not isinstance(payload, list):
        raise HTTPException(
            status_code=502,
            detail={"code": 502, "message": "币圈数据源响应结构不正确", "data": None},
        )
    return payload


def _direction(change: Optional[float]) -> str:
    if change is None:
        return "flat"
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


async def _evaluate(params: Dict[str, Any], ctx: FactorContext) -> Dict[str, Any]:
    symbols = {str(s).strip().upper() for s in (params.get("symbols") or []) if str(s).strip()}
    quotes = {str(q).strip().upper() for q in (params.get("quotes") or ["USDT"]) if str(q).strip()}
    directions = set(params.get("directions") or ["up", "down", "flat"])
    min_change = _num(params.get("minChangePercent"))
    max_change = _num(params.get("maxChangePercent"))
    min_quote_volume = _num(params.get("minQuoteVolume"))
    limit = int(params.get("limit") or 20)

    updated_at = now_iso()
    tickers = await _fetch_tickers()
    matched: List[Dict[str, Any]] = []
    for ticker in tickers:
        if not isinstance(ticker, dict):
            continue
        pair = str(ticker.get("currency_pair") or "").upper()
        if not pair or "_" not in pair:
            continue
        base, quote = pair.split("_", 1)
        if quotes and quote not in quotes:
            continue
        if symbols and pair not in symbols:
            continue
        change = _num(ticker.get("change_percentage"))
        direction = _direction(change)
        if directions and direction not in directions:
            continue
        if min_change is not None and (change is None or change < min_change):
            continue
        if max_change is not None and (change is None or change > max_change):
            continue
        quote_volume = _num(ticker.get("quote_volume"))
        if min_quote_volume is not None and (quote_volume is None or quote_volume < min_quote_volume):
            continue
        matched.append(
            {
                "symbol": pair,
                "base": base,
                "quote": quote,
                "direction": direction,
                "last": _num(ticker.get("last")),
                "changePercent": change,
                "high24h": _num(ticker.get("high_24h")),
                "low24h": _num(ticker.get("low_24h")),
                "baseVolume": _num(ticker.get("base_volume")),
                "quoteVolume": quote_volume,
                "highestBid": _num(ticker.get("highest_bid")),
                "lowestAsk": _num(ticker.get("lowest_ask")),
                "updatedAt": updated_at,
                "generatedAt": updated_at,
            }
        )

    # 最新数据优先用绝对涨跌幅排序，让市场最活跃/异动最大的币排在前面。
    matched.sort(key=lambda c: abs(c["changePercent"] or 0), reverse=True)
    returned = matched[:limit]

    changes = [c["changePercent"] for c in matched if c["changePercent"] is not None]
    avg_change = round(sum(changes) / len(changes), 2) if changes else None
    up = sum(1 for c in matched if c["changePercent"] is not None and c["changePercent"] > 0)
    down = sum(1 for c in matched if c["changePercent"] is not None and c["changePercent"] < 0)
    flat = len(matched) - up - down

    if not matched:
        signal = "NONE"
        score = 0.0
        summary = "当前筛选范围内没有币圈行情。"
    else:
        if avg_change is None or avg_change == 0:
            signal = "FLAT"
        elif avg_change > 0:
            signal = "UP"
        else:
            signal = "DOWN"
        score = float(max(0, min(100, round(50 + avg_change * 5, 1))))
        examples = "；".join(
            "%s %s%%" % (c["symbol"], c["changePercent"]) for c in returned[:5]
        )
        summary = (
            "币圈现货共 %d 个交易对：上涨 %d / 下跌 %d / 平盘 %d，平均涨跌 %s%%。"
            % (len(matched), up, down, flat, avg_change)
        )
        if examples:
            summary += " 异动最大：%s%s。" % (examples, "…" if len(returned) > 5 else "")

    return {
        "factorKey": FACTOR_KEY,
        "signal": signal,
        "score": score,
        "summary": summary,
        "generatedAt": updated_at,
        "details": {
            "updatedAt": updated_at,
            "matchedSymbols": len(matched),
            "returnedSymbols": len(returned),
            "counts": {
                "up": up,
                "down": down,
                "flat": flat,
                "averageChangePercent": avg_change,
            },
            "coins": returned,
        },
        "riskNote": "加密货币波动极大，仅用于研究观察，不构成投资建议。",
    }


crypto_market = FactorSpec(
    factor_key=FACTOR_KEY,
    name="币圈行情（影子模式）",
    description=(
        "影子模式专用：读取币圈现货 24h 行情（默认 Gate.io），"
        "可按交易对、计价币、涨跌方向、涨跌幅阈值和成交额过滤。"
    ),
    params_schema=_PARAMS_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    cost=0,
    cache_seconds=30,
    risk_note="加密货币波动极大，仅用于研究观察，不构成投资建议。",
    handler=_evaluate,
    tags=["crypto", "spot", "24h", "shadow"],
    shadow_only=True,
)
