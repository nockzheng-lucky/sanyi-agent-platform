"""已推送信号流水：策略订阅页“今日已发”只读当天（北京时间自然日）。

推送 worker 在 Pushplus 实际发送成功后写入一条 payload 快照；
页面按 sent_at 过滤出北京时间今天，历史数据保留在库里但不展示。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from .db import get_conn


def _ensure_episode_started(
    subscription_id: int,
    match_key: str,
    sent_at: str,
) -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT episode_started_at, first_seen_at, last_seen_at"
        " FROM subscription_match_keys WHERE subscription_id = ? AND match_key = ?",
        (int(subscription_id), match_key),
    ).fetchone()
    if row is not None:
        return row["episode_started_at"] or row["first_seen_at"] or sent_at
    return sent_at


def record_sent_signal(
    subscription_id: int,
    match_key: str,
    payload: Dict[str, Any],
    sent_at: str,
) -> None:
    """记录一条实际发送成功的信号快照；同一 episode 内重复发送只保留一条。"""
    conn = get_conn()
    episode_started_at = _ensure_episode_started(subscription_id, match_key, sent_at)
    snapshot = dict(payload or {})
    snapshot.setdefault("subscriptionId", int(subscription_id))
    payload_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO subscription_signal_log("
            "subscription_id, match_key, payload_json, episode_started_at, sent_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                int(subscription_id),
                match_key,
                payload_json,
                episode_started_at,
                sent_at,
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - 流水失败不阻塞推送主流程
        print("subscription_signal_log insert failed sub=%s: %s" % (subscription_id, exc))


def _beijing_today() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def list_sent_today_for_user(user_id: int, limit: int = 2000) -> List[Dict[str, Any]]:
    """返回该用户今天（北京时间 00:00-24:00）实际推送过的信号，时间倒序。"""
    today = _beijing_today()
    conn = get_conn()
    rows = conn.execute(
        "SELECT l.*, s.name AS subscription_name, s.factor_key AS factor_key"
        " FROM subscription_signal_log l"
        " JOIN signal_subscriptions s ON s.id = l.subscription_id"
        " WHERE s.user_id = ? AND s.status = 'active'"
        " ORDER BY l.sent_at DESC, l.id DESC LIMIT ?",
        (int(user_id), int(limit)),
    ).fetchall()

    result: List[Dict[str, Any]] = []
    for row in rows:
        sent_at = _parse_iso(row["sent_at"])
        if sent_at is None:
            continue
        local = sent_at.astimezone(ZoneInfo("Asia/Shanghai"))
        if local.date() != today.date():
            continue
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            payload = {}
        result.append(
            {
                "id": int(row["id"]),
                "subscriptionId": int(row["subscription_id"]),
                "subscriptionName": row["subscription_name"],
                "factorKey": row["factor_key"],
                "matchKey": row["match_key"],
                "episodeStartedAt": row["episode_started_at"],
                "sentAt": row["sent_at"],
                "payload": payload,
            }
        )
    return result
