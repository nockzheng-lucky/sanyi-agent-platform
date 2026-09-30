"""期货今日门信号因子：只读今天 15:00 周期内新开/新关的门。

与“今日门信号”事件口径对齐：过去开的门不显示、不推送。
数据复用 gate_condition 的 qh 门池读取；门池里 open_at/close_at 是北京时间。
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from .base import FactorContext, FactorSpec, now_iso
from .gate_condition import MA208_ANCHORS, MA208_MODES, _fetch_gates, _ma208_distance_pct, _ma208_matches

FACTOR_KEY = "futures_gate_signal"

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
_ALL_ACTIONS = ("open", "close")

_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "frequencies": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。周期过滤，默认 5m / 15m / 1h。",
        },
        "gateTypes": {
            "type": "array",
            "items": {"type": "string", "enum": ["tian", "di"]},
            "description": "可选。天门 / 地门，默认全部。",
        },
        "actions": {
            "type": "array",
            "items": {"type": "string", "enum": list(_ALL_ACTIONS)},
            "description": "可选。只返回开门(open) / 关门(close)，默认全部。",
        },
        "symbols": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。只返回这些品种，例如 [\"OI0\", \"RB0\"]。",
        },
        "ma208Anchor": {
            "type": "string",
            "enum": list(MA208_ANCHORS),
            "default": "gatePrice",
            "description": "可选。MA208 比较锚点：gatePrice=门价 / currentPrice=现价，默认门价。",
        },
        "ma208Mode": {
            "type": "string",
            "enum": list(MA208_MODES),
            "description": "可选。不传则不启用 MA208 过滤；near=附近 / above=以上 / nearOrAbove=附近或以上。",
        },
        "ma208TolerancePct": {
            "type": "number",
            "minimum": 0,
            "maximum": 50,
            "default": 1.0,
            "description": "可选。“附近”偏离百分比，默认 1（±1%）。仅 near / nearOrAbove 使用。",
        },
        "maxAgeMinutes": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10080,
            "default": 0,
            "description": "可选。0=当前 15:00 周期全部；大于 0 时只看最近 N 分钟。",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 50,
            "description": "可选。最多返回多少条，默认 50。",
        },
    },
    "additionalProperties": False,
}

_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "factorKey": {"type": "string"},
        "signal": {"type": "string", "enum": ["OPEN", "CLOSED", "MIXED", "NONE"]},
        "score": {"type": "number"},
        "summary": {"type": "string"},
        "generatedAt": {"type": "string", "format": "date-time"},
        "details": {
            "type": "object",
            "properties": {
                "updatedAt": {"type": ["string", "null"]},
                "cycleStartAt": {"type": ["string", "null"]},
                "matchedEvents": {"type": "integer"},
                "returnedEvents": {"type": "integer"},
                "events": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["updatedAt", "cycleStartAt", "matchedEvents", "returnedEvents", "events"],
        },
        "riskNote": {"type": "string"},
    },
    "required": ["factorKey", "signal", "summary", "generatedAt", "details", "riskNote"],
}


def _cycle_start(now: Optional[datetime] = None) -> datetime:
    current = now or datetime.now(BEIJING_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BEIJING_TZ)
    current = current.astimezone(BEIJING_TZ)
    start = current.replace(hour=15, minute=0, second=0, microsecond=0)
    if current < start:
        start = start - timedelta(days=1)
    return start


def _parse_event_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BEIJING_TZ)
        return dt.astimezone(BEIJING_TZ)
    except (TypeError, ValueError):
        return None


async def _evaluate(params: Dict[str, Any], ctx: FactorContext) -> Dict[str, Any]:
    frequencies = set(params.get("frequencies") or ["5m", "15m", "1h"])
    gate_types = set(params.get("gateTypes") or ["tian", "di"])
    actions = set(params.get("actions") or list(_ALL_ACTIONS))
    symbols = {str(s).strip().upper() for s in (params.get("symbols") or []) if str(s).strip()}
    max_age = int(params.get("maxAgeMinutes") or 0)
    limit = int(params.get("limit") or 50)
    ma208_anchor = str(params.get("ma208Anchor") or "gatePrice")
    ma208_mode = str(params.get("ma208Mode") or "")
    ma208_tolerance = float(params.get("ma208TolerancePct") or 1.0)

    payload = await _fetch_gates()
    cycle_start = _cycle_start()
    now_bj = datetime.now(BEIJING_TZ)

    events: List[Dict[str, Any]] = []
    for gate in payload.get("gates") or []:
        if not isinstance(gate, dict):
            continue
        sym = str(gate.get("sym") or "").upper()
        freq = str(gate.get("freq") or "")
        gtype = str(gate.get("type") or "")
        if symbols and sym not in symbols:
            continue
        if freq not in frequencies:
            continue
        if gtype not in gate_types:
            continue
        if ma208_mode and not _ma208_matches(gate, ma208_anchor, ma208_mode, ma208_tolerance):
            continue

        for action in _ALL_ACTIONS:
            if action not in actions:
                continue
            event_time = _parse_event_time(gate.get(action + "_at"))
            if event_time is None or event_time < cycle_start:
                continue
            if max_age > 0 and (now_bj - event_time).total_seconds() > max_age * 60:
                continue
            events.append(
                {
                    "eventId": "futures-gate:%s:%s" % (gate.get("key") or "", action),
                    "symbol": sym,
                    "name": gate.get("name"),
                    "contract": gate.get("tscode") or gate.get("actual_contract"),
                    "frequency": freq,
                    "gateType": gtype,
                    "key": gate.get("key"),
                    "status": "OPEN" if action == "open" else "CLOSED",
                    "formation": gate.get("formation"),
                    "gatePrice": gate.get("gate_price"),
                    "currentPrice": gate.get("current_price"),
                    "ma208": gate.get("ma208"),
                    "gateMa208DistancePct": _ma208_distance_pct(gate.get("gate_price"), gate.get("ma208")),
                    "currentMa208DistancePct": _ma208_distance_pct(gate.get("current_price"), gate.get("ma208")),
                    "openAt": gate.get("open_at") if action == "open" else None,
                    "closeAt": gate.get("close_at") if action == "close" else None,
                    "eventAt": event_time.isoformat(),
                    "generatedAt": event_time.isoformat(),
                    "summary": "%s %s %s" % (
                        sym,
                        freq,
                        "天门" if gtype == "tian" else "地门",
                    ),
                    "signalKind": "event",
                }
            )

    events.sort(key=lambda item: item["eventAt"], reverse=True)
    returned = events[:limit]
    open_count = sum(1 for e in events if e["status"] == "OPEN")
    close_count = sum(1 for e in events if e["status"] == "CLOSED")

    if not events:
        signal = "NONE"
        score = 0.0
        summary = "当前 15:00 周期内还没有符合条件的门开/关门事件。"
    elif open_count and close_count:
        signal = "MIXED"
        score = float(min(100, len(events) * 4))
        summary = "当前周期开门 %d 条、关门 %d 条。" % (open_count, close_count)
    elif open_count:
        signal = "OPEN"
        score = float(min(100, open_count * 4))
        summary = "当前周期开门 %d 条。" % open_count
    else:
        signal = "CLOSED"
        score = float(min(100, close_count * 4))
        summary = "当前周期关门 %d 条。" % close_count

    return {
        "factorKey": FACTOR_KEY,
        "signal": signal,
        "score": score,
        "summary": summary,
        "generatedAt": now_iso(),
        "details": {
            "updatedAt": now_iso(),
            "cycleStartAt": cycle_start.isoformat(),
            "matchedEvents": len(events),
            "returnedEvents": len(returned),
            "events": returned,
        },
        "riskNote": "今日门信号仅用于研究观察，不构成投资建议；门的状态必须结合行情背景理解。",
    }


futures_gate_signal = FactorSpec(
    factor_key=FACTOR_KEY,
    name="期货今日门信号（开门/关门）",
    description=(
        "对齐热力图“今日门信号”：只返回当前交易日周期（北京时间每天 15:00 切日）"
        "新开或新关的门，不返回过去已经开过的历史门。支持 5m/15m/1h、天门/地门过滤，"
        "并支持门价/现价相对 MA208 的位置过滤。"
    ),
    params_schema=_PARAMS_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    cost=0,
    cache_seconds=30,
    risk_note="今日门信号仅用于研究观察，不构成投资建议；门的状态必须结合行情背景理解。",
    handler=_evaluate,
    tags=["futures", "gate", "today", "open", "close", "event"],
)
