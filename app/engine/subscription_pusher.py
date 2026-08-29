"""订阅驱动的 Pushplus 推送 worker。

- 定时评估所有 active 订阅；
- 用 subscription_match_keys 去重，只在发现新的匹配信号时推送；
- 推送内容完全由订阅名和实际匹配信号动态生成；
- token 只从环境变量读取，不写日志。
"""
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from ..config import (
    PUSHPLUS_TOKEN,
    PUSHPLUS_URL,
    SIGNAL_SUBSCRIPTION_PUSH_SECONDS,
)
from ..db import _now_iso, get_conn
from ..factor_registry import registry
from ..signal_subscriptions import list_all_active_signal_subscriptions


def _actor_for_user(user_id: int) -> Dict[str, Any]:
    return {"id": user_id, "_table": "user_keys", "_user_id": user_id}


def _match_key(factor_key: str, match: Dict[str, Any]) -> str:
    event_id = match.get("eventId") or match.get("event_id")
    if event_id:
        return "event:%s" % event_id
    parts = [
        str(match.get("symbol") or ""),
        str(match.get("frequency") or ""),
        str(match.get("state") or match.get("status") or ""),
        str(match.get("direction") or ""),
        str(match.get("walkCode") or ""),
        str(match.get("walkMark") or ""),
    ]
    return "%s:%s" % (factor_key, ":".join(parts))


def record_match(subscription_id: int, match_key: str, now: str) -> bool:
    """记录/续期一个匹配。返回 True 表示本次应该推送（新出现或上次推送失败）。"""
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO subscription_match_keys(subscription_id, match_key, first_seen_at, last_seen_at)"
            " VALUES (?, ?, ?, ?)",
            (subscription_id, match_key, now, now),
        )
        conn.commit()
        return True
    except Exception:
        # 已存在：续期，并沿用 last_pushed_at 判断是否需要重试。
        conn.execute(
            "UPDATE subscription_match_keys SET last_seen_at = ?"
            " WHERE subscription_id = ? AND match_key = ?",
            (now, subscription_id, match_key),
        )
        conn.commit()
        row = conn.execute(
            "SELECT last_pushed_at FROM subscription_match_keys"
            " WHERE subscription_id = ? AND match_key = ?",
            (subscription_id, match_key),
        ).fetchone()
        return row is None or row["last_pushed_at"] is None


def mark_pushed(subscription_id: int, match_keys: List[str], now: str) -> None:
    conn = get_conn()
    conn.executemany(
        "UPDATE subscription_match_keys SET last_pushed_at = ?"
        " WHERE subscription_id = ? AND match_key = ?",
        [(now, subscription_id, key) for key in match_keys],
    )
    conn.commit()


def _beijing_time(value: Any) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(ZoneInfo("Asia/Shanghai"))
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return str(value).replace("T", " ")[:16]


def _match_line(match: Dict[str, Any]) -> str:
    label = match.get("contract") or match.get("name") or match.get("symbol") or "未知标的"
    freq = match.get("frequency")
    state = match.get("state") or match.get("status")
    if state in ("OPEN", "FORMATION_ABOVE"):
        state = {"OPEN": "地门开", "FORMATION_ABOVE": "地门形成·无动作门上"}[state]
    direction = match.get("direction")
    walk = match.get("walkMark") or match.get("walkCode")
    price = match.get("price") or match.get("last")
    changed = match.get("changePercent")
    time_text = _beijing_time(
        match.get("updatedAt")
        or match.get("generatedAt")
        or match.get("openAt")
        or match.get("barTime")
    )
    parts = [str(label)]
    if freq:
        parts.append(str(freq))
    if state:
        parts.append(str(state))
    if direction:
        parts.append({"long": "多", "short": "空", "none": "无"}.get(str(direction), str(direction)))
    if walk:
        parts.append("走%s" % walk)
    if changed is not None:
        parts.append("%s%%" % changed)
    if price is not None:
        parts.append("价 %s" % price)
    line = " · ".join(parts)
    if time_text:
        line += "（%s）" % time_text
    return line


def _build_push(title_prefix: str, sub: Dict[str, Any], matches: List[Dict[str, Any]]) -> Tuple[str, str]:
    title = "%s%s · 新增 %d 条信号" % (
        title_prefix,
        sub.get("name") or sub.get("factorKey"),
        len(matches),
    )
    lines = []
    for match in matches[:10]:
        lines.append("- " + _match_line(match))
    if len(matches) > 10:
        lines.append("- 其余 %d 条请在平台订阅面板查看" % (len(matches) - 10))
    return title, "\n".join(lines)


async def send_pushplus(title: str, content: str, token: str = "") -> bool:
    token = (token or PUSHPLUS_TOKEN).strip()
    if not token:
        return False
    payload = {
        "token": token,
        "title": title[:100],
        "content": content,
        "template": "txt",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = await client.post(PUSHPLUS_URL, json=payload)
            body = response.json()
    except Exception:
        return False
    return response.status_code == 200 and str(body.get("code")) == "200"


class SubscriptionPusher:
    def __init__(self, title_prefix: str = "【三易订阅】") -> None:
        self.title_prefix = title_prefix

    async def run_once(self) -> int:
        if not PUSHPLUS_TOKEN:
            return 0
        sent = 0
        now = _now_iso()
        for sub in list_all_active_signal_subscriptions():
            try:
                result = await registry.evaluate(
                    token_record=_actor_for_user(int(sub["userId"])),
                    factor_key=sub["factorKey"],
                    params=sub["filters"],
                    audit=False,
                )
                details = result.get("details") or {}
                matches = details.get("events") or details.get("cells") or details.get("coins") or []
                pending: List[Dict[str, Any]] = []
                pending_keys: List[str] = []
                for match in matches:
                    if not isinstance(match, dict):
                        continue
                    key = _match_key(sub["factorKey"], match)
                    if record_match(sub["id"], key, now):
                        pending.append(match)
                        pending_keys.append(key)
                if not pending:
                    continue
                title, content = _build_push(self.title_prefix, sub, pending)
                if await send_pushplus(title, content):
                    mark_pushed(sub["id"], pending_keys, now)
                    sent += 1
            except Exception as exc:  # noqa: BLE001 - 单个订阅失败不阻塞其他订阅
                print(
                    "subscription_pusher error sub=%s: %s: %s"
                    % (sub.get("id"), type(exc).__name__, exc)
                )
        return sent

    async def run(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001 - worker 必须持续运行
                print("subscription_pusher loop error: %s" % exc)
            await asyncio.sleep(max(10, SIGNAL_SUBSCRIPTION_PUSH_SECONDS))
