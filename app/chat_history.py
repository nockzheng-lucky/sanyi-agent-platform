"""服务端 Agent 会话历史。

按 owner_key 存储最近 N 轮 user/assistant 消息。
页面 Agent 每次只提交最新一条 user 消息，由后端合并历史后执行。
"""
from typing import Any, Dict, List

from .agent.filter_store import owner_key
from .db import _now_iso, get_conn

HISTORY_LIMIT = 50


def list_chat_history(owner: str, limit: int = HISTORY_LIMIT) -> List[Dict[str, str]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content FROM chat_history WHERE owner_key = ? ORDER BY id DESC LIMIT ?",
        (owner, limit),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def append_chat_messages(owner: str, messages: List[Dict[str, Any]]) -> None:
    now = _now_iso()
    conn = get_conn()
    for message in messages:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        if role not in ("user", "assistant") or not content:
            continue
        conn.execute(
            "INSERT INTO chat_history(owner_key, role, content, created_at, is_pending)"
            " VALUES (?, ?, ?, ?, 0)",
            (owner, role, content, now),
        )
    conn.commit()
    _prune_history(owner)


def update_pending_assistant(owner: str, content: str) -> None:
    """增量保存正在生成的回答。

    页面切走/刷新时，未完成的回答也能从历史里恢复出来，不会被丢掉。
    """
    content = str(content or "")
    if not content:
        return
    now = _now_iso()
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM chat_history WHERE owner_key = ? AND role = 'assistant'"
        " AND is_pending = 1 ORDER BY id DESC LIMIT 1",
        (owner,),
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE chat_history SET content = ?, created_at = ? WHERE id = ?",
            (content, now, row["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO chat_history(owner_key, role, content, created_at, is_pending)"
            " VALUES (?, 'assistant', ?, ?, 1)",
            (owner, content, now),
        )
    conn.commit()


def finalize_pending_assistant(owner: str) -> None:
    """把最后一次未完成的回答标记为正常历史。"""
    conn = get_conn()
    conn.execute(
        "UPDATE chat_history SET is_pending = 0 WHERE owner_key = ? AND is_pending = 1",
        (owner,),
    )
    conn.commit()


def _prune_history(owner: str) -> None:
    conn = get_conn()
    conn.execute(
        "DELETE FROM chat_history WHERE owner_key = ? AND id NOT IN ("
        " SELECT id FROM chat_history WHERE owner_key = ? ORDER BY id DESC LIMIT ?"
        ")",
        (owner, owner, HISTORY_LIMIT),
    )
    conn.commit()


def clear_chat_history(owner: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM chat_history WHERE owner_key = ?", (owner,))
    conn.commit()


def owner_for_record(token_record: Dict[str, Any]) -> str:
    return owner_key(token_record)
