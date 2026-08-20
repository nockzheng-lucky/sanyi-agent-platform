"""读取生产 green 的 gate_events.sqlite3（只读），筛选当天目标门信号。

与 heatmap“今日门信号”同源：
- 表：gate_event
- 交易日窗口：今天任意时间，或昨天 21:00 之后（夜盘跨日）
- 目标：gate_type=地门，freq∈{5m,15m,1h}，
  - formation 且 门上（无动作门上）
  - first-action 且 first_action=open（地门开）
"""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

FACTOR_KEY = "dimen_gate_signal"


def _parse_event_at(text: str) -> Optional[datetime]:
    if not text:
        return None
    value = str(text).strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y%m%d",
    ):
        try:
            return datetime.strptime(value[:19], fmt)
        except (ValueError, TypeError):
            continue
    return None


def _is_trading_day(event_at: datetime, now: datetime) -> bool:
    if event_at.date() == now.date():
        return True
    yesterday = now - timedelta(days=1)
    return (
        event_at.date() == yesterday.date()
        and event_at.time() >= datetime.strptime("21:00", "%H:%M").time()
    )


def _trading_day_lower(now: datetime) -> datetime:
    lower = now - timedelta(days=1)
    return lower.replace(hour=21, minute=0, second=0, microsecond=0)


def _is_formation_above(payload: Dict[str, Any]) -> bool:
    formation = str(payload.get("formation") or "")
    text = str(payload.get("text") or "")
    return formation == "门上" or "门上" in text


def read_signals(
    path: str,
    frequencies: Tuple[str, ...] = ("5m", "15m", "1h"),
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """读取当天目标门事件。只读打开，不写上游 SQLite。"""
    if not path or not Path(path).exists():
        return []
    current = now or datetime.now()
    lower = _trading_day_lower(current)

    conn = None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT event_seq, event_id, event_kind, event_at, payload_json"
            " FROM gate_event"
            " WHERE event_kind IN (?, ?) AND event_at >= ?"
            " ORDER BY event_at DESC, event_seq DESC",
            ("formation", "first-action", lower.isoformat()),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    events: List[Dict[str, Any]] = []
    for row in rows:
        event_at = _parse_event_at(str(row["event_at"] or ""))
        if event_at is None or not _is_trading_day(event_at, current):
            continue
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("gate_type") != "地门":
            continue
        freq = str(payload.get("freq") or "")
        if freq not in frequencies:
            continue

        kind = str(row["event_kind"] or "")
        if kind == "formation":
            if not _is_formation_above(payload):
                continue
            status = "FORMATION_ABOVE"
        elif kind == "first-action" and payload.get("first_action") == "open":
            status = "OPEN"
        else:
            continue

        symbol = str(payload.get("sym") or "")
        event_time = event_at.isoformat()
        summary = str(payload.get("text") or "") or "%s %s 地门%s" % (
            symbol,
            freq,
            "已开" if status == "OPEN" else "形成·无动作门上",
        )
        events.append(
            {
                "event_id": str(row["event_id"]),
                "factor_key": FACTOR_KEY,
                "symbol": symbol,
                "frequency": freq,
                "status": status,
                "formation": str(payload.get("formation") or ""),
                "gate_price": payload.get("gate_price"),
                "current_price": payload.get("current_price"),
                "open_at": event_time if status == "OPEN" else None,
                "bar_time": event_time,
                "generated_at": event_time,
                "summary": summary,
                "payload_json": json.dumps(payload, ensure_ascii=False, default=str),
            }
        )
    return events
