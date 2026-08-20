"""地门信号因子：地门开 + 地门形成（无动作门上），5m / 15m / 1h。

数据来源：生产 green 的 gate_events.sqlite3（只读），与热力图“今日门信号”同源。
- 地门开           = event_kind=first-action 且 first_action=open
- 地门形成(无动作门上) = event_kind=formation 且形成侧为“门上”
- 级别             = 5m / 15m / 1h
- 只取当天交易日：今天，或昨天 21:00 之后

事件语义：
- “出现即提示”由 app/engine/poller.py 完成：新 event_id 出现即入库并推送页面。
- 本因子 handler 只做“查询最近信号”，供页面 Agent 的工具调用和未来 MCP/REST 使用。
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..db import list_signal_events
from .base import FactorContext, FactorSpec

FACTOR_KEY = "dimen_gate_signal"

_PARAMS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "frequencies": {
            "type": "array",
            "items": {"type": "string", "enum": ["5m", "15m", "1h"]},
            "description": "可选。默认全部级别：5m / 15m / 1h。",
        },
        "symbols": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。只返回这些品种的信号；留空表示全部。",
        },
        "maxAgeMinutes": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10080,
            "default": 0,
            "description": "可选。0=当天全部；大于 0 时只返回最近 N 分钟内的信号。",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "default": 20,
            "description": "可选。最多返回多少条信号；默认 20，控制上下文长度。",
        },
    },
    "additionalProperties": False,
}

_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "factorKey": {"type": "string"},
        "signal": {"type": "string", "description": "LONG（存在做多观察信号）/ NONE"},
        "score": {"type": "number"},
        "summary": {"type": "string"},
        "generatedAt": {"type": "string", "format": "date-time"},
        "details": {
            "type": "object",
            "properties": {
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "eventId": {"type": "string"},
                            "symbol": {"type": "string"},
                            "frequency": {"type": "string"},
                            "status": {"type": "string", "enum": ["OPEN", "FORMATION_ABOVE"]},
                            "formation": {"type": "string"},
                            "gatePrice": {"type": ["number", "null"]},
                            "currentPrice": {"type": ["number", "null"]},
                            "openAt": {"type": ["string", "null"]},
                            "barTime": {"type": ["string", "null"]},
                            "generatedAt": {"type": ["string", "null"]},
                            "summary": {"type": "string"},
                        },
                    },
                }
            },
        },
        "riskNote": {"type": "string"},
    },
    "required": ["factorKey", "signal", "summary", "generatedAt", "details", "riskNote"],
}


def _event_to_detail(row: dict) -> Dict[str, Any]:
    return {
        "eventId": row["event_id"],
        "symbol": row["symbol"],
        "frequency": row["frequency"],
        "status": row["status"],
        "formation": row.get("formation"),
        "gatePrice": row.get("gate_price"),
        "currentPrice": row.get("current_price"),
        "openAt": row.get("open_at"),
        "barTime": row.get("bar_time"),
        "generatedAt": row.get("open_at") or row.get("bar_time"),
        "summary": row.get("summary"),
    }


def _parse_event_time(row: Dict[str, Any]) -> Optional[datetime]:
    """解析门信号真实时间：优先 open_at，其次 bar_time，最后平台发现时间。"""
    now = datetime.now()
    for value in (row.get("open_at"), row.get("bar_time"), row.get("first_seen_at")):
        if not value:
            continue
        text = str(value).strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d %H:%M"):
            try:
                dt = datetime.strptime(text[:19], fmt)
                if fmt == "%m/%d %H:%M":
                    dt = dt.replace(year=now.year)
                    if dt > now + timedelta(days=1):
                        dt = dt.replace(year=now.year - 1)
                return dt
            except (ValueError, TypeError):
                continue
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            return dt
        except (ValueError, TypeError):
            continue
    return None


async def _evaluate(params: Dict[str, Any], ctx: FactorContext) -> Dict[str, Any]:
    frequencies: List[str] = list(params.get("frequencies") or ["5m", "15m", "1h"])
    symbols: List[str] = list(params.get("symbols") or [])
    max_age = int(params.get("maxAgeMinutes") or 0)
    limit = int(params.get("limit") or 20)

    # 先多取一些，按门信号真实时间过滤/排序后再截断。
    rows = list_signal_events(
        frequencies=frequencies,
        symbols=symbols,
        limit=500,
        include_baseline=True,
    )
    now = datetime.now()
    scoped = []
    for row in rows:
        event_time = _parse_event_time(row)
        if event_time is None:
            continue
        if max_age > 0 and (now - event_time).total_seconds() > max_age * 60:
            continue
        scoped.append((event_time, row))
    scoped.sort(key=lambda item: item[0], reverse=True)
    scoped = scoped[:limit]

    events = [_event_to_detail(r) for _, r in scoped]
    open_count = sum(1 for e in events if e["status"] == "OPEN")
    signal = "LONG" if open_count > 0 else "NONE"

    if not events:
        if max_age > 0:
            summary = "最近 %d 分钟内没有地门开/地门形成（无动作门上）信号。" % max_age
        else:
            summary = "今天暂时没有新的地门开/地门形成（无动作门上）信号。"
    else:
        pieces = []
        for e in events[:8]:
            label = "已开" if e["status"] == "OPEN" else "形成·无动作门上"
            pieces.append("%s %s %s" % (e["symbol"], e["frequency"], label))
        summary = "按信号时间返回最近 %d 条：%s%s" % (
            len(events),
            "；".join(pieces),
            "…" if len(events) > 8 else "",
        )

    return {
        "factorKey": FACTOR_KEY,
        "signal": signal,
        "score": float(min(100, len(events) * 10)),
        "summary": summary,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "details": {"events": events},
        "riskNote": "门信号仅用于研究观察，不构成投资建议；信号时间必须结合行情背景理解。",
    }


dimen_gate_signal = FactorSpec(
    factor_key=FACTOR_KEY,
    name="地门信号（地门开 / 地门形成·无动作门上，5m/15m/1h）",
    description=(
        "读取三易引擎当前门信号：type=地门，级别 5m/15m/1h，"
        "live_status 为 已开 或 无动作·门上。"
        "事件由平台轮询器发现后实时推送到页面；本接口用于 Agent 查询最近信号。"
    ),
    params_schema=_PARAMS_SCHEMA,
    output_schema=_OUTPUT_SCHEMA,
    cost=0,  # 月费订阅制：查询/推送不逐次扣费，usage_logs 仍保留调用审计
    cache_seconds=0,
    risk_note=(
        "该因子是技术结构观察信号，不代表未来涨跌，不应单独作为交易依据；"
        "实际决策需结合流动性、仓位、风控与合规要求。"
    ),
    handler=_evaluate,
    tags=["dimen_gate", "5m", "15m", "1h", "event", "long"],
)
