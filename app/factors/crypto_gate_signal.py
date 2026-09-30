"""币圈今日门信号因子（影子模式专用）。

只读“事件流”，不读全部门池快照。
数据源：东京币圈服务器 GET /api/events?scope=today（与热力图“今日门信号”同一管道）。

事件四态：
- open  -> OPEN（开门）
- close -> CLOSED（关门）
- new + 文本含“门上” -> FORMATION_ABOVE（形成门·无动作门上）
- new + 文本含“门下” -> FORMATION_BELOW（形成门·无动作门下）

注意：type=new 的事件没有 formation 字段，形成侧只能从 text 解析；
门池里的 formation 是“当前”状态，可能已经和事件发生时不一致，不能回退使用。
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException

from ..config import (
    CRYPTO_EVENTS_API_URL,
    CRYPTO_EVENTS_CONNECT_TIMEOUT_SECONDS,
    CRYPTO_EVENTS_TIMEOUT_SECONDS,
    CRYPTO_VERIFY_SSL,
)
from .base import FactorContext, FactorSpec, now_iso
from .crypto_gate_condition import _fetch_gates
from .gate_condition import MA208_ANCHORS, MA208_MODES, _ma208_distance_pct, _ma208_matches

FACTOR_KEY = "crypto_gate_signal"

ALL_FREQUENCIES = ("5m", "15m", "1h", "1d", "1w", "1M")
BEIJING_TZ = ZoneInfo("Asia/Shanghai")

# eventTypes 到规范 status 的映射。formation 是兼容旧订阅的分组值；
# formationAbove / formationBelow 用于新订阅的精确单侧过滤。
_EVENT_TYPE_STATUSES = {
    "open": frozenset(("OPEN",)),
    "close": frozenset(("CLOSED",)),
    "formation": frozenset(("FORMATION_ABOVE", "FORMATION_BELOW")),
    "formationAbove": frozenset(("FORMATION_ABOVE",)),
    "formationBelow": frozenset(("FORMATION_BELOW",)),
}
_DEFAULT_EVENT_TYPES = ("open", "close", "formation")

_CACHE: Dict[str, Any] = {"at": 0.0, "payload": None}
_CACHE_TTL_SECONDS = 15.0

_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "frequencies": {
            "type": "array",
            "items": {"type": "string", "enum": list(ALL_FREQUENCIES)},
            "description": "可选。默认只返回 5m / 15m / 1h。",
        },
        "eventTypes": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["open", "close", "formation", "formationAbove", "formationBelow"],
            },
            "description": (
                "可选。open=开门；close=关门；formation=形成门（门上+门下）；"
                "formationAbove=形成门·无动作门上；formationBelow=形成门·无动作门下。"
                "默认返回 open / close / formation。"
            ),
        },
        "gateTypes": {
            "type": "array",
            "items": {"type": "string", "enum": ["tian", "di"]},
            "description": "可选。天门 / 地门；默认全部。",
        },
        "symbols": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。只返回这些币种，例如 [\"BTCUSDT\", \"ETHUSDT\"]。",
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
            "description": "可选。0=当日全部；大于 0 时只返回最近 N 分钟内的门事件。",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 30,
            "description": "可选。最多返回多少条，默认 30。",
        },
    },
    "additionalProperties": False,
}

_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "factorKey": {"type": "string"},
        "signal": {"type": "string", "enum": ["OPEN", "CLOSED", "FORMING", "MIXED", "NONE"]},
        "score": {"type": "number"},
        "summary": {"type": "string"},
        "generatedAt": {"type": "string", "format": "date-time"},
        "details": {
            "type": "object",
            "properties": {
                "updatedAt": {"type": ["string", "null"]},
                "matchedEvents": {"type": "integer"},
                "returnedEvents": {"type": "integer"},
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "eventId": {"type": "string"},
                            "symbol": {"type": "string"},
                            "frequency": {"type": "string"},
                            "gateType": {"type": ["string", "null"]},
                            "status": {
                                "type": "string",
                                "enum": ["OPEN", "CLOSED", "FORMATION_ABOVE", "FORMATION_BELOW"],
                            },
                            "formation": {"type": ["string", "null"]},
                            "openAt": {"type": ["string", "null"]},
                            "closeAt": {"type": ["string", "null"]},
                            "eventAt": {"type": ["string", "null"]},
                        },
                    },
                },
            },
            "required": ["updatedAt", "matchedEvents", "returnedEvents", "events"],
        },
        "riskNote": {"type": "string"},
    },
    "required": ["factorKey", "signal", "summary", "generatedAt", "details", "riskNote"],
}


def _parse_clock_date(date: str, clock: str, tzinfo: Any) -> Optional[datetime]:
    text = "%s %s" % (date or "", clock or "")
    now_utc = datetime.now(timezone.utc)
    for fmt in ("%m/%d %H:%M:%S", "%m/%d %H:%M"):
        try:
            parsed = datetime.strptime(text.strip(), fmt)
        except (TypeError, ValueError):
            continue
        parsed = parsed.replace(year=now_utc.year, tzinfo=tzinfo)
        if parsed > now_utc + timedelta(days=1):
            parsed = parsed.replace(year=now_utc.year - 1)
        return parsed
    return None


def _event_utc_datetime(date: str, event_time: str) -> Optional[datetime]:
    """东京 UTC 服务器生成的 date/time，按 UTC 解析。"""
    return _parse_clock_date(date, event_time, timezone.utc)


def _event_beijing_iso(event: Dict[str, Any]) -> Optional[str]:
    """把事件时间统一成北京时间 ISO。

    scope=today 的币圈 sidebar pipeline 已经返回北京时间 date/time（timezone=CST，
    同时带 utc_date/utc_time）；老数据没有这些字段，则按东京 UTC 解析再转换。
    """
    if event.get("timezone") == "CST":
        parsed = _parse_clock_date(str(event.get("date") or ""), str(event.get("time") or ""), BEIJING_TZ)
        if parsed is not None:
            return parsed.isoformat()
    if event.get("utc_date") or event.get("utc_time"):
        parsed = _event_utc_datetime(str(event.get("utc_date") or ""), str(event.get("utc_time") or ""))
        if parsed is not None:
            return parsed.astimezone(BEIJING_TZ).isoformat()
    parsed = _event_utc_datetime(str(event.get("date") or ""), str(event.get("time") or ""))
    return parsed.astimezone(BEIJING_TZ).isoformat() if parsed else None


def _parse_text_gate_type(text: str) -> Optional[str]:
    text = str(text or "")
    if "天门" in text:
        return "tian"
    if "地门" in text:
        return "di"
    return None


def _event_name(text: str, symbol: str) -> str:
    text = str(text or "")
    freq_index = min(
        [index for index in (text.find(freq) for freq in ALL_FREQUENCIES) if index >= 0] or [-1]
    )
    prefix = text[:freq_index].strip() if freq_index > 0 else ""
    return prefix or symbol


def _desired_statuses(event_types: set) -> set:
    statuses = set()
    for event_type in event_types:
        statuses.update(_EVENT_TYPE_STATUSES.get(str(event_type), ()))
    return statuses


async def _evaluate(params: Dict[str, Any], ctx: FactorContext) -> Dict[str, Any]:
    if not CRYPTO_EVENTS_API_URL:
        raise HTTPException(
            status_code=503,
            detail={"code": 503, "message": "币圈门事件源未配置，请设置 SANYI_CRYPTO_EVENTS_API_URL", "data": None},
        )
    frequencies = set(params.get("frequencies") or ["5m", "15m", "1h"])
    event_types = set(params.get("eventTypes") or list(_DEFAULT_EVENT_TYPES))
    desired_statuses = _desired_statuses(event_types)
    if not desired_statuses:
        desired_statuses = _desired_statuses(set(_DEFAULT_EVENT_TYPES))
    gate_types = set(params.get("gateTypes") or ["tian", "di"])
    symbols = {str(s).strip().upper() for s in (params.get("symbols") or []) if str(s).strip()}
    max_age = int(params.get("maxAgeMinutes") or 0)
    limit = int(params.get("limit") or 30)
    ma208_anchor = str(params.get("ma208Anchor") or "gatePrice")
    ma208_mode = str(params.get("ma208Mode") or "")
    ma208_tolerance = float(params.get("ma208TolerancePct") or 1.0)

    now_mono = time.monotonic()
    if _CACHE["payload"] is None or now_mono - _CACHE["at"] >= _CACHE_TTL_SECONDS:
        payload = None
        last_error: Optional[str] = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        CRYPTO_EVENTS_TIMEOUT_SECONDS,
                        connect=min(CRYPTO_EVENTS_CONNECT_TIMEOUT_SECONDS, CRYPTO_EVENTS_TIMEOUT_SECONDS),
                    ),
                    verify=CRYPTO_VERIFY_SSL,
                ) as client:
                    response = await client.get(CRYPTO_EVENTS_API_URL)
                if response.status_code != 200:
                    raise HTTPException(
                        status_code=502,
                        detail={"code": 502, "message": "币圈门事件源返回 HTTP %s" % response.status_code, "data": None},
                    )
                try:
                    payload = response.json()
                except ValueError:
                    raise HTTPException(
                        status_code=502,
                        detail={"code": 502, "message": "币圈门事件源返回了非 JSON 内容", "data": None},
                    )
                break
            except httpx.ConnectTimeout as exc:
                last_error = exc.__class__.__name__
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                break
            except httpx.HTTPError as exc:
                last_error = exc.__class__.__name__
                break
        if payload is None:
            raise HTTPException(
                status_code=502,
                detail={"code": 502, "message": "币圈门事件源不可用：%s" % (last_error or "Unknown"), "data": None},
            )
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise HTTPException(
                status_code=502,
                detail={"code": 502, "message": "币圈门事件源响应结构不正确", "data": None},
            )
        _CACHE["at"] = time.monotonic()
        _CACHE["payload"] = payload
    else:
        payload = _CACHE["payload"]

    gate_pool: Dict[str, Dict[str, Any]] = {}
    try:
        gates_payload = await _fetch_gates()
        for gate in gates_payload.get("gates") or []:
            if isinstance(gate, dict) and gate.get("key"):
                gate_pool[str(gate["key"])] = gate
    except Exception:  # noqa: BLE001 - 门池只用于补充价格/名称
        pass

    now_beijing = datetime.now(BEIJING_TZ)
    today_str = now_beijing.strftime("%m/%d")
    yesterday = now_beijing - timedelta(days=1)
    yesterday_str = yesterday.strftime("%m/%d")

    matched: List[Dict[str, Any]] = []
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        text_preview = str(event.get("text") or "")
        if event_type == "open":
            if str(event.get("edge") or "") != "open" and not bool(event.get("first_action")):
                continue
            status = "OPEN"
        elif event_type == "close":
            if str(event.get("edge") or "") != "close" and not bool(event.get("first_action")):
                continue
            status = "CLOSED"
        elif event_type == "new":
            if "门下" in text_preview:
                status = "FORMATION_BELOW"
            elif "门上" in text_preview:
                status = "FORMATION_ABOVE"
            else:
                continue
        else:
            continue
        if status not in desired_statuses:
            continue
        freq = str(event.get("freq") or "")
        if freq not in frequencies:
            continue
        symbol = str(event.get("sym") or "").upper()
        if symbols and symbol not in symbols:
            continue

        text = str(event.get("text") or "")
        gate_type = _parse_text_gate_type(text) or _parse_text_gate_type(str(event.get("formation") or ""))
        if gate_type and gate_type not in gate_types:
            continue

        event_date = str(event.get("date") or "")
        event_time = str(event.get("time") or "")
        if event_date not in (today_str, yesterday_str):
            continue
        if event_date == yesterday_str and event_time < "21:00":
            continue
        beijing_at = _event_beijing_iso(event)
        if beijing_at is None:
            continue
        if max_age > 0:
            try:
                dt = datetime.fromisoformat(beijing_at)
                if (now_beijing - dt).total_seconds() > max_age * 60:
                    continue
            except ValueError:
                continue

        key = str(event.get("key") or "")
        gate = gate_pool.get(key) or {}
        if ma208_mode and not _ma208_matches(gate, ma208_anchor, ma208_mode, ma208_tolerance):
            continue
        if gate_type is None:
            gate_type = gate.get("type")
        name = gate.get("name") or _event_name(text, symbol)
        summary = text or "%s %s 门信号" % (symbol, freq)
        if event_type == "new":
            # 事件发生时形成的侧只能来自事件 text；门池 formation 是当前状态，
            # 门随后开/关后可能已翻转，不能回退使用，否则 status 与 formation 自相矛盾。
            formation = "门上" if status == "FORMATION_ABOVE" else "门下"
        else:
            formation = event.get("formation") or gate.get("formation")
        # eventId 只用 key + 语义族，不带展示时间：
        # - 形成门(new)与开门(open)属于同一个入场观察信号，同一 key 只推一次；
        # - 关门(close)是另一个语义族，保留独立 eventId。
        event_suffix = "close" if event_type == "close" else "signal"
        matched.append(
            {
                "eventId": "crypto-gate:%s:%s" % (key, event_suffix),
                "symbol": symbol,
                "name": name,
                "contract": gate.get("contract"),
                "frequency": freq,
                "gateType": gate_type or gate.get("type"),
                "status": status,
                "formation": formation,
                "gatePrice": gate.get("gate_price") if gate else None,
                "currentPrice": gate.get("current_price") if gate else None,
                "ma208": gate.get("ma208") if gate else None,
                "gateMa208DistancePct": _ma208_distance_pct(gate.get("gate_price"), gate.get("ma208")) if gate else None,
                "currentMa208DistancePct": _ma208_distance_pct(gate.get("current_price"), gate.get("ma208")) if gate else None,
                "openAt": beijing_at if status == "OPEN" else None,
                "closeAt": beijing_at if status == "CLOSED" else None,
                "eventAt": beijing_at,
                "barTime": beijing_at,
                "generatedAt": beijing_at,
                "key": key,
                "summary": summary,
                "signalKind": "event",
            }
        )

    # 同一 key 的“形成门/开门”只保留一条，关门作为独立语义保留。
    # 产品规则：门上形成门的第一动作是关门，开门事件不应压过门上形成；
    # 门下形成门的第一动作才是开门。因此优先级：门上形成 > 开门 > 门下形成。
    signal_rank = {"FORMATION_ABOVE": 0, "OPEN": 1, "FORMATION_BELOW": 2}
    signal_by_key: Dict[str, Dict[str, Any]] = {}
    close_by_key: Dict[str, Dict[str, Any]] = {}
    for event in matched:
        event_key = str(event.get("key") or "")
        if event["status"] == "CLOSED":
            close_by_key[event_key] = event
            continue
        current = signal_by_key.get(event_key)
        if current is None or signal_rank.get(event["status"], 9) < signal_rank.get(current["status"], 9):
            signal_by_key[event_key] = event
    matched = list(signal_by_key.values()) + list(close_by_key.values())

    matched.sort(key=lambda item: item["eventAt"] or "", reverse=True)
    returned = matched[:limit]
    open_count = sum(1 for e in matched if e["status"] == "OPEN")
    close_count = sum(1 for e in matched if e["status"] == "CLOSED")
    formation_above_count = sum(1 for e in matched if e["status"] == "FORMATION_ABOVE")
    formation_below_count = sum(1 for e in matched if e["status"] == "FORMATION_BELOW")
    forming_count = formation_above_count + formation_below_count

    if not matched:
        signal = "NONE"
        score = 0.0
        summary = "今天币圈暂时没有新的门事件（5m/15m/1h）。"
    else:
        kinds = sum(1 for count in (open_count, close_count, forming_count) if count > 0)
        if kinds > 1:
            signal = "MIXED"
        elif forming_count:
            signal = "FORMING"
        elif open_count:
            signal = "OPEN"
        else:
            signal = "CLOSED"
        score = float(min(100, open_count * 10 + close_count * 6 + forming_count * 6))
        summary = (
            "今天币圈门事件共 %d 条：开门 %d 条、关门 %d 条、"
            "形成门上 %d 条、形成门下 %d 条。"
            % (len(matched), open_count, close_count, formation_above_count, formation_below_count)
        )

    return {
        "factorKey": FACTOR_KEY,
        "signal": signal,
        "score": score,
        "summary": summary,
        "generatedAt": now_iso(),
        "details": {
            "updatedAt": now_iso(),
            "matchedEvents": len(matched),
            "returnedEvents": len(returned),
            "events": returned,
        },
        "riskNote": "币圈门事件仅用于研究观察，不构成投资建议；加密货币波动极大，需严格控制风险。",
    }


crypto_gate_signal = FactorSpec(
    factor_key=FACTOR_KEY,
    name="币圈今日门信号（影子模式）",
    description=(
        "对齐期货门信号口径：只读东京币圈服务器当天事件流，"
        "支持四态过滤：open=开门、close=关门、formationAbove=形成门·无动作门上、"
        "formationBelow=形成门·无动作门下；formation=形成门（两侧都含）。"
        "不是历史门池快照。支持 5m/15m/1h、天门/地门过滤，"
        "并支持门价/现价相对 MA208 的位置过滤。"
    ),
    params_schema=_PARAMS_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    cost=0,
    cache_seconds=15,
    risk_note="币圈今日门信号仅用于研究观察，不构成投资建议；加密货币波动极大，需严格控制风险。",
    handler=_evaluate,
    tags=["crypto", "gate", "open", "close", "formation", "today", "event", "shadow"],
    shadow_only=True,
    domain="crypto",
)
