"""进程内信号总线：poller 发现新事件后，推给所有订阅者（当前是页面 SSE）。"""
import asyncio
from typing import Any, AsyncIterator, Set


class SignalBus:
    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()

    def publish(self, event: Any) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 订阅端太慢时丢事件，避免内存堆积；页面可以再拉 latest 接口补齐。
                pass

    def subscribe(self, maxsize: int = 200) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)


bus = SignalBus()
