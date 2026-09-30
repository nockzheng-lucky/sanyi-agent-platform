"""Agent 订阅信号 -> 东京币圈交易桥。

独立于 Pushplus：对所有 active 订阅评估（但只允许显式配置的订阅号），
用 agent_trade_signal_keys 表按 eventId 去重，发现新匹配后签名 POST 到
东京币圈 Web 的 /api/trading/agent-signals。

失败会持续重试，不重复发单；本模块不持有 Bybit 凭据。
"""
import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

from .composite_evaluator import evaluate_subscription
from .config import (
    AGENT_TRADE_ENABLED,
    AGENT_TRADE_POLL_SECONDS,
    AGENT_TRADE_SUBSCRIPTION_IDS,
    AGENT_TRADE_TIMEOUT_SECONDS,
    AGENT_TRADE_VERIFY_SSL,
    AGENT_TRADE_WEBHOOK_SECRET_FILE,
    AGENT_TRADE_WEBHOOK_URL,
)
from .db import _now_iso, get_conn
from .signal_subscriptions import (
    is_signal_subscription_active,
    list_all_active_signal_subscriptions,
)


def _actor_for_user(user_id: int) -> Dict[str, Any]:
    return {"id": user_id, "_table": "user_keys", "_user_id": user_id}


def _load_secret() -> str:
    try:
        value = Path(AGENT_TRADE_WEBHOOK_SECRET_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return value


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


def _ensure_table() -> None:
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_trade_signal_keys (
            subscription_id INTEGER NOT NULL,
            match_key TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            sent_at TEXT,
            last_error TEXT,
            PRIMARY KEY (subscription_id, match_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_trade_bridge_state (
            subscription_id INTEGER PRIMARY KEY,
            baseline_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_trade_bindings (
            subscription_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    for raw in AGENT_TRADE_SUBSCRIPTION_IDS:
        try:
            subscription_id = int(raw)
        except (TypeError, ValueError):
            continue
        conn.execute(
            "INSERT OR IGNORE INTO agent_trade_bindings(subscription_id, enabled, updated_at)"
            " VALUES (?, 1, ?)",
            (subscription_id, _now_iso()),
        )
    conn.commit()


def set_agent_trade_binding(subscription_id: int, enabled: bool) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO agent_trade_bindings(subscription_id, enabled, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(subscription_id) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at",
        (int(subscription_id), 1 if enabled else 0, _now_iso()),
    )
    conn.commit()


def enabled_agent_trade_subscription_ids() -> frozenset:
    conn = get_conn()
    rows = conn.execute(
        "SELECT subscription_id FROM agent_trade_bindings WHERE enabled = 1"
    ).fetchall()
    return frozenset(int(row["subscription_id"]) for row in rows)


def binding_rows() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT subscription_id, enabled, updated_at FROM agent_trade_bindings ORDER BY subscription_id"
    ).fetchall()
    return [dict(row) for row in rows]


def _ensure_baseline(subscription_id: int, now: str) -> bool:
    """Return True when this call just created the first baseline."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT baseline_at FROM agent_trade_bridge_state WHERE subscription_id = ?",
        (subscription_id,),
    ).fetchone()
    if existing is not None:
        return False
    conn.execute(
        "INSERT INTO agent_trade_bridge_state(subscription_id, baseline_at) VALUES (?, ?)",
        (subscription_id, now),
    )
    conn.commit()
    return True


def _record_match(subscription_id: int, match_key: str, now: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Return (should_send, existing_row). New rows and unsent rows are retried."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT * FROM agent_trade_signal_keys WHERE subscription_id = ? AND match_key = ?",
        (subscription_id, match_key),
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO agent_trade_signal_keys(subscription_id, match_key, first_seen_at, last_seen_at)"
            " VALUES (?, ?, ?, ?)",
            (subscription_id, match_key, now, now),
        )
        conn.commit()
        return True, None
    conn.execute(
        "UPDATE agent_trade_signal_keys SET last_seen_at = ?"
        " WHERE subscription_id = ? AND match_key = ?",
        (now, subscription_id, match_key),
    )
    conn.commit()
    row = dict(existing)
    return row.get("sent_at") is None, row


def _mark_sent(subscription_id: int, match_key: str, now: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE agent_trade_signal_keys SET sent_at = ?, last_error = NULL"
        " WHERE subscription_id = ? AND match_key = ?",
        (now, subscription_id, match_key),
    )
    conn.commit()


def _mark_skipped(subscription_id: int, match_key: str, error: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE agent_trade_signal_keys SET sent_at = ?, last_error = ?"
        " WHERE subscription_id = ? AND match_key = ?",
        (_now_iso(), str(error)[:300], subscription_id, match_key),
    )
    conn.commit()


def _mark_error(subscription_id: int, match_key: str, error: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE agent_trade_signal_keys SET last_error = ?"
        " WHERE subscription_id = ? AND match_key = ?",
        (str(error)[:300], subscription_id, match_key),
    )
    conn.commit()


def _signed_headers(body: bytes, secret: str) -> Dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"),
        ("%s.%s" % (timestamp, body.decode("utf-8"))).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Sanyi-Agent-Timestamp": timestamp,
        "X-Sanyi-Agent-Signature": signature,
    }


def _payload(sub: Dict[str, Any], match: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "signal_id": "%s:%s" % (sub["id"], match.get("eventId")),
        "subscription_id": int(sub["id"]),
        "symbol": str(match.get("symbol") or "").upper(),
        "frequency": str(match.get("frequency") or ""),
        "gate_type": str(match.get("gateType") or ""),
        "gate_price": str(match.get("gatePrice") or ""),
        "suggested_leverage": int(match.get("suggestedLeverage") or 0),
        "event_id": str(match.get("eventId") or ""),
        "received_at": _now_iso(),
        "name": str(sub.get("name") or ""),
        "last_bar_time": match.get("lastBarTime") or match.get("last_bar_time") or "",
        "volatility_pct": match.get("volatilityPct"),
        "atr_pct": match.get("atrPct"),
        "day_range_pct": match.get("dayRangePct"),
        "computed_at": match.get("computedAt") or match.get("computed_at") or "",
        "contexts": match.get("contexts") or [],
    }


async def _post_signal(payload: Dict[str, Any], secret: str) -> Tuple[str, str]:
    """Return (outcome, error). outcome: sent / skipped / retry."""
    if not AGENT_TRADE_WEBHOOK_URL:
        return "retry", "agent_trade_webhook_url_not_configured"
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                AGENT_TRADE_TIMEOUT_SECONDS,
                connect=min(10.0, AGENT_TRADE_TIMEOUT_SECONDS),
            ),
            verify=AGENT_TRADE_VERIFY_SSL,
        ) as client:
            response = await client.post(
                AGENT_TRADE_WEBHOOK_URL,
                content=body,
                headers=_signed_headers(body, secret),
            )
    except httpx.HTTPError as exc:
        return "retry", "%s: %s" % (type(exc).__name__, exc)
    if response.status_code == 200:
        return "sent", "ok"
    if response.status_code == 409:
        # 账户未就绪：这个新信号按用户口径直接跳过，不排队、不补发。
        return "skipped", "account_not_ready:%s" % response.text[:160]
    return "retry", "http_%s: %s" % (response.status_code, response.text[:200])


def _valid_match(match: Dict[str, Any]) -> bool:
    if not isinstance(match, dict):
        return False
    if str(match.get("gateType") or "") != "di":
        return False
    if match.get("gatePrice") in (None, ""):
        return False
    if not str(match.get("symbol") or "").strip() or not str(match.get("frequency") or "").strip():
        return False
    return bool(str(match.get("eventId") or "").strip())


async def _send_matches_now(sub: Dict[str, Any], item: Dict[str, Any], secret: str) -> int:
    """Send current matches immediately (used by app-shell activation)."""
    sent = 0
    now = _now_iso()
    sub_id = int(sub["id"])
    for match in item.get("matches") or []:
        if not _valid_match(match):
            continue
        key = _match_key(match)
        should_send, _existing = _record_match(sub_id, key, now)
        if not should_send:
            continue
        payload = _payload(sub, match)
        outcome, error = await _post_signal(payload, secret)
        if outcome == "sent":
            _mark_sent(sub_id, key, now)
            sent += 1
        elif outcome == "skipped":
            _mark_skipped(sub_id, key, error)
        else:
            _mark_error(sub_id, key, error)
    return sent


class AgentTradeBridge:
    def __init__(self) -> None:
        _ensure_table()

    async def run_once(self) -> int:
        if not AGENT_TRADE_ENABLED:
            return 0
        secret = _load_secret()
        if not secret:
            print("agent_trade_bridge: webhook secret missing")
            return 0
        allowed = enabled_agent_trade_subscription_ids()
        if not allowed:
            return 0
        sent = 0
        now = _now_iso()
        for sub in list_all_active_signal_subscriptions():
            sub_id = int(sub["id"])
            if sub_id not in allowed:
                continue
            try:
                item = await evaluate_subscription(
                    actor=_actor_for_user(int(sub["userId"])),
                    sub=sub,
                )
                if not is_signal_subscription_active(sub_id, int(sub["userId"])):
                    continue
                first_baseline = _ensure_baseline(sub_id, now)
                for match in item.get("matches") or []:
                    if not _valid_match(match):
                        continue
                    key = _match_key(match)
                    if first_baseline:
                        # 首次只建 baseline：当前已存在的信号不触发真实交易。
                        _record_match(sub_id, key, now)
                        _mark_sent(sub_id, key, now)
                        continue
                    should_send, _existing = _record_match(sub_id, key, now)
                    if not should_send:
                        continue
                    payload = _payload(sub, match)
                    outcome, error = await _post_signal(payload, secret)
                    if outcome == "sent":
                        _mark_sent(sub_id, key, now)
                        sent += 1
                    elif outcome == "skipped":
                        _mark_skipped(sub_id, key, error)
                    else:
                        _mark_error(sub_id, key, error)
            except Exception as exc:  # noqa: BLE001 - one bad subscription must not stop bridge
                print(
                    "agent_trade_bridge error sub=%s: %s: %s"
                    % (sub_id, type(exc).__name__, exc)
                )
        return sent

    async def run(self) -> None:
        while True:
            try:
                sent = await self.run_once()
                print("agent_trade_bridge run sent=%s" % sent, flush=True)
            except Exception as exc:  # noqa: BLE001 - worker must stay alive
                print("agent_trade_bridge loop error: %s" % exc, flush=True)
            await asyncio.sleep(max(5, AGENT_TRADE_POLL_SECONDS))


bridge = AgentTradeBridge()
