"""鉴权、限流、令牌泄露防护。

- 业务 API：X-API-Token 或 Authorization: Bearer。
- 页面聊天：服务端会话 cookie，令牌不出现在前端内存/URL 之外的地方。
- 限流：原型用进程内滑动窗口；生产部署多副本时要换 Redis。
"""
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

from fastapi import Cookie, Depends, HTTPException, Request, status

from .accounts import authenticate_user_key, get_default_user_key, get_user, get_user_id_by_session
from .config import TOKEN_RATE_LIMIT_PER_MIN
from .db import authenticate, get_session_token_id

_RATE_WINDOW: Dict[Tuple[str, int], Deque[float]] = defaultdict(deque)
USER_COOKIE_NAME = "sanyi_user"


def extract_token(request: Request) -> Optional[str]:
    raw = request.headers.get("X-API-Token", "").strip()
    if raw:
        return raw
    auth = request.headers.get("Authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _auth_record(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    record = authenticate(raw)
    if record is not None:
        record["_table"] = "tokens"
        return record
    return authenticate_user_key(raw)


def require_token(request: Request) -> dict:
    record = _auth_record(extract_token(request))
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 401, "message": "令牌无效、已过期或额度已用尽", "data": None},
        )
    scope = str(record.get("_table") or "tokens")
    if not _rate_check(scope, record["id"], record.get("rate_limit_per_min") or TOKEN_RATE_LIMIT_PER_MIN):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": 429, "message": "请求过于频繁，请稍后再试", "data": None},
        )
    return record


def _rate_check(scope: str, token_id: int, limit: int) -> bool:
    now = time.monotonic()
    q = _RATE_WINDOW[(scope, token_id)]
    while q and now - q[0] > 60.0:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


async def get_session_token(
    sanyi_session: Optional[str] = Cookie(default=None),
) -> dict:
    """页面聊天会话鉴权。会话 ID 存 HttpOnly cookie。"""
    token_id = get_session_token_id(sanyi_session or "")
    if token_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 401, "message": "登录已失效，请重新输入令牌", "data": None},
        )
    from .db import get_token_record

    record = get_token_record(token_id)
    if record is None or record["status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 401, "message": "账号或令牌不可用", "data": None},
        )
    return record


async def get_user_session(
    sanyi_user: Optional[str] = Cookie(default=None),
) -> dict:
    """注册/登录后的用户会话鉴权。"""
    user_id = get_user_id_by_session(sanyi_user or "")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 401, "message": "登录已失效，请重新登录", "data": None},
        )
    user = get_user(user_id)
    if user is None or user["status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 401, "message": "账号不可用", "data": None},
        )
    return user


async def get_actor(
    request: Request,
    sanyi_user: Optional[str] = Cookie(default=None),
    sanyi_session: Optional[str] = Cookie(default=None),
) -> dict:
    """统一取调用身份：
    - X-API-Token/Bearer → 用户 Key 或兼容旧令牌；
    - 用户会话 cookie → 该用户当前默认 active Key（没有 Key 返回 403）；
    - 旧 /chat 令牌会话 → 兼容旧令牌。
    """
    record = _auth_record(extract_token(request))
    if record is not None:
        return record

    user_id = get_user_id_by_session(sanyi_user or "")
    if user_id is not None:
        user = get_user(user_id)
        if user is None or user["status"] != "active":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": 401, "message": "账号不可用", "data": None},
            )
        key = get_default_user_key(user_id)
        if key is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": 403, "message": "请先申请一个 Key", "data": None},
            )
        key["_table"] = "user_keys"
        key["_key_id"] = key["id"]
        key["_user_id"] = user_id
        return key

    return await get_session_token(sanyi_session)


async def get_token_or_session(
    request: Request,
    sanyi_user: Optional[str] = Cookie(default=None),
    sanyi_session: Optional[str] = Cookie(default=None),
) -> dict:
    """信号流等接口：页面走用户/令牌会话，外部 Agent 走 X-API-Token/Bearer。"""
    return await get_actor(request, sanyi_user=sanyi_user, sanyi_session=sanyi_session)


def require_admin(request: Request) -> None:
    """原型期的令牌签发接口保护。默认关闭，开启后需要 ADMIN_KEY。"""
    from .config import ADMIN_KEY, ENABLE_TOKEN_ISSUE_API

    if not ENABLE_TOKEN_ISSUE_API:
        raise HTTPException(status_code=404, detail={"code": 404, "message": "未启用", "data": None})
    if not ADMIN_KEY:
        raise HTTPException(status_code=500, detail={"code": 500, "message": "服务端未配置 ADMIN_KEY", "data": None})
    auth = request.headers.get("X-Admin-Key", "").strip()
    if auth != ADMIN_KEY:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "管理密钥无效", "data": None})
