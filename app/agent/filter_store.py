"""Agent 的持久筛选条件存储。

用户用自然语言说“只看 15 分钟”“只保留破诀”时，LLM 调用
sanyi_update_filters 把条件按 factorKey 存到这里；后续执行
sanyi_evaluate_factor 时自动把存量条件合并进 params（当次显式参数优先）。

存储键：
- 用户 Key：user:<user_id>（换 Key 后筛选条件仍保留）
- 旧版令牌：tokens:<id>
"""
import json
from typing import Any, Dict, Optional

from ..db import _now_iso, get_conn

FILTER_KEYS = (
    "frequencies",
    "symbols",
    "states",
    "directions",
    "broken",
    "walkCodes",
    "walkMarks",
    "maxAgeMinutes",
    "limit",
)


def owner_key(token_record: Dict[str, Any]) -> str:
    user_id = token_record.get("_user_id")
    if user_id:
        return "user:%s" % int(user_id)
    return "%s:%s" % (str(token_record.get("_table") or "tokens"), int(token_record["id"]))


def get_filter_state(token_record: Dict[str, Any]) -> Dict[str, Any]:
    """返回 {factorKey: filters}，无记录时为 {}。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT filters_json FROM agent_filters WHERE owner_key = ?",
        (owner_key(token_record),),
    ).fetchone()
    if row is None:
        return {}
    try:
        state = json.loads(row["filters_json"])
    except (TypeError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def patch_filters(
    token_record: Dict[str, Any],
    factor_key: str,
    patch: Dict[str, Any],
    replace: bool = False,
) -> Dict[str, Any]:
    state = get_filter_state(token_record)
    current = state.get(factor_key) if isinstance(state.get(factor_key), dict) else {}
    if replace:
        merged = {}
    else:
        merged = dict(current)
    for key, value in (patch or {}).items():
        if key not in FILTER_KEYS:
            continue
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    if merged:
        state[factor_key] = merged
    else:
        state.pop(factor_key, None)

    conn = get_conn()
    conn.execute(
        "INSERT INTO agent_filters(owner_key, filters_json, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(owner_key) DO UPDATE SET filters_json = excluded.filters_json,"
        " updated_at = excluded.updated_at",
        (owner_key(token_record), json.dumps(state, ensure_ascii=False), _now_iso()),
    )
    conn.commit()
    return state


def clear_filters(token_record: Dict[str, Any], factor_key: Optional[str] = None) -> Dict[str, Any]:
    state = get_filter_state(token_record)
    if factor_key:
        state.pop(factor_key, None)
    else:
        state = {}
    conn = get_conn()
    conn.execute(
        "INSERT INTO agent_filters(owner_key, filters_json, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(owner_key) DO UPDATE SET filters_json = excluded.filters_json,"
        " updated_at = excluded.updated_at",
        (owner_key(token_record), json.dumps(state, ensure_ascii=False), _now_iso()),
    )
    conn.commit()
    return state


def merge_params(stored: Dict[str, Any], explicit: Dict[str, Any]) -> Dict[str, Any]:
    """合并存量筛选条件与当次显式参数：显式参数优先。"""
    merged = dict(stored or {})
    for key, value in (explicit or {}).items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged
