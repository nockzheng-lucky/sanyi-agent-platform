import asyncio
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from ..db import list_signal_events
from ..engine.signal_bus import bus
from ..security import get_token_or_session

router = APIRouter(prefix="/api/v1/signal-events", tags=["signals"])


def _detail(row: dict) -> dict:
    return {
        "id": row["id"],
        "eventId": row["event_id"],
        "factorKey": row["factor_key"],
        "symbol": row["symbol"],
        "contract": row.get("contract"),
        "sourceKind": row.get("source_kind"),
        "frequency": row["frequency"],
        "status": row["status"],
        "formation": row.get("formation"),
        "gatePrice": row.get("gate_price"),
        "currentPrice": row.get("current_price"),
        "openAt": row.get("open_at"),
        "barTime": row.get("bar_time"),
        "generatedAt": row.get("open_at") or row.get("bar_time"),
        "summary": row.get("summary"),
        "firstSeenAt": row.get("first_seen_at"),
    }


@router.get("/latest")
async def latest(
    request: Request,
    frequencies: Optional[str] = Query(default=None),
    symbols: Optional[str] = Query(default=None),
    afterId: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    includeBaseline: bool = Query(default=False),
    token: dict = Depends(get_token_or_session),
):
    freqs = [x.strip() for x in (frequencies or "").split(",") if x.strip()] or None
    syms = [x.strip() for x in (symbols or "").split(",") if x.strip()] or None
    rows = list_signal_events(
        frequencies=freqs, symbols=syms, limit=limit, include_baseline=includeBaseline
    )
    rows = [r for r in rows if int(r["id"]) > afterId]
    return {"code": 0, "message": "ok", "data": {"events": [_detail(r) for r in rows]}}


@router.get("/stream")
async def stream(
    request: Request,
    token: dict = Depends(get_token_or_session),
):
    """SSE 实时推送：页面打开后，新出现的门信号会自动弹出来。"""

    async def gen():
        queue = bus.subscribe()
        try:
            yield "event: hello\ndata: %s\n\n" % json.dumps(
                {"message": "已连接信号流"}, ensure_ascii=False
            )
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield "event: signal\ndata: %s\n\n" % json.dumps(
                        event, ensure_ascii=False, default=str
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
