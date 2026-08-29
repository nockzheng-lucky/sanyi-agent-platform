"""用户级通知通道（Pushplus）。

token 属于用户本人，按 user_id 加密存储；推送 worker 根据订阅归属人
读取对应通道，不使用全局 token。
"""
from typing import Any, Dict, Optional

from .accounts import decrypt_secret, encrypt_secret
from .db import _now_iso, get_conn

PROVIDER_PUSHPLUS = "pushplus"


def mask_token(raw: str) -> str:
    raw = (raw or "").strip()
    if len(raw) <= 10:
        return raw[:2] + "****" + raw[-2:]
    return raw[:6] + "****" + raw[-4:]


def set_pushplus_token(user_id: int, token: str) -> Dict[str, Any]:
    token = (token or "").strip()
    if not token:
        raise ValueError("Pushplus token 不能为空")
    now = _now_iso()
    conn = get_conn()
    conn.execute(
        "INSERT INTO user_push_channels(user_id, provider, token_encrypted, enabled, created_at, updated_at)"
        " VALUES (?, ?, ?, 1, ?, ?)"
        " ON CONFLICT(user_id, provider) DO UPDATE SET token_encrypted = excluded.token_encrypted,"
        " enabled = 1, updated_at = excluded.updated_at",
        (user_id, PROVIDER_PUSHPLUS, encrypt_secret(token), now, now),
    )
    conn.commit()
    return get_pushplus_channel(user_id)


def get_pushplus_channel(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM user_push_channels WHERE user_id = ? AND provider = ? AND enabled = 1",
        (user_id, PROVIDER_PUSHPLUS),
    ).fetchone()
    if row is None:
        return None
    rec = dict(row)
    raw = decrypt_secret(rec["token_encrypted"]) or ""
    return {
        "id": rec["id"],
        "provider": rec["provider"],
        "tokenMasked": mask_token(raw),
        "enabled": bool(rec["enabled"]),
        "createdAt": rec["created_at"],
        "updatedAt": rec["updated_at"],
    }


def get_pushplus_token(user_id: int) -> Optional[str]:
    conn = get_conn()
    row = conn.execute(
        "SELECT token_encrypted FROM user_push_channels"
        " WHERE user_id = ? AND provider = ? AND enabled = 1",
        (user_id, PROVIDER_PUSHPLUS),
    ).fetchone()
    if row is None:
        return None
    return decrypt_secret(row["token_encrypted"])


def delete_pushplus_token(user_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM user_push_channels WHERE user_id = ? AND provider = ?",
        (user_id, PROVIDER_PUSHPLUS),
    )
    conn.commit()
    return cur.rowcount > 0
