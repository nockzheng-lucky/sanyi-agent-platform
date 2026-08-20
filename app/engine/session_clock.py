"""交易时段判断。

配置格式：HH:MM-HH:MM，逗号分隔。
支持跨零点夜盘段：21:00-02:30 表示 21:00~24:00 和 00:00~02:30。
"""
from datetime import datetime, time
from typing import List, Tuple


def parse_sessions(raw: str) -> List[Tuple[time, time]]:
    sessions: List[Tuple[time, time]] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or "-" not in part:
            continue
        start_s, end_s = part.split("-", 1)
        try:
            h1, m1 = start_s.strip().split(":")
            h2, m2 = end_s.strip().split(":")
            sessions.append((time(int(h1), int(m1)), time(int(h2), int(m2))))
        except (ValueError, TypeError):
            continue
    return sessions


def in_trading_session(now: datetime, raw: str) -> bool:
    """空配置表示不限制时段（仅用于本地联调）。"""
    sessions = parse_sessions(raw)
    if not sessions:
        return True
    t = now.time()
    for start, end in sessions:
        if end <= start:
            # 跨零点：例如 21:00-02:30
            if t >= start or t < end:
                return True
        elif start <= t < end:
            return True
    return False
