"""读取现有 sanyi 引擎写出的 gate_registry.json，筛选目标门信号。

只读，不写上游文件；路径通过 SANYI_GATE_REGISTRY_FILE 配置。
"""
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

STATUS_MAP = {
    "已开": "OPEN",
    "无动作·门上": "FORMATION_ABOVE",
}

FACTOR_KEY = "dimen_gate_signal"


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _parse_registry(data: Any) -> Dict[str, Any]:
    gates: Dict[str, Any] = {}
    if isinstance(data, dict):
        raw_gates = data.get("gates", data)
        if isinstance(raw_gates, dict):
            gates = raw_gates
        elif isinstance(raw_gates, list):
            for item in raw_gates:
                if isinstance(item, dict) and item.get("key"):
                    gates[str(item["key"])] = item
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("key"):
                gates[str(item["key"])] = item
    return gates


def read_signals(
    path: str,
    frequencies: Tuple[str, ...] = ("5m", "15m", "1h"),
    symbol_filter: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """读取并归一化门信号。只返回新出现的候选事件，不负责去重。"""
    if not path or not Path(path).exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    events: List[Dict[str, Any]] = []
    for key, gate in _parse_registry(data).items():
        if not isinstance(gate, dict):
            continue
        if gate.get("type") != "di":
            continue
        freq = str(gate.get("freq") or "")
        if freq not in frequencies:
            continue
        status_raw = str(gate.get("live_status") or "")
        status = STATUS_MAP.get(status_raw)
        if status is None:
            continue
        symbol = str(gate.get("sym") or "")
        if symbol_filter and symbol not in symbol_filter:
            continue

        open_at = str(gate.get("open_at") or "") or None
        bar_time = str(gate.get("t2_time") or gate.get("t2_str") or "") or None
        generated_at = open_at or bar_time
        stable = "|".join([str(gate.get("key") or key), status_raw])
        event_id = _sha1(stable)

        summary = "%s %s 地门%s" % (
            gate.get("name") or symbol,
            freq,
            "已开（做多观察）" if status == "OPEN" else "形成·无动作门上",
        )
        events.append(
            {
                "event_id": event_id,
                "factor_key": FACTOR_KEY,
                "symbol": symbol,
                "frequency": freq,
                "status": status,
                "formation": str(gate.get("formation") or ""),
                "gate_price": gate.get("gate_price"),
                "current_price": gate.get("current_price"),
                "open_at": open_at,
                "bar_time": bar_time,
                "generated_at": generated_at,
                "summary": summary,
                "payload_json": json.dumps(gate, ensure_ascii=False, default=str),
            }
        )
    return events
