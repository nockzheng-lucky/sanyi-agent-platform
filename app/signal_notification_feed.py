"""Persist the current signal notifications (Agent subscription match feed).

This is independent from Pushplus and from auto-trading: every active
subscription is evaluated on a timer and new eventId matches are stored with
their full payload so the crypto trading page can show "SKY 通知" and let the
user click 纳入开仓.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from .composite_evaluator import evaluate_subscription
from .config import SIGNAL_NOTIFICATION_FEED_POLL_SECONDS
from .db import _now_iso, get_conn
from .signal_subscriptions import list_all_active_signal_subscriptions


def _ensure_table() -> None:
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_signal_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id INTEGER NOT NULL,
            match_key TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            UNIQUE (subscription_id, match_key)
        )
        """
    )
    conn.commit()


def _actor_for_user(user_id: int) -> Dict[str, Any]:
    return {"id": user_id, "_table": "user_keys", "_user_id": user_id}


def _match_key(match: Dict[str, Any]) -> str:
    event_id = str(match.get("eventId") or match.get("event_id") or "")
    if event_id:
        return "event:%s" % event_id
    return "match:%s" % json.dumps(
        {
            key: match.get(key)
            for key in ("symbol", "frequency", "gateType", "gatePrice", "liveStatus", "formation")
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _record_notification(sub: Dict[str, Any], match: Dict[str, Any], now: str) -> bool:
    conn = get_conn()
    subscription_id = int(sub["id"])
    match_key = _match_key(match)
    payload = dict(match)
    payload.setdefault("subscriptionId", subscription_id)
    payload.setdefault("subscriptionName", sub.get("name") or "")
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    existing = conn.execute(
        "SELECT id FROM agent_signal_notifications WHERE subscription_id = ? AND match_key = ?",
        (subscription_id, match_key),
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO agent_signal_notifications(subscription_id, match_key, payload_json, first_seen_at, last_seen_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (subscription_id, match_key, payload_json, now, now),
        )
        conn.commit()
        return True
    conn.execute(
        "UPDATE agent_signal_notifications SET last_seen_at = ?, payload_json = ? WHERE id = ?",
        (now, payload_json, int(existing["id"])),
    )
    conn.commit()
    return False


def list_notifications(subscription_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM agent_signal_notifications WHERE subscription_id = ?"
        " ORDER BY last_seen_at DESC, id DESC LIMIT ?",
        (int(subscription_id), int(limit)),
    ).fetchall()
    values = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            payload = {}
        values.append({
            "id": int(row["id"]),
            "subscriptionId": int(row["subscription_id"]),
            "matchKey": row["match_key"],
            "firstSeenAt": row["first_seen_at"],
            "lastSeenAt": row["last_seen_at"],
            "payload": payload,
        })
    return values


async def scan_once() -> int:
    _ensure_table()
    now = _now_iso()
    created = 0
    for sub in list_all_active_signal_subscriptions():
        try:
            item = await evaluate_subscription(
                actor=_actor_for_user(int(sub["userId"])),
                sub=sub,
            )
        except Exception:
            continue
        for match in item.get("matches") or []:
            if not isinstance(match, dict):
                continue
            if _record_notification(sub, match, now):
                created += 1
    return created


class SignalNotificationRecorder:
    async def run(self) -> None:
        while True:
            try:
                await scan_once()
            except Exception:
                pass
            await asyncio.sleep(max(5, SIGNAL_NOTIFICATION_FEED_POLL_SECONDS))


recorder = SignalNotificationRecorder()
