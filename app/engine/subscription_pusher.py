"""订阅驱动的 Pushplus 推送 worker。

- 定时评估所有 active 订阅；
- 用 subscription_match_keys 去重，只在发现新的匹配信号时推送；
- 推送内容完全由订阅名和实际匹配信号动态生成；
- token 只从环境变量读取，不写日志。
"""
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

import httpx

from ..composite_evaluator import evaluate_subscription
from ..config import PUSHPLUS_URL, SIGNAL_SUBSCRIPTION_PUSH_SECONDS
from ..db import _now_iso, get_conn
from ..push_channels import get_pushplus_token
from ..signal_subscriptions import (
    is_signal_subscription_active,
    list_all_active_signal_subscriptions,
    set_subscription_baseline,
)


def _actor_for_user(user_id: int) -> Dict[str, Any]:
    return {"id": user_id, "_table": "user_keys", "_user_id": user_id}


_PUSHPLUS_BLOCKED_UNTIL: Dict[int, float] = {}
_PUSHPLUS_BLOCK_SECONDS = 3600


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
        str(match.get("comboKey") or ""),
        str(match.get("gateType") or ""),
        str(match.get("liveStatus") or ""),
        str(match.get("key") or ""),
        str(match.get("formation") or ""),
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


def _match_epoch(match: Dict[str, Any]) -> float:
    """取信号时间 epoch；没有时间返回 0。"""
    for key in ("eventAt", "openAt", "closeAt", "generatedAt", "updatedAt", "barTime"):
        value = match.get(key)
        if not value:
            continue
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            return dt.timestamp()
        except Exception:
            continue
    # 门信号没有 openAt 时，用 cross/t2/t1 结构时间兜底（qh 侧为北京时间）。
    for key in ("crossTime", "t2Time", "t1Time"):
        value = match.get(key)
        if not value:
            continue
        try:
            dt = datetime.strptime(str(value)[:11].strip(), "%m/%d %H:%M")
            dt = dt.replace(year=datetime.now().year, tzinfo=ZoneInfo("Asia/Shanghai"))
            return dt.timestamp()
        except Exception:
            continue
    return 0.0


def _is_stale_baseline_match(match: Dict[str, Any], baseline_at: Any) -> bool:
    """对门池类信号，baseline 之后才被 limit 扩出来的老门不补推。

    没有时间字段的历史门按老门处理；有 openAt/closeAt 且晚于 baseline 的新门照常推。
    """
    if match.get("gateType") not in ("tian", "di") and not match.get("key"):
        return False
    try:
        base_dt = datetime.fromisoformat(str(baseline_at).replace("Z", "+00:00"))
        base_epoch = base_dt.timestamp()
    except Exception:
        return False
    epoch = _match_epoch(match)
    return epoch <= 0 or epoch < base_epoch


def _match_line(match: Dict[str, Any]) -> str:
    label = match.get("contract") or match.get("name") or match.get("symbol") or "未知标的"
    freq = match.get("frequency")
    state = match.get("state") or match.get("status")
    gate_type = match.get("gateType")
    state_mapped = False
    if state in ("OPEN", "CLOSED", "FORMATION_ABOVE", "FORMATION_BELOW") and gate_type in ("tian", "di"):
        state = {
            ("tian", "OPEN"): "天门开",
            ("tian", "CLOSED"): "天门关",
            ("tian", "FORMATION_ABOVE"): "天门形成·无动作门上",
            ("tian", "FORMATION_BELOW"): "天门形成·无动作门下",
            ("di", "OPEN"): "地门开",
            ("di", "CLOSED"): "地门关",
            ("di", "FORMATION_ABOVE"): "地门形成·无动作门上",
            ("di", "FORMATION_BELOW"): "地门形成·无动作门下",
        }[(gate_type, state)]
        state_mapped = True
    elif state in ("OPEN", "FORMATION_ABOVE"):
        state = {"OPEN": "地门开", "FORMATION_ABOVE": "地门形成·无动作门上"}[state]
        state_mapped = True
    direction = match.get("direction")
    walk = match.get("walkMark") or match.get("walkCode")
    price = match.get("price") or match.get("last")
    changed = match.get("changePercent")
    time_text = _beijing_time(
        match.get("eventAt")
        or match.get("openAt")
        or match.get("closeAt")
        or match.get("generatedAt")
        or match.get("updatedAt")
        or match.get("barTime")
    )
    parts = [str(label)]
    if freq:
        parts.append(str(freq))
    if state:
        parts.append(str(state))
    if match.get("gateType") in ("tian", "di") and not state_mapped:
        parts.append("天门" if match.get("gateType") == "tian" else "地门")
    if match.get("liveStatus"):
        parts.append(str(match["liveStatus"]))
    if match.get("formation") and not state_mapped:
        parts.append(str(match["formation"]))
    if direction:
        parts.append({"long": "多", "short": "空", "none": "无"}.get(str(direction), str(direction)))
    if walk:
        parts.append("走%s" % walk)
    if changed is not None:
        parts.append("%s%%" % changed)
    if price is not None:
        parts.append("价 %s" % price)
    if match.get("gatePrice") is not None:
        parts.append("门价 %s" % match["gatePrice"])
    if match.get("gateMa208DistancePct") is not None:
        try:
            distance = float(match["gateMa208DistancePct"])
        except (TypeError, ValueError):
            distance = None
        if distance is not None:
            if abs(distance) < 0.005:
                parts.append("门价贴MA208")
            elif distance > 0:
                parts.append("门价高于MA208 %s%%" % distance)
            else:
                parts.append("门价低于MA208 %s%%" % abs(distance))
    if match.get("suggestedLeverage") is not None:
        parts.append("建议杠杆 %sx" % match["suggestedLeverage"])
    if match.get("volatilityPct") is not None:
        parts.append("波动率 %s%%" % match["volatilityPct"])
    for context in match.get("contexts") or []:
        if not isinstance(context, dict):
            continue
        for gate in context.get("matches") or []:
            if not isinstance(gate, dict):
                continue
            if gate.get("gateType") not in ("tian", "di"):
                continue
            gate_type = "天门" if gate.get("gateType") == "tian" else "地门"
            live_status = gate.get("liveStatus") or ""
            gate_parts = [gate_type]
            if live_status:
                gate_parts.append(str(live_status))
            if gate.get("gatePrice") is not None:
                gate_parts.append("门价 %s" % gate["gatePrice"])
            parts.append(" · ".join(gate_parts))
    line = " · ".join(parts)
    if time_text:
        line += "（%s）" % time_text
    return line


def _build_push(title_prefix: str, sub: Dict[str, Any], match: Dict[str, Any]) -> Tuple[str, str]:
    line = _match_line(match)
    short = line.split("（", 1)[0]
    title = "%s%s · %s" % (
        title_prefix,
        sub.get("name") or sub.get("factorKey"),
        short,
    )
    return title[:100], "- " + line


def _build_batch_push(title_prefix: str, entries: List[Dict[str, Any]]) -> Tuple[str, str]:
    """同一轮出现的多条新信号合并成一条 Pushplus。"""
    if len(entries) == 1:
        return _build_push(title_prefix, entries[0]["sub"], entries[0]["match"])

    lines: List[str] = []
    first_short = ""
    for entry in entries:
        sub = entry["sub"]
        line = _match_line(entry["match"])
        sub_label = sub.get("name") or sub.get("factorKey") or "订阅"
        lines.append("- [%s] %s" % (sub_label, line))
        if not first_short:
            first_short = line.split("（", 1)[0]

    title = "%s%d 条新信号 · %s" % (title_prefix, len(entries), first_short)
    return title[:100], "\n".join(lines)


async def send_pushplus_checked(title: str, content: str, token: str) -> Tuple[bool, int]:
    token = (token or "").strip()
    if not token:
        return False, 0
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
        return False, 0
    code = int(body.get("code") or 0)
    return response.status_code == 200 and code == 200, code


async def send_pushplus(title: str, content: str, token: str) -> bool:
    ok, _code = await send_pushplus_checked(title, content, token)
    return ok


class SubscriptionPusher:
    def __init__(self, title_prefix: str = "【三易订阅】") -> None:
        self.title_prefix = title_prefix

    async def run_once(self) -> int:
        sent = 0
        now = _now_iso()
        pending_by_user: Dict[int, Dict[str, Any]] = {}
        for sub in list_all_active_signal_subscriptions():
            sub_id = int(sub["id"])
            user_id = int(sub["userId"])
            token = get_pushplus_token(user_id)
            if not token:
                continue
            try:
                item = await evaluate_subscription(
                    actor=_actor_for_user(user_id),
                    sub=sub,
                )
                # 评估期间用户可能刚取消订阅；推送前实时复核，避免取消后仍推。
                if not is_signal_subscription_active(sub_id, user_id):
                    print(
                        "subscription_pusher skip cancelled sub=%s user=%s"
                        % (sub_id, user_id)
                    )
                    continue
                matches = item.get("matches") or []
                if not sub.get("baselineAt"):
                    # 首轮只建 baseline：把订阅时已经存在的信号记为“已推送”，不实际发。
                    baseline_keys: List[str] = []
                    for match in matches:
                        if not isinstance(match, dict):
                            continue
                        key = _match_key(sub["factorKey"], match)
                        record_match(sub["id"], key, now)
                        baseline_keys.append(key)
                    if baseline_keys:
                        mark_pushed(sub["id"], baseline_keys, now)
                    set_subscription_baseline(sub["id"], now)
                    continue

                pending: List[Dict[str, Any]] = []
                stale_keys: List[str] = []
                for match in matches:
                    if not isinstance(match, dict):
                        continue
                    key = _match_key(sub["factorKey"], match)
                    if _is_stale_baseline_match(match, sub.get("baselineAt")):
                        # baseline 之后因 limit 扩大才进入视野的老门：记录但不推送。
                        record_match(sub["id"], key, now)
                        stale_keys.append(key)
                        continue
                    if record_match(sub["id"], key, now):
                        pending.append({"sub": sub, "match": match, "key": key})
                if stale_keys:
                    mark_pushed(sub["id"], stale_keys, now)
                if not pending:
                    continue
                bucket = pending_by_user.setdefault(
                    user_id,
                    {"token": token, "entries": []},
                )
                bucket["entries"].extend(pending)
            except Exception as exc:  # noqa: BLE001 - 单个订阅失败不阻塞其他订阅
                print(
                    "subscription_pusher error sub=%s: %s: %s"
                    % (sub.get("id"), type(exc).__name__, exc)
                )

        for user_id, bucket in pending_by_user.items():
            blocked_until = _PUSHPLUS_BLOCKED_UNTIL.get(user_id, 0.0)
            if asyncio.get_running_loop().time() < blocked_until:
                continue

            # 同一轮出现的新信号合并成一条 Pushplus；发送前逐订阅复核 active。
            entries: List[Dict[str, Any]] = []
            for entry in bucket["entries"]:
                sub_id = int(entry["sub"]["id"])
                if is_signal_subscription_active(sub_id, user_id):
                    entries.append(entry)
                else:
                    print(
                        "subscription_pusher drop cancelled sub=%s user=%s"
                        % (sub_id, user_id)
                    )
            if not entries:
                continue

            title, content = _build_batch_push(self.title_prefix, entries)
            ok, code = await send_pushplus_checked(
                title,
                content,
                token=str(bucket["token"] or ""),
            )
            if ok:
                keys_by_sub: Dict[int, List[str]] = {}
                for entry in entries:
                    keys_by_sub.setdefault(int(entry["sub"]["id"]), []).append(entry["key"])
                for sub_id, keys in keys_by_sub.items():
                    mark_pushed(sub_id, keys, now)
                sent += 1
                continue
            if code in (900, 903):
                # 900=日请求次数超限；903=token 无效。停止重试 1 小时，避免继续冲击通道。
                _PUSHPLUS_BLOCKED_UNTIL[user_id] = (
                    asyncio.get_running_loop().time() + _PUSHPLUS_BLOCK_SECONDS
                )
                print(
                    "subscription_pusher pushplus blocked user=%s code=%s"
                    % (user_id, code)
                )
            # 其它失败（网络/5xx）下一轮继续补发。

        return sent

    async def run(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001 - worker 必须持续运行
                print("subscription_pusher loop error: %s" % exc)
            await asyncio.sleep(max(10, SIGNAL_SUBSCRIPTION_PUSH_SECONDS))
