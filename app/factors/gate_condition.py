"""门条件因子：按门类型 / 生命周期筛选当前门池。

数据源：qh.shhghf.com 的 GET /api/gates?view=all（只读）。
该因子独立超时、独立缓存，数据源异常只影响本因子，不影响平台其他功能。
"""
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
from fastapi import HTTPException

from ..config import GATE_API_TIMEOUT_SECONDS, GATE_API_URL
from .base import FactorContext, FactorSpec, now_iso

FACTOR_KEY = "gate_condition"

DEFAULT_LIVE_STATUSES = ("已开", "开+关", "无动作·门上", "无动作·门下", "已关")
ALL_LIVE_STATUSES = ("已开", "开+关", "无动作·门上", "无动作·门下", "已关", "删除")

MA208_ANCHORS = ("gatePrice", "currentPrice")
MA208_MODES = ("near", "above", "nearOrAbove")

_CACHE: Dict[str, Any] = {"at": 0.0, "payload": None}
_CACHE_TTL_SECONDS = 15.0

_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "symbols": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。只返回这些品种，例如 [\"RB0\", \"AU0\"]。",
        },
        "frequencies": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。周期过滤，例如 [\"15m\", \"1h\"]。",
        },
        "gateTypes": {
            "type": "array",
            "items": {"type": "string", "enum": ["tian", "di"]},
            "description": "可选。门类型：tian=天门，di=地门；默认全部。",
        },
        "liveStatuses": {
            "type": "array",
            "items": {"type": "string", "enum": list(ALL_LIVE_STATUSES)},
            "description": "可选。默认排除“删除”，只返回仍有效的门状态。",
        },
        "includeDeleted": {
            "type": "boolean",
            "description": "可选。true 时包含已删除门。",
        },
        "ma208Anchor": {
            "type": "string",
            "enum": list(MA208_ANCHORS),
            "default": "gatePrice",
            "description": (
                "可选。MA208 比较锚点。gatePrice=门价；currentPrice=现价。"
                "默认 gatePrice（出条件按门价算）。"
            ),
        },
        "ma208Mode": {
            "type": "string",
            "enum": list(MA208_MODES),
            "description": (
                "可选。不传则不启用 MA208 过滤。near=在 MA208 附近（±ma208TolerancePct）；"
                "above=在 MA208 以上；nearOrAbove=附近或以上。"
            ),
        },
        "ma208TolerancePct": {
            "type": "number",
            "minimum": 0,
            "maximum": 50,
            "default": 1.0,
            "description": "可选。“附近”的偏离百分比，默认 1（即 ±1%）。仅 near / nearOrAbove 使用。",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 30,
            "description": "可选。最多返回多少个门，默认 30。",
        },
    },
    "additionalProperties": False,
}

_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "factorKey": {"type": "string"},
        "signal": {"type": "string", "enum": ["OPEN", "FORMING", "CLOSED", "MIXED", "NONE"]},
        "score": {"type": "number"},
        "summary": {"type": "string"},
        "generatedAt": {"type": "string", "format": "date-time"},
        "details": {
            "type": "object",
            "properties": {
                "updatedAt": {"type": ["string", "null"]},
                "matchedGates": {"type": "integer"},
                "returnedGates": {"type": "integer"},
                "counts": {
                    "type": "object",
                    "properties": {
                        "tian": {"type": "integer"},
                        "di": {"type": "integer"},
                        "opened": {"type": "integer"},
                        "forming": {"type": "integer"},
                        "closed": {"type": "integer"},
                    },
                },
                "gates": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["updatedAt", "matchedGates", "returnedGates", "counts", "gates"],
        },
        "riskNote": {"type": "string"},
    },
    "required": ["factorKey", "signal", "summary", "generatedAt", "details", "riskNote"],
}


_GATE_NAIVE_TIME_FIELDS = ("open_at", "close_at", "delete_at", "t1_raw", "bar_close_at", "pullback_time")
_NAIVE_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?$")


def normalize_gate_datetimes(payload: Dict[str, Any], offset: str) -> Dict[str, Any]:
    """给门池里“无时区的本地时间字符串”补上明确时区。

    期货 qh 服务用 Asia/Shanghai 本地时间；币圈东京服务用 UTC。
    前端/推送收到带时区的 ISO 时间后，才能正确换算成北京时间。
    """
    for gate in payload.get("gates") or []:
        if not isinstance(gate, dict):
            continue
        for field in _GATE_NAIVE_TIME_FIELDS:
            value = gate.get(field)
            if isinstance(value, str) and _NAIVE_DATETIME_RE.match(value.strip()):
                time_part = value.strip().replace(" ", "T")
                gate[field] = "%s%s" % (time_part, offset)
    return payload


async def _fetch_gates(client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    if not GATE_API_URL:
        raise HTTPException(status_code=503, detail={"code": 503, "message": "门数据源未配置", "data": None})
    owns_client = client is None
    if owns_client:
        now = time.monotonic()
        if _CACHE["payload"] is not None and now - _CACHE["at"] < _CACHE_TTL_SECONDS:
            return _CACHE["payload"]
        client = httpx.AsyncClient(timeout=httpx.Timeout(GATE_API_TIMEOUT_SECONDS, connect=5.0))
    try:
        try:
            response = await client.get(GATE_API_URL)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": 502, "message": "门数据源不可用：%s" % exc.__class__.__name__, "data": None},
            )
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail={"code": 502, "message": "门数据源返回 HTTP %s" % response.status_code, "data": None},
            )
        try:
            payload = response.json()
        except ValueError:
            raise HTTPException(
                status_code=502,
                detail={"code": 502, "message": "门数据源返回了非 JSON 内容", "data": None},
            )
    finally:
        if owns_client:
            await client.aclose()
    if not isinstance(payload, dict) or not isinstance(payload.get("gates"), list):
        raise HTTPException(status_code=502, detail={"code": 502, "message": "门数据源响应结构不正确", "data": None})
    payload = normalize_gate_datetimes(payload, "+08:00")
    if owns_client:
        _CACHE["at"] = time.monotonic()
        _CACHE["payload"] = payload
    return payload


def _ma208_distance_pct(anchor_price: Any, ma208: Any) -> Optional[float]:
    """返回 (价格 - MA208) / MA208 * 100；数据缺失或非法时返回 None。"""
    try:
        price = float(anchor_price)
        base = float(ma208)
    except (TypeError, ValueError):
        return None
    if not base:
        return None
    distance = (price - base) / base * 100.0
    return round(distance, 4)


def _ma208_matches(gate: Dict[str, Any], anchor: str, mode: str, tolerance_pct: float) -> bool:
    """MA208 位置过滤：near / above / nearOrAbove。"""
    if not mode:
        return True
    if anchor == "currentPrice":
        anchor_price = gate.get("current_price")
    else:
        anchor_price = gate.get("gate_price")
    distance = _ma208_distance_pct(anchor_price, gate.get("ma208"))
    if distance is None:
        return False
    if mode == "near":
        return abs(distance) <= float(tolerance_pct)
    if mode == "above":
        return distance >= 0
    if mode == "nearOrAbove":
        return distance >= -float(tolerance_pct)
    return True


def _gate_to_detail(gate: Dict[str, Any]) -> Dict[str, Any]:
    live_status = gate.get("live_status")
    return {
        "symbol": gate.get("sym"),
        "name": gate.get("name"),
        "contract": gate.get("tscode") or gate.get("actual_contract"),
        "frequency": gate.get("freq"),
        "gateType": gate.get("type"),
        "gatePrice": gate.get("gate_price"),
        "currentPrice": gate.get("current_price"),
        "liveStatus": live_status,
        "formation": gate.get("formation"),
        "openAt": gate.get("open_at"),
        "closeAt": gate.get("close_at"),
        "deleteAt": gate.get("delete_at"),
        "isFirst": bool(gate.get("is_first")),
        "key": gate.get("key"),
        "t1Time": gate.get("t1_str"),
        "t1Raw": gate.get("t1_raw"),
        "t2Time": gate.get("t2_str"),
        "crossTime": gate.get("cross_str"),
        "crossRaw": gate.get("cross_raw") or gate.get("x_anchor_time"),
        "xAnchorTime": gate.get("x_anchor_time"),
        "xAbove": gate.get("x_above"),
        "xCrosses": gate.get("x_crosses"),
        "openEdge": bool(gate.get("open_edge")),
        "closeEdge": bool(gate.get("close_edge")),
        "pullbackConfirmed": bool(gate.get("pullback_confirmed")),
        "ma208": gate.get("ma208"),
        "gateMa208DistancePct": _ma208_distance_pct(gate.get("gate_price"), gate.get("ma208")),
        "currentMa208DistancePct": _ma208_distance_pct(gate.get("current_price"), gate.get("ma208")),
    }


async def _evaluate_with_fetch(
    params: Dict[str, Any],
    ctx: FactorContext,
    fetch_gates: Callable[[], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    symbols = {str(s).strip().upper() for s in (params.get("symbols") or []) if str(s).strip()}
    frequencies = {str(f).strip() for f in (params.get("frequencies") or []) if str(f).strip()}
    gate_types = set(params.get("gateTypes") or ["tian", "di"])
    live_statuses = set(params.get("liveStatuses") or list(DEFAULT_LIVE_STATUSES))
    if params.get("includeDeleted"):
        live_statuses.add("删除")
    limit = int(params.get("limit") or 30)
    ma208_anchor = str(params.get("ma208Anchor") or "gatePrice")
    ma208_mode = str(params.get("ma208Mode") or "")
    ma208_tolerance = float(params.get("ma208TolerancePct") or 1.0)

    payload = await fetch_gates()
    matched: List[Dict[str, Any]] = []
    for gate in payload.get("gates") or []:
        if not isinstance(gate, dict):
            continue
        sym = str(gate.get("sym") or "").upper()
        freq = str(gate.get("freq") or "")
        gtype = str(gate.get("type") or "")
        live = str(gate.get("live_status") or "")
        if symbols and sym not in symbols:
            continue
        if frequencies and freq not in frequencies:
            continue
        if gate_types and gtype not in gate_types:
            continue
        if live_statuses and live not in live_statuses:
            continue
        if ma208_mode and not _ma208_matches(gate, ma208_anchor, ma208_mode, ma208_tolerance):
            continue
        matched.append(_gate_to_detail(gate))

    # 已开 / 形成中的门优先，随后按品种与周期排序，输出稳定。
    status_order = {"已开": 0, "开+关": 1, "无动作·门上": 2, "无动作·门下": 3, "已关": 4, "删除": 5}
    matched.sort(key=lambda g: (status_order.get(g["liveStatus"], 9), g["symbol"], g["frequency"]))
    returned = matched[:limit]

    tian = sum(1 for g in matched if g["gateType"] == "tian")
    di = sum(1 for g in matched if g["gateType"] == "di")
    opened = sum(1 for g in matched if g["liveStatus"] in ("已开", "开+关"))
    forming = sum(1 for g in matched if g["liveStatus"] in ("无动作·门上", "无动作·门下"))
    closed = sum(1 for g in matched if g["liveStatus"] == "已关")

    if not matched:
        signal = "NONE"
        score = 0.0
        summary = "当前筛选范围内没有符合条件的门。"
    else:
        if opened and not forming:
            signal = "OPEN"
        elif forming and not opened:
            signal = "FORMING"
        elif closed and not opened and not forming:
            signal = "CLOSED"
        else:
            signal = "MIXED"
        score = float(min(100, opened * 8 + forming * 4 + closed * 1))
        examples = "；".join(
            "%s %s %s %s" % (g["symbol"], g["frequency"], "天门" if g["gateType"] == "tian" else "地门", g["liveStatus"])
            for g in returned[:5]
        )
        summary = "共 %d 个门：天门 %d / 地门 %d；已开 %d / 形成中 %d / 已关 %d。" % (
            len(matched), tian, di, opened, forming, closed,
        )
        if examples:
            summary += " 示例：%s%s。" % (examples, "…" if len(returned) > 5 else "")

    return {
        "factorKey": FACTOR_KEY,
        "signal": signal,
        "score": score,
        "summary": summary,
        "generatedAt": now_iso(),
        "details": {
            "updatedAt": payload.get("updated") or now_iso(),
            "matchedGates": len(matched),
            "returnedGates": len(returned),
            "counts": {"tian": tian, "di": di, "opened": opened, "forming": forming, "closed": closed},
            "gates": returned,
        },
        "riskNote": "门信号仅用于研究观察，不构成投资建议；门的状态与周期必须结合行情背景理解。",
    }


async def _evaluate(params: Dict[str, Any], ctx: FactorContext) -> Dict[str, Any]:
    return await _evaluate_with_fetch(params, ctx, _fetch_gates)


gate_condition = FactorSpec(
    factor_key=FACTOR_KEY,
    name="门条件（门类型 / 开门 / 关门 / 形成门）",
    description=(
        "读取 qh 全市场门池，可按品种、周期、天门/地门、"
        "已开 / 已关 / 无动作门上 / 无动作门下等生命周期状态过滤，"
        "并支持门价/现价相对 MA208 的位置过滤（附近 / 以上 / 附近或以上）。"
    ),
    params_schema=_PARAMS_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    cost=0,
    cache_seconds=30,
    risk_note="门信号仅用于研究观察，不构成投资建议；门的状态与周期必须结合行情背景理解。",
    handler=_evaluate,
    tags=["gate", "tian", "di", "open", "close", "formation"],
    event_based=False,
)
