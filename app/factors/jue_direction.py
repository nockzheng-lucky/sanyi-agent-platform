"""诀与破诀因子：全品种期货市场的 80/20 诀方向与破诀状态。

数据源：qh.shhghf.com 的 GET /api/market/jue-direction（只读 HTTP）。

语义（以 API state_rules 为准）：
- 80诀       thr=80，未破，方向多
- 80诀破诀   thr=80，已破，方向空
- 20诀       thr=20，未破，方向空
- 20诀破诀   thr=20，已破，方向多
- 无诀       无结构

本因子只负责查询和结构化，不重算 qh 引擎；每个 cell 是
「品种 × 周期」的一个诀状态单元。
"""
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from ..config import JUE_DIRECTION_TIMEOUT_SECONDS, JUE_DIRECTION_URL
from .base import FactorContext, FactorSpec, now_iso

FACTOR_KEY = "jue_direction"

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
            "description": "可选。只返回这些品种（API 的 sym，如 AU0 / CU0）；留空表示全部。",
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
            "description": "可选。默认只返回多 / 空，不含无方向单元。",
        },
        "broken": {
            "type": ["boolean", "null"],
            "description": "可选。null=不限；true=只看破诀；false=只看未破。",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 30,
            "description": "可选。最多返回多少个诀单元；默认 30，控制上下文长度。",
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
            "enum": ["LONG", "SHORT", "MIXED", "NONE"],
            "description": "LONG=筛选范围内多头占优；SHORT=空头占优；MIXED=多空数量相当；NONE=无诀状态。",
        },
        "score": {"type": "number", "description": "0-100 的方向一致性，50 表示多空各半。"},
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
                "stateRules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "state": {"type": "string"},
                            "threshold": {"type": ["integer", "null"]},
                            "broken": {"type": "boolean"},
                            "direction": {"type": ["string", "null"]},
                            "side": {"type": ["string", "null"]},
                        },
                    },
                },
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


async def _fetch_raw(client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    """读取 qh 诀方向 API。测试可 monkeypatch 本函数，避免外网依赖。"""
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(JUE_DIRECTION_TIMEOUT_SECONDS, connect=5.0))
    try:
        try:
            response = await client.get(JUE_DIRECTION_URL)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": 502, "message": "诀方向数据源不可用：%s" % exc.__class__.__name__, "data": None},
            )
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": 502,
                    "message": "诀方向数据源返回 HTTP %s" % response.status_code,
                    "data": None,
                },
            )
        try:
            payload = response.json()
        except ValueError:
            raise HTTPException(
                status_code=502,
                detail={"code": 502, "message": "诀方向数据源返回了非 JSON 内容", "data": None},
            )
    finally:
        if owns_client:
            await client.aclose()

    if not isinstance(payload, dict) or not isinstance(payload.get("sectors"), list):
        raise HTTPException(
            status_code=502,
            detail={"code": 502, "message": "诀方向数据源响应结构不正确", "data": None},
        )
    return payload


def _norm_rules(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rules = payload.get("state_rules") or []
    out = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        out.append(
            {
                "state": rule.get("state"),
                "threshold": rule.get("thr"),
                "broken": bool(rule.get("broken")),
                "direction": rule.get("direction"),
                "side": rule.get("side"),
            }
        )
    return out


def _cell_to_detail(sector: Dict[str, Any], item: Dict[str, Any], cell: Dict[str, Any]) -> Dict[str, Any]:
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
    }


async def _evaluate(params: Dict[str, Any], ctx: FactorContext) -> Dict[str, Any]:
    frequencies = set(params.get("frequencies") or list(ALL_FREQUENCIES))
    symbols = {str(s).strip().upper() for s in (params.get("symbols") or []) if str(s).strip()}
    states = set(params.get("states") or list(DEFAULT_STATES))
    directions = set(params.get("directions") or ["long", "short"])
    broken_filter = params.get("broken")
    limit = int(params.get("limit") or 30)

    payload = await _fetch_raw()
    sectors = payload.get("sectors") or []
    rules = _norm_rules(payload)

    matched: List[Dict[str, Any]] = []
    for sector in sectors:
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
                matched.append(_cell_to_detail(sector, item, cell))

    matched.sort(
        key=lambda c: (
            _DIRECTION_ORDER.get(str(c["direction"] or "none"), 3),
            0 if c["broken"] else 1,
            _FREQ_ORDER.get(str(c["frequency"] or ""), 99),
            str(c["symbol"] or ""),
            str(c["state"] or ""),
        )
    )

    long = sum(1 for c in matched if c["direction"] == "long")
    short = sum(1 for c in matched if c["direction"] == "short")
    none = sum(1 for c in matched if c["direction"] in (None, "none"))
    broken_count = sum(1 for c in matched if c["broken"])
    pending = sum(1 for c in matched if c["pending"])
    # API 的 pair_confirm 以“成对确认”计数；prev/next 会落在相邻两个 cell 上，
    # 这里数 next 即可避免把一对算两次。
    pair_confirm = sum(1 for c in matched if c["pairConfirmNext"])
    walk_20_2 = sum(1 for c in matched if c["walkMark"] == "20破·走2")
    walk_80_b = sum(1 for c in matched if c["walkMark"] == "80破·走B")

    returned = matched[:limit]
    total = len(matched)
    jue_total = long + short

    if total == 0:
        signal = "NONE"
        score = 0.0
        summary = "当前筛选范围内没有诀 / 破诀状态。"
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
        summary = (
            "筛选范围内共 %d 个诀单元：多 %d / 空 %d / 破诀 %d / 待确认 %d，整体%s。"
            % (total, long, short, broken_count, pending, side_text)
        )
        if examples:
            summary += " 示例：%s%s。" % (examples, "…" if len(returned) > 5 else "")
        if total > len(returned):
            summary += " 完整明细请缩小范围或调大 limit。"

    return {
        "factorKey": FACTOR_KEY,
        "signal": signal,
        "score": float(score),
        "summary": summary,
        "generatedAt": payload.get("updated_at") or now_iso(),
        "details": {
            "updatedAt": payload.get("updated_at"),
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
        "riskNote": (
            "诀/破诀是多空结构观察指标，不构成投资建议；"
            "破诀表示原结构被反向打破，需结合更大周期方向、流动性与风控使用。"
        ),
    }


jue_direction = FactorSpec(
    factor_key=FACTOR_KEY,
    name="诀与破诀（80/20 诀方向与破诀）",
    description=(
        "读取全品种期货市场的诀方向状态：80诀=多头结构、20诀=空头结构；"
        "80诀破诀=原多头被打破转空、20诀破诀=原空头被打破转多。"
        "覆盖 5m/15m/1h/1d/1w/1M，可过滤品种、周期、方向、状态和是否破诀。"
    ),
    params_schema=_PARAMS_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    cost=0,  # 月费订阅制：查询不逐次扣费，usage_logs 仍保留调用审计
    cache_seconds=30,
    risk_note=(
        "诀与破诀是市场结构观察信号，不代表未来涨跌，不应单独作为交易依据；"
        "实际决策需结合更大周期、成交量、仓位、风控与合规要求。"
    ),
    handler=_evaluate,
    tags=["jue", "direction", "long", "short", "broken", "market"],
)
