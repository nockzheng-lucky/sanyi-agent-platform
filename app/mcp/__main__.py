"""sanyi-mcp 命令行入口。

用法：
  # 本地联调
  SANYI_BASE_URL=http://127.0.0.1:8100 SANYI_API_TOKEN=sk-sanyi-... \
      python -m app.mcp

  # 生产平台（推荐从环境变量读取，不要把 Key 写进 shell 历史）
  SANYI_BASE_URL=https://signal.shhghf.com \
  SANYI_API_TOKEN=sk-sanyi-... \
      python -m app.mcp --stdio

MCP stdio transport 规定 stdout 只能出现 JSON-RPC 消息，因此本命令
不做启动横幅；诊断信息走 stderr。
"""
import argparse
import asyncio
import os
import sys
from typing import Optional

from .client import PlatformClient
from .server import MCP_SERVER_NAME, MCP_SERVER_VERSION, MCPStdioServer, run_stdio


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sanyi-mcp",
        description="三易信号平台 MCP stdio 服务（sanyi_list_factors / sanyi_evaluate_factor）",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="三易平台基础 URL；默认取 SANYI_BASE_URL，再回退 http://127.0.0.1:8100",
    )
    parser.add_argument(
        "--token",
        default="",
        help="平台 Key（sk-sanyi-...）；默认取 SANYI_API_TOKEN。只显示一次，请勿提交到仓库",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0,
        help="HTTP 超时秒数；默认取 SANYI_HTTP_TIMEOUT，再回退 30",
    )
    parser.add_argument("--stdio", action="store_true", default=True, help=argparse.SUPPRESS)
    parser.add_argument("--check", action="store_true", help="连接平台并列出因子后退出（诊断用）")
    parser.add_argument(
        "--version",
        action="version",
        version="%s %s" % (MCP_SERVER_NAME, MCP_SERVER_VERSION),
    )
    return parser


async def _check(client: PlatformClient) -> int:
    try:
        factors = await client.list_factors()
    except Exception as exc:
        print("MCP check failed: %s" % exc, file=sys.stderr)
        return 1
    print(
        "MCP check ok: %d factor(s) available: %s"
        % (len(factors), ", ".join(str(f.get("factorKey", "?")) for f in factors)),
        file=sys.stderr,
    )
    return 0


def main(argv: Optional[list] = None) -> None:
    args = build_parser().parse_args(argv)

    base_url = args.base_url or _env("SANYI_BASE_URL", "http://127.0.0.1:8100")
    token = args.token or _env("SANYI_API_TOKEN")
    try:
        timeout = args.timeout or float(_env("SANYI_HTTP_TIMEOUT", "30"))
    except ValueError:
        timeout = 30.0

    try:
        client = PlatformClient(base_url=base_url, api_token=token, timeout_seconds=timeout)
    except ValueError as exc:
        print("sanyi-mcp config error: %s" % exc, file=sys.stderr)
        raise SystemExit(2)

    if args.check:
        raise SystemExit(asyncio.run(_check(client)))

    server = MCPStdioServer(client)
    try:
        run_stdio(server)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
