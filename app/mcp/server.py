"""最小可用的 MCP stdio JSON-RPC 服务。

不依赖官方 mcp SDK（当前 Python 3.9 环境装不了新版 SDK），只实现
用户自己的 Agent 实际需要的三个能力：

- initialize / notifications/initialized
- tools/list
- tools/call（sanyi_list_factors / sanyi_evaluate_factor）

消息格式：stdin/stdout 每行一个 JSON-RPC 2.0 消息，与 MCP stdio
transport 兼容。平台调用仍然走 HTTPS REST，Key 只放在本进程环境中。
"""
import asyncio
import json
import sys
from typing import Any, Dict, List, Optional, Protocol

from .client import PlatformAPIError

MCP_SERVER_NAME = "sanyi-agent-platform"
MCP_SERVER_VERSION = "0.1.0"
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

class MethodNotFoundError(Exception):
    pass


class FactorGateway(Protocol):
    """MCPStdioServer 只依赖这两个方法，便于在 HTTP 代理与平台内直连间复用。"""

    async def list_factors(self) -> List[Dict[str, Any]]: ...

    async def evaluate_factor(
        self,
        factor_key: str,
        params: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]: ...


def tool_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "name": "sanyi_list_factors",
            "description": (
                "列出三易信号平台当前可用的全部因子（factorKey、名称、参数 schema、"
                "输出 schema、风险提示等元信息）。调用任何具体因子前先调用本工具确认。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "sanyi_evaluate_factor",
            "description": (
                "按 factorKey 执行三易因子查询。常用 factorKey：dimen_gate_signal"
                "（地门信号：地门开 / 地门形成·无动作门上，5m/15m/1h）。"
                "params 的具体字段以 sanyi_list_factors 返回的 paramsSchema 为准。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "factorKey": {
                        "type": "string",
                        "description": "因子唯一标识，例如 dimen_gate_signal。",
                    },
                    "params": {
                        "type": "object",
                        "description": "因子查询参数；按 paramsSchema 填充，可为空对象。",
                    },
                    "requestId": {
                        "type": "string",
                        "description": "可选。调用方生成的请求 ID，便于用量审计与排查。",
                    },
                },
                "required": ["factorKey"],
                "additionalProperties": False,
            },
        },
    ]


class MCPStdioServer:
    """处理单条 JSON-RPC 消息；stdin/stdout 传输由 run_stdio_async 完成。"""

    def __init__(self, client: FactorGateway) -> None:
        self.client = client
        self.protocol_version = DEFAULT_PROTOCOL_VERSION
        self.initialized = False

    # ── 入口 ────────────────────────────────────────────

    async def handle_line(self, raw_line: str) -> Optional[Dict[str, Any]]:
        line = (raw_line or "").strip()
        if not line:
            return None
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return self._error(None, -32700, "Parse error")
        if not isinstance(message, dict):
            return self._error(None, -32600, "Invalid Request: 仅支持单个 JSON-RPC 对象")
        return await self.handle_message(message)

    async def handle_message(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = message.get("method")
        has_id = "id" in message
        request_id = message.get("id")

        if not has_id:
            # JSON-RPC notification；MCP 的 initialized 等通知不回包。
            if method == "notifications/initialized":
                self.initialized = True
            return None

        if not isinstance(method, str) or not method:
            return self._error(request_id, -32600, "Invalid Request: method 缺失")
        try:
            result = await self._dispatch(method, message.get("params") or {})
        except MethodNotFoundError as exc:
            return self._error(request_id, -32601, str(exc))
        except (TypeError, ValueError) as exc:
            return self._error(request_id, -32602, "Invalid params: %s" % exc)
        except PlatformAPIError as exc:  # dispatch 阶段不应出现，兜底转 JSON-RPC 错误
            return self._error(request_id, -32000, exc.message)
        except Exception as exc:  # 不把内部细节泄露给客户端
            print("mcp internal error: %s" % exc.__class__.__name__, file=sys.stderr)
            return self._error(request_id, -32603, "Internal error")
        return self._result(request_id, result)

    # ── JSON-RPC 协议 ───────────────────────────────────

    @staticmethod
    def _result(request_id: Any, result: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    async def _dispatch(self, method: str, params: Any) -> Dict[str, Any]:
        if not isinstance(params, dict):
            params = {}

        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": tool_definitions()}
        if method == "tools/call":
            return await self._tools_call(params)
        if method == "prompts/list":
            return {"prompts": []}
        if method == "resources/list":
            return {"resources": []}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        raise MethodNotFoundError("Method not found: %s" % method)

    def _initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        requested = params.get("protocolVersion")
        if requested in SUPPORTED_PROTOCOL_VERSIONS:
            self.protocol_version = requested
        else:
            self.protocol_version = DEFAULT_PROTOCOL_VERSION
        self.initialized = True
        return {
            "protocolVersion": self.protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": MCP_SERVER_NAME,
                "version": MCP_SERVER_VERSION,
            },
            "instructions": (
                "三易信号平台 MCP。先调用 sanyi_list_factors 获取可用因子与参数；"
                "再调用 sanyi_evaluate_factor 查询信号。所有结果都必须展示信号时间"
                "（openAt/barTime）并转述 riskNote，不得把工具输出当作交易指令。"
            ),
        }

    async def _tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("tools/call 缺少 name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("tools/call 的 arguments 必须是对象")

        try:
            if name == "sanyi_list_factors":
                factors = await self.client.list_factors()
                return self._tool_result({"factors": factors})

            if name == "sanyi_evaluate_factor":
                factor_key = arguments.get("factorKey")
                if not isinstance(factor_key, str) or not factor_key:
                    raise ValueError("factorKey 不能为空")
                factor_params = arguments.get("params") or {}
                if not isinstance(factor_params, dict):
                    raise ValueError("params 必须是对象")
                result = await self.client.evaluate_factor(
                    factor_key=factor_key,
                    params=factor_params,
                    request_id=arguments.get("requestId") or None,
                )
                return self._tool_result(result)

            raise ValueError("Unknown tool: %s" % name)
        except PlatformAPIError as exc:
            # 按 MCP 约定：工具执行失败仍返回 tools/call 结果，并标记 isError。
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "三易平台调用失败（code=%s）：%s" % (exc.code, exc.message),
                    }
                ],
                "isError": True,
            }

    @staticmethod
    def _tool_result(payload: Dict[str, Any]) -> Dict[str, Any]:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        result: Dict[str, Any] = {
            "content": [{"type": "text", "text": text}],
        }
        # structuredContent 是可选增强字段；不支持结构化内容的客户端仍可用 text。
        result["structuredContent"] = payload
        return result


async def run_stdio_async(server: MCPStdioServer) -> None:
    """按行读取 stdin 的 JSON-RPC 消息，响应写回 stdout。"""
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        response = await server.handle_line(line)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()


def run_stdio(server: MCPStdioServer) -> None:
    asyncio.run(run_stdio_async(server))
