from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api import chat, factors, health, tokens
from .config import SYSTEM_NAME
from .db import authenticate, create_session, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


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
app.include_router(factors.router)
app.include_router(tokens.router)
app.include_router(chat.router)


@app.get("/")
async def index():
    return RedirectResponse(url="/chat")


_web_dir = Path(__file__).resolve().parent / "web" / "static"


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
