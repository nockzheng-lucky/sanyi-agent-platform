"""币圈诀与破诀因子（影子模式专用）。

数据源：东京币圈服务器 sanyi-bybit-tokyo-01 的 /api/market/jue-direction。
该接口与 qh.shhghf.com 的期货诀方向接口同构，只是品种是 BTC/ETH 等币圈合约。
"""
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from ..config import CRYPTO_JUE_URL, CRYPTO_TIMEOUT_SECONDS, CRYPTO_VERIFY_SSL
from .base import FactorContext, FactorSpec, now_iso

FACTOR_KEY = "crypto_market"

ALL_FREQUENCIES = ("5m", "15m", "1h", "1d", "1w", "1M")
DEFAULT_STATES = ("80诀", "80诀破诀", "20诀", "20诀破诀")
ALL_DIRECTIONS = ("long", "short", "none")

_FREQ_ORDER = {freq: idx for idx, freq in enumerate(ALL_FREQUENCIES)}
_DIRECTION_ORDER = {"long": 0, "short": 1, "none": 2}

_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "frequencies": {
            "type": "array",
            "items": {"type": "string", "enum": list(ALL_FREQUENCIES)},
            "description": "可选。默认全部周期：5m / 15m / 1h / 1d / 1w / 1M。",
        },
        "symbols": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。只返回这些交易对，例如 [\"BTCUSDT\", \"ETHUSDT\"]。",
        },
        "states": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["80诀", "80诀破诀", "20诀", "20诀破诀", "无诀"],
            },
            "description": "可选。默认只返回四种诀状态，不含“无诀”。",
        },
        "directions": {
            "type": "array",
            "items": {"type": "string", "enum": list(ALL_DIRECTIONS)},
            "description": "可选。默认只返回多 / 空。",
        },
        "broken": {
            "type": ["boolean", "null"],
            "description": "可选。null=不限；true=只看破诀；false=只看未破。",
        },
        "walkCodes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。只保留这些走法代码，例如 [\"2\"] 表示“走2”。",
        },
        "walkMarks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。只保留特殊标记，例如 [\"20破·走2\", \"80破·走B\"]。",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 30,
            "description": "可选。最多返回多少个诀单元，默认 30。",
        },
    },
    "additionalProperties": False,
}

_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "factorKey": {"type": "string"},
        "signal": {"type": "string", "enum": ["LONG", "SHORT", "MIXED", "NONE"]},
        "score": {"type": "number"},
        "summary": {"type": "string"},
        "generatedAt": {"type": "string", "format": "date-time"},
        "details": {
            "type": "object",
            "properties": {
                "updatedAt": {"type": ["string", "null"]},
                "matchedCells": {"type": "integer"},
                "returnedCells": {"type": "integer"},
                "counts": {
                    "type": "object",
                    "properties": {
                        "long": {"type": "integer"},
                        "short": {"type": "integer"},
                        "none": {"type": "integer"},
                        "broken": {"type": "integer"},
                        "pending": {"type": "integer"},
                        "pairConfirm": {"type": "integer"},
                        "walkMark20_2": {"type": "integer"},
                        "walkMark80_B": {"type": "integer"},
                    },
                },
                "stateRules": {"type": "array"},
                "cells": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "name": {"type": "string"},
                            "sector": {"type": "string"},
                            "contract": {"type": ["string", "null"]},
                            "frequency": {"type": "string"},
                            "state": {"type": "string"},
                            "direction": {"type": ["string", "null"]},
                            "side": {"type": ["string", "null"]},
                            "threshold": {"type": ["integer", "null"]},
                            "price": {"type": ["number", "null"]},
                            "broken": {"type": "boolean"},
                            "gap": {"type": "boolean"},
                            "pending": {"type": "boolean"},
                            "rsi3": {"type": ["number", "null"]},
                            "walkState": {"type": ["string", "null"]},
                            "walkCode": {"type": ["string", "null"]},
                            "walkMark": {"type": ["string", "null"]},
                            "pairConfirmPrev": {"type": "boolean"},
                            "pairConfirmNext": {"type": "boolean"},
                            "updatedAt": {"type": ["string", "null"]},
                            "generatedAt": {"type": ["string", "null"]},
                        },
                    },
                },
            },
            "required": ["updatedAt", "matchedCells", "returnedCells", "counts", "stateRules", "cells"],
        },
        "riskNote": {"type": "string"},
    },
    "required": ["factorKey", "signal", "summary", "generatedAt", "details", "riskNote"],
}


async def _fetch_payload(client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    if not CRYPTO_JUE_URL:
        raise HTTPException(
            status_code=503,
            detail={"code": 503, "message": "币圈数据源未配置，请设置 SANYI_CRYPTO_JUE_DIRECTION_URL", "data": None},
        )
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(CRYPTO_TIMEOUT_SECONDS, connect=5.0),
            verify=CRYPTO_VERIFY_SSL,
        )
    try:
        try:
            response = await client.get(CRYPTO_JUE_URL)
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
    if not isinstance(payload, dict) or not isinstance(payload.get("sectors"), list):
        raise HTTPException(
            status_code=502,
            detail={"code": 502, "message": "币圈数据源响应结构不正确", "data": None},
        )
    return payload


def _norm_rules(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {
            "state": r.get("state"),
            "threshold": r.get("thr"),
            "broken": bool(r.get("broken")),
            "direction": r.get("direction"),
            "side": r.get("side"),
        }
        for r in (payload.get("state_rules") or [])
        if isinstance(r, dict)
    ]


def _cell_to_detail(sector: Dict[str, Any], item: Dict[str, Any], cell: Dict[str, Any], updated_at: Optional[str] = None) -> Dict[str, Any]:
    return {
        "symbol": item.get("sym"),
        "name": item.get("name"),
        "sector": sector.get("name"),
        "contract": item.get("label"),
        "frequency": cell.get("freq"),
        "state": cell.get("state"),
        "direction": cell.get("direction"),
        "side": cell.get("side"),
        "threshold": cell.get("thr"),
        "price": cell.get("price"),
        "broken": bool(cell.get("broken")),
        "gap": bool(cell.get("gap")),
        "pending": bool(cell.get("pending")),
        "rsi3": cell.get("rsi3"),
        "walkState": cell.get("walk_state") or None,
        "walkCode": cell.get("walk_code") or None,
        "walkMark": cell.get("walk_mark") or None,
        "pairConfirmPrev": bool(cell.get("pair_confirm_prev")),
        "pairConfirmNext": bool(cell.get("pair_confirm_next")),
        "updatedAt": updated_at,
        "generatedAt": updated_at,
    }


async def _evaluate(params: Dict[str, Any], ctx: FactorContext) -> Dict[str, Any]:
    frequencies = set(params.get("frequencies") or list(ALL_FREQUENCIES))
    symbols = {str(s).strip().upper() for s in (params.get("symbols") or []) if str(s).strip()}
    states = set(params.get("states") or list(DEFAULT_STATES))
    directions = set(params.get("directions") or ["long", "short"])
    walk_codes = {str(x).strip() for x in (params.get("walkCodes") or []) if str(x).strip()}
    walk_marks = {str(x).strip() for x in (params.get("walkMarks") or []) if str(x).strip()}
    broken_filter = params.get("broken")
    limit = int(params.get("limit") or 30)

    payload = await _fetch_payload()
    source_updated_at = payload.get("updated_at") or now_iso()
    rules = _norm_rules(payload)
    matched: List[Dict[str, Any]] = []
    for sector in payload.get("sectors") or []:
        if not isinstance(sector, dict):
            continue
        for item in sector.get("items") or []:
            if not isinstance(item, dict):
                continue
            sym = str(item.get("sym") or "")
            if symbols and (not sym or sym.upper() not in symbols):
                continue
            for cell in item.get("cells") or []:
                if not isinstance(cell, dict):
                    continue
                freq = str(cell.get("freq") or "")
                state = str(cell.get("state") or "")
                direction = cell.get("direction")
                broken = bool(cell.get("broken"))
                if frequencies and freq not in frequencies:
                    continue
                if states and state not in states:
                    continue
                if directions and direction not in directions:
                    continue
                if broken_filter is not None and broken is not bool(broken_filter):
                    continue
                walk_code = str(cell.get("walk_code") or "")
                walk_mark = str(cell.get("walk_mark") or "")
                if walk_codes and walk_code not in walk_codes:
                    continue
                if walk_marks and walk_mark not in walk_marks:
                    continue
                matched.append(_cell_to_detail(sector, item, cell, source_updated_at))

    matched.sort(
        key=lambda c: (
            _DIRECTION_ORDER.get(str(c["direction"] or "none"), 3),
            0 if c["broken"] else 1,
            _FREQ_ORDER.get(str(c["frequency"] or ""), 99),
            str(c["symbol"] or ""),
            str(c["state"] or ""),
        )
    )
    matched.sort(key=lambda c: str(c.get("updatedAt") or ""), reverse=True)

    returned = matched[:limit]
    total = len(matched)
    long = sum(1 for c in matched if c["direction"] == "long")
    short = sum(1 for c in matched if c["direction"] == "short")
    none = sum(1 for c in matched if c["direction"] in (None, "none"))
    broken_count = sum(1 for c in matched if c["broken"])
    pending = sum(1 for c in matched if c["pending"])
    pair_confirm = sum(1 for c in matched if c["pairConfirmNext"])
    walk_20_2 = sum(1 for c in matched if c["walkMark"] == "20破·走2")
    walk_80_b = sum(1 for c in matched if c["walkMark"] == "80破·走B")
    jue_total = long + short

    if total == 0:
        signal = "NONE"
        score = 0.0
        summary = "当前币圈筛选范围内没有诀 / 破诀状态。"
    else:
        if long > short:
            signal = "LONG"
        elif short > long:
            signal = "SHORT"
        else:
            signal = "MIXED" if jue_total > 0 else "NONE"
        score = 0.0 if jue_total == 0 else round(50.0 + 50.0 * (long - short) / jue_total, 1)
        side_text = "多头占优" if signal == "LONG" else ("空头占优" if signal == "SHORT" else "多空相当")
        examples = "；".join(
            "%s %s %s" % (c["symbol"], c["frequency"], c["state"]) for c in returned[:5]
        )
        summary = "币圈筛选范围内共 %d 个诀单元：多 %d / 空 %d / 破诀 %d / 待确认 %d，整体%s。" % (
            total, long, short, broken_count, pending, side_text,
        )
        if examples:
            summary += " 示例：%s%s。" % (examples, "…" if len(returned) > 5 else "")

    return {
        "factorKey": FACTOR_KEY,
        "signal": signal,
        "score": float(score),
        "summary": summary,
        "generatedAt": source_updated_at,
        "details": {
            "updatedAt": source_updated_at,
            "matchedCells": total,
            "returnedCells": len(returned),
            "counts": {
                "long": long,
                "short": short,
                "none": none,
                "broken": broken_count,
                "pending": pending,
                "pairConfirm": pair_confirm,
                "walkMark20_2": walk_20_2,
                "walkMark80_B": walk_80_b,
            },
            "stateRules": rules,
            "cells": returned,
        },
        "riskNote": "币圈诀/破诀仅用于研究观察，不构成投资建议；加密货币波动极大，需严格控制风险。",
    }


crypto_market = FactorSpec(
    factor_key=FACTOR_KEY,
    name="币圈诀与破诀（影子模式）",
    description=(
        "影子模式专用：读取东京币圈服务器的诀方向状态，语义与期货诀与破诀一致，"
        "但品种为 BTCUSDT / ETHUSDT 等币圈合约。覆盖 5m/15m/1h/1d/1w/1M。"
    ),
    params_schema=_PARAMS_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    cost=0,
    cache_seconds=30,
    risk_note="币圈诀/破诀仅用于研究观察，不构成投资建议；加密货币波动极大，需严格控制风险。",
    handler=_evaluate,
    tags=["crypto", "jue", "direction", "long", "short", "broken", "shadow"],
    shadow_only=True,
    domain="crypto",
)
