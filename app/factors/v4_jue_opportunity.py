"""破诀机会因子：直接消费引擎已经算好的破诀机会，不重算。

期货数据源 = /api/market/heatmap 内嵌的 jue_opportunities；
币圈数据源 = /api/market/jue-opportunities（东京侧部署完成后启用）。

这是“现成信号接入”，不是重新计算：
- 引擎/热力图算好 break_time / formation_walk / break_walk；
- 因子只做筛选、结构化和稳定 eventId。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from ..config import (
    CRYPTO_JUE_OPPORTUNITY_TIMEOUT_SECONDS,
    CRYPTO_JUE_OPPORTUNITY_URL,
    FUTURES_JUE_OPPORTUNITY_TIMEOUT_SECONDS,
    FUTURES_JUE_OPPORTUNITY_URL,
)
from .base import FactorContext, FactorSpec, now_iso

PATTERNS = {
    "walk2_break20": {
        "formationWalk": "走2",
        "thr": 20,
        "label": "成走2·破20",
    },
    "walkB_break80": {
        "formationWalk": "走B",
        "thr": 80,
        "label": "成走B·破80",
    },
}


def _params_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "frequencies": {
                "type": "array",
                "items": {"type": "string", "enum": ["15m", "1h", "1d"]},
                "description": "可选。破诀机会级别，默认 15m / 1h。",
            },
            "patterns": {
                "type": "array",
                "items": {"type": "string", "enum": list(PATTERNS.keys())},
                "description": "可选。破诀机会形态，默认 walk2_break20。",
            },
            "symbols": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选。只返回这些品种。",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "default": 200,
            },
        },
        "additionalProperties": False,
    }


def _output_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "factorKey": {"type": "string"},
            "signal": {"type": "string", "enum": ["MATCH", "NONE"]},
            "score": {"type": "number"},
            "summary": {"type": "string"},
            "generatedAt": {"type": "string"},
            "details": {
                "type": "object",
                "properties": {
                    "updatedAt": {"type": ["string", "null"]},
                    "matchedCells": {"type": "integer"},
                    "returnedCells": {"type": "integer"},
                    "cells": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["updatedAt", "matchedCells", "returnedCells", "cells"],
            },
            "riskNote": {"type": "string"},
        },
        "required": ["factorKey", "signal", "score", "summary", "generatedAt", "details", "riskNote"],
    }


def _norm_set(values: Any) -> set:
    return {str(value).strip() for value in (values or []) if str(value).strip()}


def _extract_opportunities(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        embedded = payload.get("jue_opportunities")
        if isinstance(embedded, dict):
            rows = embedded.get("opportunities")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        rows = payload.get("opportunities")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


async def _evaluate(
    domain: str,
    label: str,
    url: str,
    timeout: float,
    params: Dict[str, Any],
    ctx: FactorContext,
) -> Dict[str, Any]:
    if not url:
        raise HTTPException(status_code=503, detail={"code": 503, "message": "破诀机会数据源未配置", "data": None})
    frequencies = _norm_set(params.get("frequencies")) or {"15m", "1h"}
    pattern_keys = _norm_set(params.get("patterns")) or {"walk2_break20"}
    symbols = {str(value).upper() for value in _norm_set(params.get("symbols"))}
    limit = int(params.get("limit") or 200)

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(10.0, max(3.0, timeout))),
            verify=True,
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail={"code": 502, "message": "破诀机会数据源不可用：%s" % exc.__class__.__name__, "data": None})
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail={"code": 502, "message": "破诀机会数据源返回 HTTP %s" % response.status_code, "data": None})
    try:
        payload = response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail={"code": 502, "message": "破诀机会数据源返回了非 JSON 内容", "data": None})

    rows = _extract_opportunities(payload)
    source_updated_at = None
    if isinstance(payload, dict):
        source_updated_at = payload.get("updated_at")
        embedded = payload.get("jue_opportunities")
        if isinstance(embedded, dict):
            source_updated_at = source_updated_at or embedded.get("updated_at")

    cells: List[Dict[str, Any]] = []
    for row in rows:
        freq = str(row.get("freq") or "")
        if freq not in frequencies:
            continue
        sym = str(row.get("sym") or "").upper()
        if symbols and sym not in symbols:
            continue
        formation_walk = str(row.get("formation_walk") or "")
        thr = row.get("thr")
        matched_patterns = []
        for key in sorted(pattern_keys):
            spec = PATTERNS.get(key)
            if not spec:
                continue
            if formation_walk == spec["formationWalk"] and int(thr or 0) == spec["thr"]:
                matched_patterns.append(key)
        if not matched_patterns:
            continue
        cells.append({
            "factorKey": "%s_jue_opportunity" % domain,
            "symbol": sym,
            "name": str(row.get("name") or sym),
            "sector": row.get("sector"),
            "frequency": freq,
            "patterns": matched_patterns,
            "patternLabels": [PATTERNS[key]["label"] for key in matched_patterns],
            "thr": thr,
            "price": row.get("price"),
            "gap": bool(row.get("gap")),
            "side": row.get("side"),
            "action": row.get("action"),
            "breakText": row.get("break_text"),
            "breakTime": row.get("break_time"),
            "formationWalk": formation_walk,
            "breakWalk": row.get("break_walk"),
            "state": row.get("state"),
            "stateCode": row.get("state_code"),
            "stateDesc": row.get("state_desc"),
            "strong": bool(row.get("strong")),
            "eventId": "jue_opportunity:%s:%s:%s:%s:%s:%s" % (
                domain, sym, freq, thr, row.get("break_time") or "", formation_walk),
        })
        if limit and len(cells) >= limit:
            break

    signal = "MATCH" if cells else "NONE"
    summary = ("命中 %d 个破诀机会。" % len(cells)) if cells else "当前没有符合条件的破诀机会。"
    return {
        "factorKey": "%s_jue_opportunity" % domain,
        "signal": signal,
        "score": min(100, len(cells) * 2),
        "summary": summary,
        "generatedAt": now_iso(),
        "details": {
            "updatedAt": source_updated_at,
            "matchedCells": len(cells),
            "returnedCells": len(cells),
            "cells": cells,
        },
        "riskNote": "破诀机会来自引擎已算好的热力图数据，仅描述当前已破诀状态，不构成投资建议。",
    }


def _make_spec(domain: str, url: str, timeout: float, label: str) -> FactorSpec:
    key = "%s_jue_opportunity" % domain
    return FactorSpec(
        factor_key=key,
        name="%s · 破诀机会" % label,
        description="直接读取引擎已经算好的破诀机会；默认 patterns=walk2_break20 表示“成走2·破20”。不重新计算。",
        params_schema=_params_schema(),
        output_schema=_output_schema(),
        cost=0,
        cache_seconds=15,
        risk_note="破诀机会来自引擎已算好的热力图数据，不构成投资建议。",
        handler=lambda params, ctx: _evaluate(domain, label, url, timeout, params, ctx),
        tags=[domain, "jue", "opportunity"],
        shadow_only=False,
        domain=domain,
        event_based=True,
        kind="basic",
        group="jue",
    )


JUE_OPPORTUNITY_SPECS = [
    _make_spec("futures", FUTURES_JUE_OPPORTUNITY_URL, FUTURES_JUE_OPPORTUNITY_TIMEOUT_SECONDS, "期货"),
]
# 币圈侧部署完成后启用：
# _make_spec("crypto", CRYPTO_JUE_OPPORTUNITY_URL, CRYPTO_JUE_OPPORTUNITY_TIMEOUT_SECONDS, "币圈")
