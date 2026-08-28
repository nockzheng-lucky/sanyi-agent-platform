"""持续信号订阅存储。

Agent 在用户确认后调用 sanyi_create_subscription 创建订阅；
聊天页左侧面板通过 /api/v1/signal-subscriptions/matches 轮询当前命中结果。
"""
import json
from typing import Any, Dict, List, Optional

from .db import _now_iso, get_conn
from .factor_registry import registry


def user_id_from_record(token_record: Dict[str, Any]) -> int:
    user_id = token_record.get("_user_id")
    if not user_id:
        raise ValueError("该功能需要手机号登录账号")
    return int(user_id)


def create_signal_subscription(
    token_record: Dict[str, Any],
    factor_key: str,
    filters: Optional[Dict[str, Any]] = None,
    name: str = "",
) -> Dict[str, Any]:
    user_id = user_id_from_record(token_record)
    factor_key = str(factor_key or "").strip()
    spec = registry.get(factor_key)
    if spec is None:
        raise ValueError("因子不存在：%s" % factor_key)
    filters = registry.validate_params(spec, filters or {})
    if not filters:
        raise ValueError("订阅至少需要一个筛选条件")

    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO signal_subscriptions(user_id, factor_key, filters_json, name,"
        " status, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
        (
            user_id,
            factor_key,
            json.dumps(filters, ensure_ascii=False),
            (name or spec.name)[:80],
            _now_iso(),
            _now_iso(),
        ),
    )
    conn.commit()
    return get_signal_subscription(user_id, cur.lastrowid)


def get_signal_subscription(user_id: int, subscription_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM signal_subscriptions WHERE id = ? AND user_id = ?",
        (subscription_id, user_id),
    ).fetchone()
    return _to_dict(row) if row else None


def list_signal_subscriptions(user_id: int, include_inactive: bool = False) -> List[Dict[str, Any]]:
    conn = get_conn()
    sql = "SELECT * FROM signal_subscriptions WHERE user_id = ?"
    if not include_inactive:
        sql += " AND status = 'active'"
    sql += " ORDER BY id DESC"
    rows = conn.execute(sql, (user_id,)).fetchall()
    return [_to_dict(r) for r in rows]


def delete_signal_subscription(user_id: int, subscription_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE signal_subscriptions SET status = 'deleted', updated_at = ?"
        " WHERE id = ? AND user_id = ? AND status = 'active'",
        (_now_iso(), subscription_id, user_id),
    )
    conn.commit()
    return cur.rowcount == 1


def _to_dict(row: Any) -> Dict[str, Any]:
    rec = dict(row)
    try:
        filters = json.loads(rec.get("filters_json") or "{}")
    except (TypeError, ValueError):
        filters = {}
    return {
        "id": rec["id"],
        "factorKey": rec["factor_key"],
        "filters": filters,
        "name": rec.get("name"),
        "status": rec.get("status"),
        "createdAt": rec.get("created_at"),
        "updatedAt": rec.get("updated_at"),
    }
