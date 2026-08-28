"""远程 MCP 端点（Streamable HTTP 的 POST 子集）。

用户自己的 Agent 也可以直接 POST JSON-RPC 到 /api/v1/mcp：
- 鉴权沿用 X-API-Token / Authorization: Bearer；
- initialize 返回 Mcp-Session-Id，后续请求携带即可复用会话；
- 当前不提供 SSE 流，GET 返回 405；需要流的客户端请用本地 sanyi-mcp。

stdin/stdio 本地代理见 app/mcp/__main__.py。
"""
import secrets
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse

from ..mcp.registry_gateway import RegistryFactorGateway
from ..mcp.server import MCPStdioServer
from ..security import require_token

router = APIRouter(tags=["mcp"])

_SESSION_TTL_SECONDS = 3600.0
# session_id -> (expires_at_monotonic, actor_key, server)
_SESSIONS: Dict[str, Tuple[float, Tuple[str, int], MCPStdioServer]] = {}


def _actor_key(token: Dict[str, Any]) -> Tuple[str, int]:
    return (str(token.get("_table") or "tokens"), int(token["id"]))


def _cleanup_sessions() -> None:
    now = time.monotonic()
    stale = [sid for sid, (expires, _key, _server) in _SESSIONS.items() if expires <= now]
    for sid in stale:
        _SESSIONS.pop(sid, None)


def _session_error(payload: Dict[str, Any]) -> JSONResponse:
    body = {
        "jsonrpc": "2.0",
        "id": payload.get("id"),
        "error": {"code": -32001, "message": "MCP session expired or invalid, please re-initialize"},
    }
    return JSONResponse(body, status_code=404)


@router.get("/api/v1/mcp")
async def mcp_get() -> JSONResponse:
    """MCP 规范允许 POST-only 服务；本平台暂不提供 SSE 流。"""
    return JSONResponse(
        {
            "code": 405,
            "message": "MCP endpoint only supports POST JSON-RPC; use local sanyi-mcp if SSE is required",
            "data": None,
        },
        status_code=405,
    )


@router.post("/api/v1/mcp")
async def mcp_post(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    token: Dict[str, Any] = Depends(require_token),
):
    _cleanup_sessions()
    method = payload.get("method")
    actor_key = _actor_key(token)
    session_id = (request.headers.get("Mcp-Session-Id") or "").strip()

    server: Optional[MCPStdioServer] = None
    if session_id:
        entry = _SESSIONS.get(session_id)
        if entry is not None and entry[0] > time.monotonic() and entry[1] == actor_key:
            server = entry[2]
        else:
            _SESSIONS.pop(session_id, None)
            if method != "initialize":
                return _session_error(payload)

    if server is None:
        server = MCPStdioServer(RegistryFactorGateway(token))

    message = await server.handle_message(payload)
    if message is None:
        # JSON-RPC notification：202 + 空 body。
        return Response(status_code=202)

    response = JSONResponse(message)
    if method == "initialize":
        new_session_id = secrets.token_urlsafe(24)
        _SESSIONS[new_session_id] = (
            time.monotonic() + _SESSION_TTL_SECONDS,
            actor_key,
            server,
        )
        response.headers["Mcp-Session-Id"] = new_session_id
    return response
