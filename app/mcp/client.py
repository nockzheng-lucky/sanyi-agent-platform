"""MCP 服务访问三易平台的 HTTP 客户端。

MCP stdio 进程运行在用户自己的 Agent 所在机器上，通过网络调用平台
REST API。Token 通过环境变量或 CLI 参数注入，不进入工具结果或日志。
"""
from typing import Any, Dict, List, Optional

import httpx


class PlatformAPIError(RuntimeError):
    """平台统一返回结构中 code != 0，或 HTTP 调用失败。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - 调试便利
        return "[%s] %s" % (self.code, self.message)


class PlatformClient:
    def __init__(
        self,
        base_url: str,
        api_token: str = "",
        timeout_seconds: float = 30.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        if not self.base_url:
            raise ValueError("SANYI_BASE_URL 不能为空")
        self.api_token = (api_token or "").strip()
        self.timeout_seconds = timeout_seconds
        self._client = client

    @property
    def headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "sanyi-mcp/0.1.0",
        }
        if self.api_token:
            headers["X-API-Token"] = self.api_token
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if not self.api_token:
            raise PlatformAPIError(401, "未配置 SANYI_API_TOKEN，无法访问三易平台")

        if self._client is not None:
            client = self._client
        else:
            client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds))

        try:
            response = await client.request(
                method,
                self.base_url + path,
                headers=self.headers,
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise PlatformAPIError(-1, "无法连接三易平台：%s" % exc.__class__.__name__)
        finally:
            # 注入的 client 由测试/调用方负责关闭；自己创建的用完即关。
            if self._client is None:
                await client.aclose()

        try:
            body = response.json()
        except ValueError:
            body = None

        if isinstance(body, dict) and "code" in body:
            code = body.get("code")
            try:
                code_int = int(code)
            except (TypeError, ValueError):
                code_int = response.status_code
            message = str(body.get("message") or "").strip() or "unknown error"
            if response.status_code < 400 and code_int == 0:
                return body.get("data")
            raise PlatformAPIError(code_int or response.status_code, message)

        if response.is_error:
            text = ""
            if isinstance(body, dict):
                text = str(body.get("message") or body.get("detail") or "")
            raise PlatformAPIError(
                response.status_code,
                text[:300] or "三易平台返回 HTTP %s" % response.status_code,
            )
        return body

    async def list_factors(self) -> List[Dict[str, Any]]:
        data = await self._request("GET", "/api/v1/factors")
        factors = (data or {}).get("factors") if isinstance(data, dict) else None
        return factors if isinstance(factors, list) else []

    async def evaluate_factor(
        self,
        factor_key: str,
        params: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "factorKey": factor_key,
            "params": params or {},
        }
        if request_id:
            payload["requestId"] = request_id
        data = await self._request("POST", "/api/v1/factors/evaluate", payload)
        return data if isinstance(data, dict) else {}
