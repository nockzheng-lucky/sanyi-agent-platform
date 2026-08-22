"""手机号注册/登录、短信验证码、用户会话、用户 Key 生命周期。"""
import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken

from .config import PHONE_HASH_SECRET, SMS_MOCK
from .db import _now_iso, get_conn

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
PBKDF2_ITERATIONS = 200000


# ── 手机号 ──────────────────────────────────────────────


def normalize_phone(phone: str) -> Optional[str]:
    value = str(phone or "").strip().replace(" ", "").replace("-", "")
    if value.startswith("+86"):
        value = value[3:]
    if value.startswith("0086"):
        value = value[4:]
    return value if PHONE_RE.match(value) else None


def phone_hash(phone: str) -> str:
    return hmac.new(PHONE_HASH_SECRET.encode("utf-8"), phone.encode("utf-8"), hashlib.sha256).hexdigest()


def mask_phone(phone: str) -> str:
    return phone[:3] + "****" + phone[-4:]


# ── 密码 ──────────────────────────────────────────────


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=32
    )
    return "pbkdf2$%d$%s$%s" % (PBKDF2_ITERATIONS, salt.hex(), digest.hex())


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, iterations_s, salt_s, digest_s = encoded.split("$")
        salt = bytes.fromhex(salt_s)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations_s), dklen=32
        )
        return hmac.compare_digest(digest.hex(), digest_s)
    except Exception:
        return False


# ── 用户 ──────────────────────────────────────────────


def create_user(phone: str, password: str, agreement_version: str = "2026-08-21-placeholder") -> Dict[str, Any]:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO users(phone_hash, phone_masked, password_hash, status,"
        " agreement_version, created_at) VALUES (?, ?, ?, 'active', ?, ?)",
        (phone_hash(phone), mask_phone(phone), hash_password(password), agreement_version, _now_iso()),
    )
    conn.commit()
    return {"id": cur.lastrowid, "phoneMasked": mask_phone(phone)}


def find_user_by_phone(phone: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE phone_hash = ?", (phone_hash(phone),)).fetchone()
    return dict(row) if row else None


def touch_user_login(user_id: int) -> None:
    conn = get_conn()
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now_iso(), user_id))
    conn.commit()


# ── 短信验证码（MOCK 优先）────────────────────────────


def create_sms_code(phone: str, purpose: str = "register") -> Tuple[str, str]:
    """创建 6 位验证码。返回 (掩码说明, debugCode 或 空字符串)。"""
    conn = get_conn()
    latest = conn.execute(
        "SELECT created_at FROM sms_codes WHERE phone_hash = ? AND purpose = ?"
        " ORDER BY id DESC LIMIT 1",
        (phone_hash(phone), purpose),
    ).fetchone()
    if latest:
        created = datetime.fromisoformat(latest["created_at"])
        if (datetime.now(timezone.utc) - created).total_seconds() < 60:
            raise PermissionError("验证码发送过于频繁，请 60 秒后再试")
    code = "%06d" % secrets.randbelow(1000000)
    code_hash = hmac.new(PHONE_HASH_SECRET.encode("utf-8"), code.encode("utf-8"), hashlib.sha256).hexdigest()
    conn.execute(
        "INSERT INTO sms_codes(phone_hash, purpose, code_hash, expires_at, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            phone_hash(phone),
            purpose,
            code_hash,
            (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            _now_iso(),
        ),
    )
    conn.commit()
    debug_code = code if SMS_MOCK else ""
    return mask_phone(phone), debug_code


def verify_sms_code(phone: str, code: str, purpose: str = "register") -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM sms_codes WHERE phone_hash = ? AND purpose = ? AND verified = 0"
        " ORDER BY id DESC LIMIT 1",
        (phone_hash(phone), purpose),
    ).fetchone()
    if row is None:
        return False
    rec = dict(row)
    if rec["attempts"] >= 5:
        return False
    if datetime.fromisoformat(rec["expires_at"]) < datetime.now(timezone.utc):
        return False
    expected = hmac.new(PHONE_HASH_SECRET.encode("utf-8"), str(code).encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, rec["code_hash"]):
        conn.execute("UPDATE sms_codes SET attempts = attempts + 1 WHERE id = ?", (rec["id"],))
        conn.commit()
        return False
    conn.execute("UPDATE sms_codes SET verified = 1 WHERE id = ?", (rec["id"],))
    conn.commit()
    return True


# ── 用户会话 ──────────────────────────────────────────


def create_user_session(user_id: int, user_agent: str = "", ip: str = "", ttl_hours: int = 12) -> str:
    raw = secrets.token_urlsafe(32)
    conn = get_conn()
    conn.execute(
        "INSERT INTO user_sessions(user_id, session_hash, user_agent, ip, expires_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            user_id,
            hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            user_agent[:200],
            ip[:64],
            (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat(),
            _now_iso(),
        ),
    )
    conn.commit()
    return raw


def get_user_id_by_session(raw: str) -> Optional[int]:
    if not raw:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM user_sessions WHERE session_hash = ?",
        (hashlib.sha256(raw.encode("utf-8")).hexdigest(),),
    ).fetchone()
    if row is None:
        return None
    rec = dict(row)
    try:
        if datetime.fromisoformat(rec["expires_at"]) < datetime.now(timezone.utc):
            return None
    except ValueError:
        return None
    return rec["user_id"]


def delete_user_session(raw: str) -> None:
    if not raw:
        return
    conn = get_conn()
    conn.execute(
        "DELETE FROM user_sessions WHERE session_hash = ?",
        (hashlib.sha256(raw.encode("utf-8")).hexdigest(),),
    )
    conn.commit()


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


# ── 用户 Key ──────────────────────────────────────────


def _fernet() -> Fernet:
    key_material = hashlib.sha256(("key-enc:" + PHONE_HASH_SECRET).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key_material))


def _key_active(rec: Dict[str, Any]) -> bool:
    if rec.get("status") != "active":
        return False
    if rec.get("expires_at"):
        try:
            if datetime.fromisoformat(rec["expires_at"]) < datetime.now(timezone.utc):
                return False
        except ValueError:
            return False
    return True


def issue_user_key(
    user_id: int,
    name: str,
    expires_in_days: Optional[int] = None,
    allow_ips: str = "",
    rate_limit_per_min: int = 60,
    quota_total: int = -1,
) -> Dict[str, Any]:
    raw = "sk-sanyi-" + secrets.token_urlsafe(24)
    expires_at = None
    if expires_in_days:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=int(expires_in_days))).isoformat()
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO user_keys(user_id, name, token_hash, token_prefix, token_encrypted,"
        " status, quota_total, rate_limit_per_min, allow_ips, expires_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
        (
            user_id,
            name,
            hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            raw[:16],
            _fernet().encrypt(raw.encode("utf-8")).decode("ascii"),
            quota_total,
            rate_limit_per_min,
            allow_ips,
            expires_at,
            _now_iso(),
        ),
    )
    conn.commit()
    return {
        "id": cur.lastrowid,
        "token": raw,
        "name": name,
        "expiresAt": expires_at,
        "quotaTotal": quota_total,
        "rateLimitPerMin": rate_limit_per_min,
    }


def list_user_keys(user_id: int) -> List[Dict[str, Any]]:
    """用户自己的 Key 列表：只返回 active，删除后从列表消失。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM user_keys WHERE user_id = ? AND status = 'active' ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_user_key(user_id: int, key_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM user_keys WHERE id = ? AND user_id = ?", (key_id, user_id)
    ).fetchone()
    return dict(row) if row else None


def get_default_user_key(user_id: int) -> Optional[Dict[str, Any]]:
    for key in list_user_keys(user_id):
        if _key_active(key):
            return key
    return None


def revoke_user_key(user_id: int, key_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE user_keys SET status = 'revoked' WHERE id = ? AND user_id = ?",
        (key_id, user_id),
    )
    conn.commit()
    return cur.rowcount == 1


def reveal_user_key(user_id: int, key_id: int) -> Optional[str]:
    """用户本人点击眼睛时解密返回 Key 明文。Key 加密存储，默认列表只显示掩码。"""
    key = get_user_key(user_id, key_id)
    if key is None:
        return None
    encrypted = key.get("token_encrypted")
    if not encrypted:
        return None
    try:
        return _fernet().decrypt(encrypted.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return None


def rotate_user_key(user_id: int, key_id: int, name: str = "") -> Optional[Dict[str, Any]]:
    old = get_user_key(user_id, key_id)
    if old is None:
        return None
    created = issue_user_key(
        user_id,
        name or (old["name"] + "·轮换"),
        expires_in_days=None if not old.get("expires_at") else None,
        allow_ips=old.get("allow_ips") or "",
        rate_limit_per_min=old.get("rate_limit_per_min") or 60,
        quota_total=old.get("quota_total") or -1,
    )
    revoke_user_key(user_id, key_id)
    return created


def authenticate_user_key(raw: str) -> Optional[Dict[str, Any]]:
    if not raw or not raw.startswith("sk-"):
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM user_keys WHERE token_hash = ?",
        (hashlib.sha256(raw.strip().encode("utf-8")).hexdigest(),),
    ).fetchone()
    if row is None:
        return None
    rec = dict(row)
    if not _key_active(rec):
        return None
    rec["_table"] = "user_keys"
    rec["_key_id"] = rec["id"]
    rec["_user_id"] = rec["user_id"]
    return rec


def charge_user_key(key_id: int, cost: int) -> bool:
    if cost <= 0:
        return True
    conn = get_conn()
    cur = conn.execute(
        "UPDATE user_keys SET quota_used = quota_used + ?, last_used_at = ?"
        " WHERE id = ? AND (quota_total = -1 OR quota_used + ? <= quota_total)",
        (cost, _now_iso(), key_id, cost),
    )
    conn.commit()
    return cur.rowcount == 1


def user_key_sanitize(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": rec["id"],
        "name": rec["name"],
        "status": rec["status"],
        "tokenPrefix": rec.get("token_prefix"),
        "quotaTotal": rec.get("quota_total"),
        "quotaUsed": rec.get("quota_used"),
        "rateLimitPerMin": rec.get("rate_limit_per_min"),
        "allowIps": rec.get("allow_ips"),
        "expiresAt": rec.get("expires_at"),
        "lastUsedAt": rec.get("last_used_at"),
        "createdAt": rec.get("created_at"),
    }


# ── 订阅（支付确认 + 权益开启）───────────────────────────


PLANS = {
    "monthly": {"name": "月费版", "amount_cents": 9900, "period_days": 30},
}

# MOCK 礼品卡代码：真实礼品卡生成上线前用于测试。
MOCK_GIFT_CODES = {
    "MOCK30": 30,
    "MOCK90": 90,
    "MOCK365": 365,
}


def redeem_gift_code(user: Dict[str, Any], code: str) -> Dict[str, Any]:
    """兑换礼品卡代码。当前 MOCK 阶段只接受固定测试代码，天数从到期日继续累加。"""
    code = (code or "").strip().upper()
    days = MOCK_GIFT_CODES.get(code)
    if days is None:
        raise ValueError("礼品卡代码无效")
    now = datetime.now(timezone.utc)
    current = user.get("subscription_expires_at")
    base = None
    if current:
        try:
            parsed = datetime.fromisoformat(current)
            if parsed > now:
                base = parsed
        except ValueError:
            base = None
    base = base or now
    new_expires = base + timedelta(days=days)
    conn = get_conn()
    conn.execute(
        "UPDATE users SET subscription_plan = 'gift', subscription_expires_at = ? WHERE id = ?",
        (new_expires.isoformat(), user["id"]),
    )
    conn.execute(
        "INSERT INTO gift_card_redemptions(user_id, code_hash, plan, period_days,"
        " expires_before, expires_after, redeemed_at) VALUES (?, ?, 'gift', ?, ?, ?, ?)",
        (
            user["id"],
            hashlib.sha256(code.encode("utf-8")).hexdigest(),
            days,
            base.isoformat(),
            new_expires.isoformat(),
            _now_iso(),
        ),
    )
    conn.commit()
    return {"plan": "gift", "periodDays": days, "expiresAt": new_expires.isoformat()}


def is_subscription_active(user: Dict[str, Any]) -> bool:
    from .config import SUBSCRIPTION_REQUIRED

    if not SUBSCRIPTION_REQUIRED:
        return True
    expires = user.get("subscription_expires_at")
    if not expires:
        return False
    try:
        return datetime.fromisoformat(expires) > datetime.now(timezone.utc)
    except ValueError:
        return False


def create_subscription_request(user_id: int, plan: str, payment_note: str = "") -> Dict[str, Any]:
    plan_info = PLANS.get(plan)
    if plan_info is None:
        raise ValueError("未知订阅方案")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO subscription_requests(user_id, plan, amount_cents, period_days,"
        " status, payment_note, created_at) VALUES (?, ?, ?, ?, 'pending_payment', ?, ?)",
        (user_id, plan, plan_info["amount_cents"], plan_info["period_days"], payment_note[:500], _now_iso()),
    )
    conn.commit()
    return {"id": cur.lastrowid, "plan": plan_info["name"], "amountCents": plan_info["amount_cents"], "periodDays": plan_info["period_days"]}


def list_my_subscription_requests(user_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM subscription_requests WHERE user_id = ? ORDER BY id DESC LIMIT 20",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_all_subscription_requests() -> List[Dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT s.*, u.phone_masked FROM subscription_requests s"
        " JOIN users u ON u.id = s.user_id ORDER BY s.id DESC LIMIT 100"
    ).fetchall()
    return [dict(r) for r in rows]


def confirm_subscription_request(request_id: int, admin_user_id: int, note: str = "") -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM subscription_requests WHERE id = ?", (request_id,)
    ).fetchone()
    if row is None:
        return False
    rec = dict(row)
    if rec["status"] not in ("pending_payment",):
        return False
    now = datetime.now(timezone.utc)
    new_expires = now + timedelta(days=int(rec["period_days"] or 30))
    conn.execute(
        "UPDATE subscription_requests SET status = 'paid', paid_at = ?, admin_user_id = ?,"
        " admin_note = ? WHERE id = ?",
        (_now_iso(), admin_user_id, note[:500], request_id),
    )
    conn.execute(
        "UPDATE users SET subscription_plan = ?, subscription_expires_at = ? WHERE id = ?",
        (rec["plan"], new_expires.isoformat(), rec["user_id"]),
    )
    conn.commit()
    return True


def reject_subscription_request(request_id: int, admin_user_id: int, note: str = "") -> bool:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE subscription_requests SET status = 'rejected', admin_user_id = ?,"
        " admin_note = ? WHERE id = ? AND status = 'pending_payment'",
        (admin_user_id, note[:500], request_id),
    )
    conn.commit()
    return cur.rowcount == 1


# ── 管理端 ──────────────────────────────────────────────


def set_user_status(user_id: int, status: str) -> bool:
    if status not in ("active", "disabled"):
        return False
    conn = get_conn()
    cur = conn.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
    conn.commit()
    return cur.rowcount == 1


def set_user_role(user_id: int, role: str) -> bool:
    if role not in ("user", "admin"):
        return False
    conn = get_conn()
    cur = conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    conn.commit()
    return cur.rowcount == 1


def list_users_summary() -> List[Dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT u.id, u.phone_masked, u.status, u.role, u.agreement_version,"
        " u.created_at, u.last_login_at, COUNT(k.id) AS key_count"
        " FROM users u LEFT JOIN user_keys k ON k.user_id = u.id"
        " GROUP BY u.id ORDER BY u.id DESC LIMIT 200"
    ).fetchall()
    return [dict(r) for r in rows]


def list_all_keys() -> List[Dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT k.*, u.phone_masked FROM user_keys k JOIN users u ON u.id = k.user_id"
        " ORDER BY k.id DESC LIMIT 200"
    ).fetchall()
    return [dict(r) for r in rows]


def revoke_any_user_key(key_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("UPDATE user_keys SET status = 'revoked' WHERE id = ?", (key_id,))
    conn.commit()
    return cur.rowcount == 1
