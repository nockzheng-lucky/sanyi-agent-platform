"""门信号轮询器。

设计：
- 每 SIGNAL_POLL_SECONDS 秒读一次 gate_registry.json（默认 30 秒）。
- 只在交易时段内读取；空时段配置表示不限制（本地联调）。
- 第一次扫描只做 baseline，不推送历史门，避免服务重启后刷屏。
- 之后只对“新出现”的 event_id 推送；重复出现只更新 last_seen_at。
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..config import (
    GATE_FREQUENCIES,
    GATE_REGISTRY_FILE,
    SIGNAL_POLL_SECONDS,
    TRADING_SESSIONS,
)
from ..db import latest_signal_id, mark_signal_notified, upsert_signal_event
from .gate_reader import read_signals
from .session_clock import in_trading_session
from .signal_bus import bus

logger = logging.getLogger("sanyi.signal_poller")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SignalPoller:
    def __init__(
        self,
        path: str = GATE_REGISTRY_FILE,
        interval_seconds: int = SIGNAL_POLL_SECONDS,
        sessions: str = TRADING_SESSIONS,
        frequencies: tuple = GATE_FREQUENCIES,
    ) -> None:
        self.path = path
        self.interval = max(1, int(interval_seconds))
        self.sessions = sessions
        self.frequencies = tuple(frequencies)
        self.baseline_done = False

    async def scan_once(self) -> List[Dict[str, Any]]:
        now_iso = _now_iso()
        first_run = not self.baseline_done
        # 只有“数据库还是空的第一次启动”才把当前存量全部记为 baseline；
        # 之后重启时，库里已有历史，新出现的 event_id 仍然会正常推送。
        baseline_run = first_run and latest_signal_id() == 0
        new_events: List[Dict[str, Any]] = []
        for event in read_signals(self.path, self.frequencies):
            event["first_seen_at"] = now_iso
            is_new = upsert_signal_event(event, is_baseline=baseline_run)
            if is_new and not baseline_run:
                new_events.append(event)
        self.baseline_done = True
        for event in new_events:
            bus.publish(event)
            mark_signal_notified(event["event_id"])
        return new_events

    async def run(self) -> None:
        if not self.path:
            logger.info("未配置 SANYI_GATE_REGISTRY_FILE，信号轮询器只记录不读取")
        while True:
            try:
                now = datetime.now()
                if in_trading_session(now, self.sessions):
                    if not self.baseline_done:
                        logger.info("信号轮询器首次 baseline 扫描：%s", self.path)
                    await self.scan_once()
            except Exception as exc:  # noqa: BLE001 - 轮询器不能因单次坏数据退出
                logger.warning("信号轮询异常：%s", exc)
            await asyncio.sleep(self.interval)
