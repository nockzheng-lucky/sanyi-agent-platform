import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .agent_trade_bridge import AgentTradeBridge
from .agent_trade_control import router as agent_trade_router
from .api import admin, auth, chat, factors, health, keys, mcp, notifications, signal_subscriptions, signals, subscription, tokens
from .config import AGENT_TRADE_ENABLED, PUSHPLUS_ENABLED, SIGNAL_POLL_ENABLED, SYSTEM_NAME
from .db import authenticate, create_session, init_db
from .engine.poller import SignalPoller
from .engine.subscription_pusher import SubscriptionPusher
from .signal_notification_feed import SignalNotificationRecorder


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    poller_task = None
    pusher_task = None
    agent_trade_task = None
    notification_feed_task = None
    if SIGNAL_POLL_ENABLED:
        poller = SignalPoller()
        poller_task = asyncio.create_task(poller.run())
    if PUSHPLUS_ENABLED:
        pusher = SubscriptionPusher()
        pusher_task = asyncio.create_task(pusher.run())
    if AGENT_TRADE_ENABLED:
        agent_trade_task = asyncio.create_task(AgentTradeBridge().run())
    notification_feed_task = asyncio.create_task(SignalNotificationRecorder().run())
    try:
        yield
    finally:
        for task in (poller_task, pusher_task, agent_trade_task, notification_feed_task):
            if task is not None:
                task.cancel()
        for task in (poller_task, pusher_task, agent_trade_task, notification_feed_task):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass


app = FastAPI(title=SYSTEM_NAME, version="0.1.0", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        body = detail
    else:
        body = {"code": exc.status_code, "message": str(detail), "data": None}
    return JSONResponse(status_code=exc.status_code, content=body)


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(notifications.router)
app.include_router(admin.router)
app.include_router(keys.router)
app.include_router(subscription.router)
app.include_router(factors.router)
app.include_router(signals.router)
app.include_router(tokens.router)
app.include_router(mcp.router)
app.include_router(signal_subscriptions.router)
app.include_router(agent_trade_router)
app.include_router(chat.router)


@app.get("/")
async def index():
    return RedirectResponse(url="/chat")


_web_dir = Path(__file__).resolve().parent / "web" / "static"


@app.get("/login", include_in_schema=False)
async def login_page():
    return FileResponse(_web_dir / "login.html", headers={"Cache-Control": "no-store"})


@app.get("/register", include_in_schema=False)
async def register_page():
    return FileResponse(_web_dir / "register.html", headers={"Cache-Control": "no-store"})


@app.get("/keys", include_in_schema=False)
async def keys_page():
    return FileResponse(_web_dir / "keys.html", headers={"Cache-Control": "no-store"})


@app.get("/factors", include_in_schema=False)
async def factors_page():
    return FileResponse(_web_dir / "factors.html", headers={"Cache-Control": "no-store"})


@app.get("/crypto", include_in_schema=False)
async def crypto_factors_page():
    return FileResponse(_web_dir / "factors.html", headers={"Cache-Control": "no-store"})


@app.get("/admin", include_in_schema=False)
async def admin_page():
    return FileResponse(_web_dir / "admin.html", headers={"Cache-Control": "no-store"})


@app.get("/agreement", include_in_schema=False)
async def agreement_page():
    return FileResponse(_web_dir / "agreement.html", headers={"Cache-Control": "no-store"})


@app.get("/privacy", include_in_schema=False)
async def privacy_page():
    return FileResponse(_web_dir / "privacy.html", headers={"Cache-Control": "no-store"})


@app.get("/subscription", include_in_schema=False)
async def subscription_page():
    return FileResponse(_web_dir / "subscription.html", headers={"Cache-Control": "no-store"})


@app.get("/notifications", include_in_schema=False)
async def notifications_page():
    return FileResponse(_web_dir / "notifications.html", headers={"Cache-Control": "no-store"})


@app.get("/strategies", include_in_schema=False)
async def strategies_page():
    return FileResponse(_web_dir / "strategies.html", headers={"Cache-Control": "no-store"})


@app.get("/chat", include_in_schema=False)
async def chat_entry(request: Request, token: Optional[str] = None):
    """处理 /chat?token=sk-... 快捷登录。

    令牌只用于换服务端会话，随后 303 跳转到无 token 的 /chat，
    避免令牌长期留在地址栏、浏览器历史和截图里。
    """
    if token:
        record = authenticate(token)
        if record is not None:
            session_raw = create_session(record["id"], ttl_hours=12)
            response = RedirectResponse(url="/chat", status_code=303)
            chat.set_session_cookie(response, session_raw, 12)
            response.headers["Cache-Control"] = "no-store"
            return response
    return FileResponse(_web_dir / "index.html", headers={"Cache-Control": "no-store"})
app.mount("/chat", StaticFiles(directory=str(_web_dir), html=True), name="chat-static")
