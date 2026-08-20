"""门信号轮询器。

设计：
- 每 SIGNAL_POLL_SECONDS 秒读一次 gate_events.sqlite3（默认 30 秒）。
- 只在交易时段内读取；空时段配置表示不限制（本地联调）。
- 上游 SQL 查询本身已限定“当天交易日”，所以没有历史门。
- 对同一 event_id 只推送一次；重复扫描只更新 last_seen_at。
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from ..config import GATE_EVENTS_DB, GATE_FREQUENCIES, SIGNAL_POLL_SECONDS, TRADING_SESSIONS
from ..db import mark_signal_notified, upsert_signal_event
from .gate_reader import read_signals
from .session_clock import in_trading_session
from .signal_bus import bus

logger = logging.getLogger("sanyi.signal_poller")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SignalPoller:
    def __init__(
        self,
        path: str = GATE_EVENTS_DB,
        interval_seconds: int = SIGNAL_POLL_SECONDS,
        sessions: str = TRADING_SESSIONS,
        frequencies: tuple = GATE_FREQUENCIES,
    ) -> None:
        self.path = path
        self.interval = max(1, int(interval_seconds))
        self.sessions = sessions
        self.frequencies = tuple(frequencies)

    async def scan_once(self) -> List[Dict[str, Any]]:
        now_iso = _now_iso()
        new_events: List[Dict[str, Any]] = []
        for event in read_signals(self.path, self.frequencies):
            event["first_seen_at"] = now_iso
            is_new = upsert_signal_event(event, is_baseline=False)
            if is_new:
                new_events.append(event)
        for event in new_events:
            bus.publish(event)
            mark_signal_notified(event["event_id"])
        return new_events

    async def run(self) -> None:
        if not self.path:
            logger.info("未配置 SANYI_GATE_EVENTS_DB，信号轮询器只记录不读取")
        while True:
            try:
                now = datetime.now()
                if in_trading_session(now, self.sessions):
                    await self.scan_once()
            except Exception as exc:  # noqa: BLE001 - 轮询器不能因单次坏数据退出
                logger.warning("信号轮询异常：%s", exc)
            await asyncio.sleep(self.interval)
