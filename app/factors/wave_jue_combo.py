"""走法 × 破诀组合因子（当前快照版）。

注意：这是“当前最新快照”的组合，不区分破诀与走法发生的先后顺序。
精确时序版本（破诀时仍处于某走法）待 /api/factors/{sym}/{freq} 上线后升级。

数据复用 jue_direction 的短缓存，不额外增加 qh 数据源压力。
"""
from typing import Any, Dict, List, Optional

from .base import FactorContext, FactorSpec, now_iso
from .jue_direction import _cell_to_detail, _fetch_raw

FACTOR_KEY = "wave_jue_combo"

COMBOS: Dict[str, Dict[str, str]] = {
    "walk2_break20": {"state": "20诀破诀", "walkCode": "2", "label": "走2·破20诀"},
    "walkB_break80": {"state": "80诀破诀", "walkCode": "B", "label": "走B·破80诀"},
    "walkC_break20": {"state": "20诀破诀", "walkCode": "C", "label": "走C·破20诀"},
}

_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "combos": {
            "type": "array",
            "items": {"type": "string", "enum": list(COMBOS.keys())},
            "description": "可选。默认全部：walk2_break20 / walkB_break80 / walkC_break20。",
        },
        "frequencies": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。周期过滤，例如 [\"15m\", \"1h\"]。",
        },
        "symbols": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。品种过滤，例如 [\"RB0\", \"AU0\"]。",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 30,
            "description": "可选。最多返回多少条组合信号。",
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
                "counts": {"type": "object"},
                "cells": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["updatedAt", "matchedCells", "returnedCells", "counts", "cells"],
        },
        "riskNote": {"type": "string"},
    },
    "required": ["factorKey", "signal", "summary", "generatedAt", "details", "riskNote"],
}


async def _evaluate(params: Dict[str, Any], ctx: FactorContext) -> Dict[str, Any]:
    combos = set(params.get("combos") or list(COMBOS.keys()))
    frequencies = {str(f).strip() for f in (params.get("frequencies") or []) if str(f).strip()}
    symbols = {str(s).strip().upper() for s in (params.get("symbols") or []) if str(s).strip()}
    limit = int(params.get("limit") or 30)

    payload = await _fetch_raw()
    source_updated_at = payload.get("updated_at") or now_iso()
    matched: List[Dict[str, Any]] = []
    for sector in payload.get("sectors") or []:
        if not isinstance(sector, dict):
            continue
        for item in sector.get("items") or []:
            if not isinstance(item, dict):
                continue
            sym = str(item.get("sym") or "").upper()
            if symbols and sym not in symbols:
                continue
            for cell in item.get("cells") or []:
                if not isinstance(cell, dict):
                    continue
                freq = str(cell.get("freq") or "")
                if frequencies and freq not in frequencies:
                    continue
                state = str(cell.get("state") or "")
                walk_code = str(cell.get("walk_code") or "")
                for combo_key, spec in COMBOS.items():
                    if combo_key not in combos:
                        continue
                    if state == spec["state"] and walk_code == spec["walkCode"]:
                        detail = _cell_to_detail(sector, item, cell, source_updated_at)
                        detail["comboKey"] = combo_key
                        detail["comboLabel"] = spec["label"]
                        matched.append(detail)

    # 破20诀方向为多、破80诀方向为空；按组合分组并保持稳定顺序。
    combo_order = {key: idx for idx, key in enumerate(COMBOS)}
    matched.sort(key=lambda c: (combo_order.get(c["comboKey"], 99), c["symbol"], c["frequency"]))
    returned = matched[:limit]

    counts: Dict[str, int] = {}
    for key in COMBOS:
        counts[key] = sum(1 for c in matched if c["comboKey"] == key)

    long_count = sum(1 for c in matched if c["direction"] == "long")
    short_count = sum(1 for c in matched if c["direction"] == "short")

    if not matched:
        signal = "NONE"
        score = 0.0
        summary = "当前没有符合条件的走法 × 破诀组合信号。"
    else:
        if long_count > short_count:
            signal = "LONG"
        elif short_count > long_count:
            signal = "SHORT"
        else:
            signal = "MIXED"
        score = float(min(100, len(matched) * 4))
        parts = ["%s %d 条" % (COMBOS[k]["label"], counts[k]) for k in COMBOS if counts.get(k)]
        examples = "；".join(
            "%s %s %s" % (c["symbol"], c["frequency"], c["comboLabel"]) for c in returned[:5]
        )
        summary = "当前组合信号共 %d 条：%s。" % (len(matched), "、".join(parts))
        if examples:
            summary += " 示例：%s%s。" % (examples, "…" if len(returned) > 5 else "")

    return {
        "factorKey": FACTOR_KEY,
        "signal": signal,
        "score": score,
        "summary": summary,
        "generatedAt": source_updated_at,
        "details": {
            "updatedAt": source_updated_at,
            "matchedCells": len(matched),
            "returnedCells": len(returned),
            "counts": counts,
            "cells": returned,
        },
        "riskNote": "组合信号为当前快照口径，不区分破诀与走法发生先后；仅用于研究观察，不构成投资建议。",
    }


wave_jue_combo = FactorSpec(
    factor_key=FACTOR_KEY,
    name="走法×破诀组合（走2破20 / 走B破80 / 走C破20）",
    description=(
        "当前快照版组合：走2·破20诀、走B·破80诀、走C·破20诀。"
        "注意：当前数据源只给最新状态，无法区分破诀和走法的先后顺序；"
        "精确时序版待期货因子 API 上线后升级。"
    ),
    params_schema=_PARAMS_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    cost=0,
    cache_seconds=30,
    risk_note="组合信号为当前快照口径，不区分破诀与走法发生先后；仅用于研究观察，不构成投资建议。",
    handler=_evaluate,
    tags=["wave", "jue", "combo", "walk2", "walkB", "walkC"],
)
