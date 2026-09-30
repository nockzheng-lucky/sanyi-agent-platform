"""持续信号订阅 API。

Agent 确认订阅后，订阅条件存 signal_subscriptions；
聊天页左侧面板通过 /matches 或 /stream 获取当前命中的信号列表。
"""
import asyncio
import json
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..composite_evaluator import evaluate_subscription
from ..config import SIGNAL_SUBSCRIPTION_POLL_SECONDS
from ..security import get_actor
from ..signal_subscriptions import (
    delete_signal_subscription,
    list_signal_subscriptions,
    user_id_from_record,
)

router = APIRouter(prefix="/api/v1/signal-subscriptions", tags=["signal-subscriptions"])


def _error_detail(status_code: int, message: str) -> Dict[str, Any]:
    return {"code": status_code, "message": message, "data": None}


async def build_snapshot(actor: Dict[str, Any]) -> List[Dict[str, Any]]:
    user_id = user_id_from_record(actor)
    subscriptions = list_signal_subscriptions(user_id)
    snapshot: List[Dict[str, Any]] = []
    for sub in subscriptions:
        try:
            item = await evaluate_subscription(actor, sub)
        except Exception as exc:  # noqa: BLE001 - 订阅面板不能因单个订阅异常断流
            item = {
                "id": sub.get("id"),
                "factorKey": sub.get("factorKey"),
                "name": sub.get("name"),
                "filters": sub.get("filters"),
                "conditions": sub.get("conditions") or [],
                "createdAt": sub.get("createdAt"),
                "updatedAt": sub.get("updatedAt"),
                "signal": None,
                "summary": "",
                "generatedAt": None,
                "matches": [],
                "conditionErrors": [],
                "error": "%s: %s" % (type(exc).__name__, exc),
            }
        snapshot.append(item)
    return snapshot


@router.get("")
async def subscriptions(actor: Dict[str, Any] = Depends(get_actor)):
    try:
        rows = list_signal_subscriptions(user_id_from_record(actor))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=_error_detail(403, str(exc)))
    return {"code": 0, "message": "ok", "data": {"subscriptions": rows}}


@router.delete("/{subscription_id}")
async def delete_subscription(subscription_id: int, actor: Dict[str, Any] = Depends(get_actor)):
    try:
        user_id = user_id_from_record(actor)
        deleted = delete_signal_subscription(user_id, subscription_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=_error_detail(403, str(exc)))
    if not deleted:
        raise HTTPException(status_code=404, detail=_error_detail(404, "订阅不存在"))
    return {"code": 0, "message": "订阅已删除", "data": None}


@router.get("/matches")
async def matches(actor: Dict[str, Any] = Depends(get_actor)):
    try:
        snapshot = await build_snapshot(actor)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=_error_detail(403, str(exc)))
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "subscriptions": snapshot,
            "refreshSeconds": SIGNAL_SUBSCRIPTION_POLL_SECONDS,
        },
    }


@router.get("/stream")
async def stream(request: Request, actor: Dict[str, Any] = Depends(get_actor)):
    """SSE：订阅面板打开期间，每 30 秒刷新一次当前命中信号。"""

    async def gen():
        yield "event: hello\ndata: %s\n\n" % json.dumps(
            {
                "message": "已连接订阅信号流",
                "refreshSeconds": SIGNAL_SUBSCRIPTION_POLL_SECONDS,
            },
            ensure_ascii=False,
        )
        last_snapshot = None
        while True:
            if await request.is_disconnected():
                break
            try:
                snapshot = await build_snapshot(actor)
                if snapshot != last_snapshot:
                    yield "event: snapshot\ndata: %s\n\n" % json.dumps(
                        {"subscriptions": snapshot}, ensure_ascii=False, default=str
                    )
                    last_snapshot = snapshot
            except Exception as exc:  # noqa: BLE001 - 单轮失败不能断流
                yield "event: error\ndata: %s\n\n" % json.dumps(
                    {"message": "%s: %s" % (type(exc).__name__, exc)}, ensure_ascii=False
                )
            for _ in range(max(1, SIGNAL_SUBSCRIPTION_POLL_SECONDS)):
                if await request.is_disconnected():
                    break
                await asyncio.sleep(1)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
