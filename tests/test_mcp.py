import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from app.mcp.client import PlatformClient
from app.mcp.server import MCPStdioServer

FACTOR_DESCRIPTOR = {
    "factorKey": "dimen_gate_signal",
    "name": "地门信号",
    "description": "门信号",
    "paramsSchema": {"type": "object", "properties": {}},
    "outputSchema": {"type": "object", "properties": {}},
    "cost": 0,
    "cacheSeconds": 0,
    "riskNote": "仅研究观察，不构成投资建议",
    "status": "active",
    "tags": ["dimen_gate"],
}

EVALUATE_RESULT = {
    "factorKey": "dimen_gate_signal",
    "signal": "NONE",
    "score": 0,
    "summary": "当前没有新信号",
    "generatedAt": "2026-08-28T02:00:00+00:00",
    "details": {"events": []},
    "riskNote": "仅研究观察，不构成投资建议",
}


def _platform_handler(request: httpx.Request) -> httpx.Response:
    assert request.headers.get("X-API-Token") == "sk-sanyi-test-token"
    if request.method == "GET" and request.url.path == "/api/v1/factors":
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": {"factors": [FACTOR_DESCRIPTOR]}})
    if request.method == "POST" and request.url.path == "/api/v1/factors/evaluate":
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": EVALUATE_RESULT})
    return httpx.Response(404, json={"code": 404, "message": "not found", "data": None})


def _unauthorized_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401, json={"code": 401, "message": "令牌无效", "data": None})


@pytest.mark.asyncio
async def test_mcp_initialize_tools_list_and_call():
    client = PlatformClient(
        base_url="http://platform.test",
        api_token="sk-sanyi-test-token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(_platform_handler)),
    )
    server = MCPStdioServer(client)

    init = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
    )
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"]["tools"]["listChanged"] is False

    # notification 不回包
    assert await server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.initialized is True

    tools = await server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [tool["name"] for tool in tools["result"]["tools"]]
    assert names == ["sanyi_list_factors", "sanyi_evaluate_factor"]

    listed = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "sanyi_list_factors", "arguments": {}},
        }
    )
    assert "dimen_gate_signal" in listed["result"]["content"][0]["text"]

    evaluated = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "sanyi_evaluate_factor",
                "arguments": {"factorKey": "dimen_gate_signal", "params": {"limit": 2}},
            },
        }
    )
    text = evaluated["result"]["content"][0]["text"]
    assert json.loads(text)["factorKey"] == "dimen_gate_signal"
    assert evaluated["result"]["structuredContent"]["signal"] == "NONE"


@pytest.mark.asyncio
async def test_mcp_tool_error_uses_is_error_result():
    client = PlatformClient(
        base_url="http://platform.test",
        api_token="sk-sanyi-test-token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(_unauthorized_handler)),
    )
    server = MCPStdioServer(client)
    resp = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "sanyi_list_factors", "arguments": {}},
        }
    )
    assert "error" not in resp
    assert resp["result"]["isError"] is True
    assert "code=401" in resp["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_mcp_invalid_params_unknown_method_and_parse_error():
    client = PlatformClient(
        base_url="http://platform.test",
        api_token="sk-sanyi-test-token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(_platform_handler)),
    )
    server = MCPStdioServer(client)

    resp = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "sanyi_evaluate_factor", "arguments": {"params": {}}},
        }
    )
    assert resp["error"]["code"] == -32602

    resp = await server.handle_message({"jsonrpc": "2.0", "id": 9, "method": "no/such"})
    assert resp["error"]["code"] == -32601

    resp = await server.handle_line("{ not json")
    assert resp["error"]["code"] == -32700


@pytest.mark.asyncio
async def test_mcp_protocol_version_falls_back_to_latest():
    client = PlatformClient(
        base_url="http://platform.test",
        api_token="sk-sanyi-test-token",
        client=httpx.AsyncClient(transport=httpx.MockTransport(_platform_handler)),
    )
    server = MCPStdioServer(client)
    resp = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "initialize",
            "params": {"protocolVersion": "2099-01-01", "capabilities": {}, "clientInfo": {}},
        }
    )
    assert resp["result"]["protocolVersion"] == "2025-06-18"


def test_remote_mcp_endpoint_flow(client, token_headers):
    resp = client.get("/api/v1/mcp", headers=token_headers)
    assert resp.status_code == 405

    resp = client.post("/api/v1/mcp", headers=token_headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {}}})
    assert resp.status_code == 200
    assert resp.json()["result"]["serverInfo"]["name"] == "sanyi-agent-platform"
    session_id = resp.headers["mcp-session-id"]

    session_headers = dict(token_headers)
    session_headers["Mcp-Session-Id"] = session_id
    resp = client.post("/api/v1/mcp", headers=session_headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert resp.status_code == 200
    assert [t["name"] for t in resp.json()["result"]["tools"]] == ["sanyi_list_factors", "sanyi_evaluate_factor"]

    resp = client.post(
        "/api/v1/mcp",
        headers=session_headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "sanyi_evaluate_factor",
                "arguments": {"factorKey": "dimen_gate_signal", "params": {"limit": 1}},
            },
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert json.loads(result["content"][0]["text"])["factorKey"] == "dimen_gate_signal"

    resp = client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}})
    assert resp.status_code == 401

    resp = client.post(
        "/api/v1/mcp",
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert resp.status_code == 202
    assert resp.content == b""


# ── stdio 端到端：真实子进程 + 本地 HTTP 假平台 ─────────────────


class _FakePlatformHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _respond(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        assert self.headers.get("X-API-Token") == "sk-sanyi-stdio"
        self._respond({"code": 0, "message": "ok", "data": {"factors": [FACTOR_DESCRIPTOR]}})

    def do_POST(self):
        assert self.headers.get("X-API-Token") == "sk-sanyi-stdio"
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b"{}"
        payload = json.loads(body.decode("utf-8"))
        assert payload["factorKey"] == "dimen_gate_signal"
        self._respond({"code": 0, "message": "ok", "data": EVALUATE_RESULT})


def test_mcp_stdio_end_to_end():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakePlatformHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = "http://127.0.0.1:%s" % httpd.server_address[1]

    lines = [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {}},
            }
        ),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "sanyi_evaluate_factor",
                    "arguments": {"factorKey": "dimen_gate_signal", "params": {"limit": 1}},
                },
            }
        ),
    ]
    env = os.environ.copy()
    env["SANYI_BASE_URL"] = base_url
    env["SANYI_API_TOKEN"] = "sk-sanyi-stdio"
    repo_root = Path(__file__).resolve().parents[1]

    proc = subprocess.Popen(
        [sys.executable, "-m", "app.mcp"],
        cwd=str(repo_root),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        out, err = proc.communicate("\n".join(lines) + "\n", timeout=15)
    finally:
        proc.kill()
        httpd.shutdown()
        httpd.server_close()

    assert proc.returncode == 0, err
    responses = [json.loads(line) for line in out.splitlines() if line.strip()]
    by_id = {item["id"]: item for item in responses}
    assert by_id[1]["result"]["serverInfo"]["name"] == "sanyi-agent-platform"
    assert [t["name"] for t in by_id[2]["result"]["tools"]] == [
        "sanyi_list_factors",
        "sanyi_evaluate_factor",
    ]
    assert json.loads(by_id[3]["result"]["content"][0]["text"])["factorKey"] == "dimen_gate_signal"
