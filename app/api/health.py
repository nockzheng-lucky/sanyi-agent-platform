from fastapi import APIRouter

from ..config import SYSTEM_NAME

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health():
    return {"code": 0, "message": "ok", "data": {"system": SYSTEM_NAME, "status": "up"}}
