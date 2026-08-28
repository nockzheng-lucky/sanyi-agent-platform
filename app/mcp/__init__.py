"""MCP 接入层：让用户自己的 Agent 通过标准 MCP 协议使用三易因子。

当前实现是 stdio JSON-RPC 服务，官方 MCP SDK 可用后可以替换为
Streamable HTTP / SDK 版本；工具契约与 REST 层完全一致。
"""
from .client import PlatformAPIError, PlatformClient
from .server import MCPStdioServer

__all__ = ["PlatformAPIError", "PlatformClient", "MCPStdioServer"]
