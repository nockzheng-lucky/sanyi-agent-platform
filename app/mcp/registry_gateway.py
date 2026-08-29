"""平台内直连因子注册表的 MCP gateway。

供 FastAPI 的远程 MCP 端点使用：请求已经通过 X-API-Token 鉴权，
这里直接把身份转给 FactorRegistry，不需要再走一次 HTTP 自调用。
"""
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from ..factor_registry import registry
from .client import PlatformAPIError


class RegistryFactorGateway:
    def __init__(self, token_record: Dict[str, Any]) -> None:
        self.token_record = token_record

    async def list_factors(self) -> List[Dict[str, Any]]:
        return registry.descriptors(for_record=self.token_record)

    async def evaluate_factor(
        self,
        factor_key: str,
        params: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return await registry.evaluate(
                token_record=self.token_record,
                factor_key=factor_key,
                params=params,
                request_id=request_id,
            )
        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                message = str(detail.get("message") or exc.__class__.__name__)
                code = int(detail.get("code") or exc.status_code)
            else:
                message = str(detail)
                code = exc.status_code
            raise PlatformAPIError(code, message)
