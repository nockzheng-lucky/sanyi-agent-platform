"""App-shell control API for Agent auto-trade bindings.

Tokyo web proxies these routes so the user can pick an Agent subscription in
the crypto trading page and enable/disable automatic execution for it.

Auth: same HMAC-SHA256 + timestamp scheme as the signal webhook.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request

from .agent_trade_bridge import (
    _actor_for_user,
    _ensure_baseline,
    _ensure_table,
    _load_secret,
    _send_matches_now,
    binding_rows,
    enabled_agent_trade_subscription_ids,
    set_agent_trade_binding,
)
from .composite_evaluator import evaluate_subscription
from .db import _now_iso
from .signal_notification_feed import list_notifications
from .signal_subscriptions import list_all_active_signal_subscriptions

router = APIRouter(prefix="/api/v1/agent-trade", tags=["agent-trade"])


def _verify(request: Request, timestamp: Optional[str], signature: Optional[str]) -> None:
    secret = _load_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="agent_trade_secret_not_configured")
    try:
        timestamp_int = int(timestamp or "")
    except (TypeError, ValueError):
        timestamp_int = 0
    now_epoch = int(__import__("time").time())
    if abs(now_epoch - timestamp_int) > 300:
        raise HTTPException(status_code=401, detail="agent_trade_auth_required")
    body = getattr(request.state, "raw_body", b"")
    expected = hmac.new(
        secret.encode("utf-8"),
        ("%d.%s" % (timestamp_int, body.decode("utf-8", "replace"))).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, str(signature or "")):
        raise HTTPException(status_code=401, detail="agent_trade_auth_required")


async def _subscription_view(sub: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": sub["id"],
        "name": sub.get("name") or "",
        "factorKey": sub.get("factorKey") or "",
        "conditions": sub.get("conditions") or [],
        "status": sub.get("status"),
        "baselineAt": sub.get("baselineAt"),
        "enabled": int(sub["id"]) in enabled_agent_trade_subscription_ids(),
        "matchCount": 0,
        "matches": [],
        "mode": "new_signals_only",
        "notifications": list_notifications(int(sub["id"]), limit=30),
    }


@router.get("/subscriptions")
async def subscriptions(
    request: Request,
    x_sanyi_agent_timestamp: Optional[str] = Header(None),
    x_sanyi_agent_signature: Optional[str] = Header(None),
):
    _verify(request, x_sanyi_agent_timestamp, x_sanyi_agent_signature)
    active = list_all_active_signal_subscriptions()
    values = []
    for sub in active:
        try:
            values.append(await _subscription_view(sub))
        except Exception:
            values.append({
                "id": sub["id"],
                "name": sub.get("name") or "",
                "factorKey": sub.get("factorKey") or "",
                "conditions": sub.get("conditions") or [],
                "status": sub.get("status"),
                "baselineAt": sub.get("baselineAt"),
                "enabled": int(sub["id"]) in enabled_agent_trade_subscription_ids(),
                "matchCount": 0,
                "matches": [],
                "error": "evaluation_unavailable",
            })
    return {"subscriptions": values, "bindings": binding_rows()}


@router.post("/bindings/{subscription_id}/activate")
async def activate(
    subscription_id: int,
    request: Request,
    x_sanyi_agent_timestamp: Optional[str] = Header(None),
    x_sanyi_agent_signature: Optional[str] = Header(None),
):
    raw_body = await request.body()
    request.state.raw_body = raw_body
    _verify(request, x_sanyi_agent_timestamp, x_sanyi_agent_signature)
    active = {int(sub["id"]): sub for sub in list_all_active_signal_subscriptions()}
    sub = active.get(int(subscription_id))
    if sub is None:
        raise HTTPException(status_code=404, detail="subscription_not_active")
    now = _now_iso()
    set_agent_trade_binding(int(subscription_id), True)
    _ensure_baseline(int(subscription_id), now)
    return {
        "ok": True,
        "subscriptionId": int(subscription_id),
        "enabled": True,
        "baselineAt": now,
        "mode": "new_signals_only",
        "currentMatches": [],
    }


@router.post("/bindings/{subscription_id}/deactivate")
async def deactivate(
    subscription_id: int,
    request: Request,
    x_sanyi_agent_timestamp: Optional[str] = Header(None),
    x_sanyi_agent_signature: Optional[str] = Header(None),
):
    _verify(request, x_sanyi_agent_timestamp, x_sanyi_agent_signature)
    set_agent_trade_binding(int(subscription_id), False)
    return {"ok": True, "subscriptionId": int(subscription_id), "enabled": False}
