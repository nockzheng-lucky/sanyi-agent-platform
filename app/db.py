"""SQLite 存储：令牌、用量、聊天会话。

这是原型实现，故意只依赖标准库。
后续要接支付/订阅/多用户时，把这一层替换为 New API 或正式数据库，
上层 API 契约保持不变（sk- 令牌 + X-API-Token / Authorization 鉴权）。
"""
import hashlib
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    token_prefix TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    quota_total INTEGER NOT NULL DEFAULT 0,   -- -1 表示不限量
    quota_used INTEGER NOT NULL DEFAULT 0,
    rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
    expires_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER NOT NULL,
    service TEXT NOT NULL,
    action TEXT NOT NULL,
    factor_key TEXT,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    request_id TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER NOT NULL,
    session_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_hash TEXT NOT NULL UNIQUE,
    phone_masked TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    agreement_version TEXT,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sms_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_hash TEXT NOT NULL,
    purpose TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    verified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    token_prefix TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    quota_total INTEGER NOT NULL DEFAULT -1,
    quota_used INTEGER NOT NULL DEFAULT 0,
    rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
    allow_ips TEXT,
    expires_at TEXT,
    last_used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    session_hash TEXT NOT NULL UNIQUE,
    user_agent TEXT,
    ip TEXT,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    factor_key TEXT NOT NULL,
    symbol TEXT NOT NULL,
    contract TEXT,
    source_kind TEXT,
    frequency TEXT NOT NULL,
    status TEXT NOT NULL,
    formation TEXT,
    gate_price REAL,
    current_price REAL,
    open_at TEXT,
    bar_time TEXT,
    summary TEXT,
    payload_json TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0,
    is_baseline INTEGER NOT NULL DEFAULT 0
);
"""

_local = threading.local()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(_SCHEMA)
    # 迁移旧库：加 is_baseline 列，并把存量行标记为 baseline（历史门不再当新信号展示）。
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(signal_events)").fetchall()]
    if "is_baseline" not in cols:
        conn.execute("ALTER TABLE signal_events ADD COLUMN is_baseline INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE signal_events SET is_baseline = 1")
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(signal_events)").fetchall()]
    if "contract" not in cols:
        conn.execute("ALTER TABLE signal_events ADD COLUMN contract TEXT")
    if "source_kind" not in cols:
        conn.execute("ALTER TABLE signal_events ADD COLUMN source_kind TEXT")
    usage_cols = [r["name"] for r in conn.execute("PRAGMA table_info(usage_logs)").fetchall()]
    if "user_id" not in usage_cols:
        conn.execute("ALTER TABLE usage_logs ADD COLUMN user_id INTEGER")
    if "key_id" not in usage_cols:
        conn.execute("ALTER TABLE usage_logs ADD COLUMN key_id INTEGER")
    conn.commit()


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_token(
    name: str,
    quota_total: int = 0,
    rate_limit_per_min: int = 60,
    expires_in_days: Optional[int] = None,
) -> Dict[str, Any]:
    """签发一个 sk- 令牌。只返回一次明文，之后库里只存哈希。"""
    init_db()
    raw = "sk-sanyi-" + secrets.token_urlsafe(24)
    prefix = raw[:16]
    expires_at = None
    if expires_in_days is not None:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO tokens(name, token_hash, token_prefix, quota_total, rate_limit_per_min, expires_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, _hash(raw), prefix, quota_total, rate_limit_per_min, expires_at, _now_iso()),
    )
    conn.commit()
    return {
        "id": cur.lastrowid,
        "token": raw,
        "name": name,
        "quota_total": quota_total,
        "rate_limit_per_min": rate_limit_per_min,
        "expires_at": expires_at,
    }


def authenticate(raw: str) -> Optional[Dict[str, Any]]:
    """校验令牌；令牌存哈希，日志/异常里不出现明文。"""
    if not raw or not raw.startswith("sk-"):
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM tokens WHERE token_hash = ?", (_hash(raw.strip()),)
    ).fetchone()
    if row is None:
        return None
    rec = dict(row)
    if rec["status"] != "active":
        return None
    if rec["expires_at"]:
        try:
            expires = datetime.fromisoformat(rec["expires_at"])
            if datetime.now(timezone.utc) > expires:
                return None
        except ValueError:
            return None
    if rec["quota_total"] != -1 and rec["quota_used"] >= rec["quota_total"]:
        return None
    return rec


def charge_after_success(token_id: int, cost: int) -> bool:
    """成功后扣额度。原子更新，扣不了返回 False。

    设计约定：因子/聊天只有成功返回结果后才扣费；元信息接口免费。
    """
    if cost <= 0:
        return True
    conn = get_conn()
    cur = conn.execute(
        "UPDATE tokens SET quota_used = quota_used + ?"
        " WHERE id = ? AND (quota_total = -1 OR quota_used + ? <= quota_total)",
        (cost, token_id, cost),
    )
    conn.commit()
    return cur.rowcount == 1


def log_usage(
    *,
    token_id: int,
    service: str,
    action: str,
    factor_key: Optional[str] = None,
    model: Optional[str] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost: int = 0,
    status: str = "ok",
    request_id: Optional[str] = None,
    detail: Optional[str] = None,
    user_id: Optional[int] = None,
    key_id: Optional[int] = None,
) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO usage_logs(token_id, service, action, factor_key, model,"
        " input_tokens, output_tokens, cost, status, request_id, detail, created_at,"
        " user_id, key_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            token_id,
            service,
            action,
            factor_key,
            model,
            input_tokens,
            output_tokens,
            cost,
            status,
            request_id,
            detail,
            _now_iso(),
            user_id,
            key_id,
        ),
    )
    conn.commit()


def create_session(token_id: int, ttl_hours: int = 12) -> str:
    """创建服务端会话；cookie 里只放随机会话 ID，不放用户令牌。"""
    init_db()
    raw = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
    conn = get_conn()
    conn.execute(
        "INSERT INTO chat_sessions(token_id, session_hash, expires_at, created_at)"
        " VALUES (?, ?, ?, ?)",
        (token_id, _hash(raw), expires_at, _now_iso()),
    )
    conn.commit()
    return raw


def get_session_token_id(raw: str) -> Optional[int]:
    if not raw:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM chat_sessions WHERE session_hash = ?", (_hash(raw),)
    ).fetchone()
    if row is None:
        return None
    rec = dict(row)
    try:
        if datetime.now(timezone.utc) > datetime.fromisoformat(rec["expires_at"]):
            return None
    except ValueError:
        return None
    return rec["token_id"]


def delete_session(raw: str) -> None:
    if not raw:
        return
    conn = get_conn()
    conn.execute("DELETE FROM chat_sessions WHERE session_hash = ?", (_hash(raw),))
    conn.commit()


def get_token_record(token_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM tokens WHERE id = ?", (token_id,)).fetchone()
    return dict(row) if row else None


def list_usage(token_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM usage_logs WHERE token_id = ? ORDER BY id DESC LIMIT ?",
        (token_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ── 信号事件 ──────────────────────────────────────────────


def upsert_signal_event(event: Dict[str, Any], is_baseline: bool = False) -> bool:
    """写入或刷新一条信号事件。返回 True 表示这是新事件（需要推送）。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, notified FROM signal_events WHERE event_id = ?", (event["event_id"],)
    ).fetchone()
    now = event.get("first_seen_at") or _now_iso()
    if row is None:
        conn.execute(
            "INSERT INTO signal_events(event_id, factor_key, symbol, contract, source_kind,"
            " frequency, status, formation, gate_price, current_price, open_at, bar_time,"
            " summary, payload_json, first_seen_at, last_seen_at, notified, is_baseline)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                event["event_id"],
                event["factor_key"],
                event["symbol"],
                event.get("contract"),
                event.get("source_kind"),
                event["frequency"],
                event["status"],
                event.get("formation"),
                event.get("gate_price"),
                event.get("current_price"),
                event.get("open_at"),
                event.get("bar_time"),
                event.get("summary"),
                event.get("payload_json"),
                now,
                now,
                1 if is_baseline else 0,
            ),
        )
        conn.commit()
        return True
    conn.execute(
        "UPDATE signal_events SET last_seen_at = ?, current_price = ?,"
        " payload_json = ? WHERE event_id = ?",
        (now, event.get("current_price"), event.get("payload_json"), event["event_id"]),
    )
    conn.commit()
    return False


def mark_signal_notified(event_id: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE signal_events SET notified = 1 WHERE event_id = ?", (event_id,))
    conn.commit()


def list_signal_events(
    frequencies: Optional[List[str]] = None,
    symbols: Optional[List[str]] = None,
    since_iso: Optional[str] = None,
    limit: int = 200,
    include_baseline: bool = True,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM signal_events WHERE 1=1"
    args: List[Any] = []
    if not include_baseline:
        sql += " AND is_baseline = 0"
    if frequencies:
        sql += " AND frequency IN (%s)" % ",".join("?" for _ in frequencies)
        args.extend(frequencies)
    if symbols:
        sql += " AND symbol IN (%s)" % ",".join("?" for _ in symbols)
        args.extend(symbols)
    if since_iso:
        sql += " AND first_seen_at >= ?"
        args.append(since_iso)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = get_conn().execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def latest_signal_id() -> int:
    row = get_conn().execute("SELECT MAX(id) AS m FROM signal_events").fetchone()
    return int(row["m"] or 0)
