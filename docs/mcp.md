# 三易信号平台 MCP 接入说明

本文说明如何把三易平台开放给用户自己的 Agent（Claude Desktop、Cursor、
Cline 等 MCP 客户端）。

平台三层开放方式：

- **REST**：`GET /api/v1/factors`、`POST /api/v1/factors/evaluate`，Header 使用 `X-API-Token`；
- **MCP**：平台远程端点 `POST /api/v1/mcp`，或本地代理命令 `sanyi-mcp`；
- **Skill**：每个因子的 Markdown 说明在 `docs/factors/`，供 Agent 自读自封装。

两种 MCP 接入方式：

| 方式 | 适用客户端 | 说明 |
|---|---|---|
| 远程端点 | 支持 Streamable HTTP / Remote MCP 的客户端 | `https://signal.shhghf.com/api/v1/mcp`，服务端已鉴权，无需本地装包 |
| 本地 stdio 代理 | Claude Desktop、Cursor、Cline 等 | 运行本仓库 `sanyi-mcp`，进程内通过 `SANYI_API_TOKEN` 调平台 REST |

## 1. 远程端点（Streamable HTTP POST 子集）

端点：`POST https://signal.shhghf.com/api/v1/mcp`

鉴权沿用 REST 契约：`X-API-Token: sk-sanyi-...` 或
`Authorization: Bearer sk-sanyi-...`。首次请求发送 `initialize`：

```bash
curl -sS -D /tmp/mcp-headers.txt https://signal.shhghf.com/api/v1/mcp \
  -H 'Content-Type: application/json' \
  -H 'X-API-Token: sk-sanyi-...' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "my-agent", "version": "1.0"}}
  }'
```

响应头会返回 `Mcp-Session-Id`，后续请求带上该 Header 即可复用会话：

```bash
curl -sS https://signal.shhghf.com/api/v1/mcp \
  -H 'Content-Type: application/json' \
  -H 'X-API-Token: sk-sanyi-...' \
  -H 'Mcp-Session-Id: <上一步拿到的值>' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

远程端点当前为 POST-only（GET 返回 405，不提供 SSE 流）；需要 SSE 的
MCP 客户端请使用下面的本地 stdio 代理，或直接接 REST 的
`/api/v1/signal-events/stream`。

## 2. 本地 stdio 代理：安装与快速验证

```bash
cd /path/to/sanyi-agent-platform
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
sanyi-mcp --version
```

验证 Key 与网络（`--check` 只调用免费的 `/api/v1/factors`，不会执行因子）：

```bash
sanyi-mcp --check \
  --base-url https://signal.shhghf.com \
  --token sk-sanyi-...
```

预期 stderr 输出：

```text
MCP check ok: 1 factor(s) available: dimen_gate_signal
```

## 3. 配置 MCP 客户端（本地 stdio）

### 3.1 Claude Desktop

在 `claude_desktop_config.json` 中增加：

```json
{
  "mcpServers": {
    "sanyi-signals": {
      "command": "/absolute/path/to/sanyi-agent-platform/.venv/bin/sanyi-mcp",
      "env": {
        "SANYI_BASE_URL": "https://signal.shhghf.com",
        "SANYI_API_TOKEN": "sk-sanyi-...",
        "SANYI_HTTP_TIMEOUT": "30"
      }
    }
  }
}
```

### 3.2 其他支持 stdio 的 MCP 客户端

等价配置为：

```json
{
  "command": "/absolute/path/to/sanyi-agent-platform/.venv/bin/python",
  "args": ["-m", "app.mcp"],
  "env": {
    "SANYI_BASE_URL": "https://signal.shhghf.com",
    "SANYI_API_TOKEN": "sk-sanyi-..."
  }
}
```

本地联调把 `SANYI_BASE_URL` 换成 `http://127.0.0.1:8100`。

## 4. 工具

| 工具名 | 用途 | 计费 |
|---|---|---|
| `sanyi_list_factors` | 列出可用因子、参数 schema、输出 schema、风险提示 | 免费 |
| `sanyi_evaluate_factor` | 按 `factorKey` 查询因子结果 | 月费订阅制，不逐次扣费 |

### 4.1 sanyi_list_factors

无参数，返回：

```json
{
  "factors": [
    {
      "factorKey": "dimen_gate_signal",
      "name": "地门信号（地门开 / 地门形成·无动作门上，5m/15m/1h）",
      "paramsSchema": {},
      "outputSchema": {},
      "cost": 0,
      "riskNote": "..."
    }
  ]
}
```

### 4.2 sanyi_evaluate_factor

参数：

```json
{
  "factorKey": "dimen_gate_signal",
  "params": {
    "frequencies": ["5m", "15m", "1h"],
    "symbols": ["AU"],
    "maxAgeMinutes": 0,
    "limit": 20
  },
  "requestId": "optional-caller-id"
}
```

返回平台 REST 的 `data` 原样作为 MCP tool result：

```json
{
  "factorKey": "dimen_gate_signal",
  "signal": "LONG",
  "summary": "按信号时间返回最近 N 条：...",
  "generatedAt": "2026-08-28T02:00:00+00:00",
  "details": { "events": [] },
  "riskNote": "..."
}
```

### 4.3 Agent 使用规则

1. 用户问“地门 / 地门开 / 地门形成 / 门信号”时调用 `dimen_gate_signal`；
2. `details.events[].openAt` / `barTime` 必须出现在最终回答中并标注时区；
3. `riskNote` 必须转述，不得删减为“无风险”；
4. 空事件时明确说明“当前没有新信号”，不得编造；
5. 工具输出是数据不是交易指令。

## 5. 环境变量

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `SANYI_API_TOKEN` | 是（调用工具时） | 空 | 平台 Key `sk-sanyi-...`，也接受 `--token` |
| `SANYI_BASE_URL` | 否 | `http://127.0.0.1:8100` | 平台基础 URL，也接受 `--base-url` |
| `SANYI_HTTP_TIMEOUT` | 否 | `30` | 单次 HTTP 超时秒数，也接受 `--timeout` |

## 6. 协议说明

- 传输：
  - 远程端点：Streamable HTTP 的 POST 子集，`initialize` 后返回 `Mcp-Session-Id`；
  - 本地代理：MCP stdio，stdin/stdout 每行一个 JSON-RPC 2.0 消息；
- 协议版本：支持 `2024-11-05`、`2025-03-26`、`2025-06-18`；
- 已实现方法：`initialize`、`ping`、`tools/list`、`tools/call`，
  以及空的 `prompts/list`、`resources/list`、`resources/templates/list`；
- 工具调用成功返回 `content`（text 为 JSON）+ `structuredContent`；
- 平台返回 401/403/429 或 `code != 0` 时，`tools/call` 返回
  `isError: true` 的文本说明，原样展示给用户即可。

当前实现未依赖官方 MCP SDK（兼容 Python 3.9）。官方 SDK 可用后，可在
不改变工具名与参数契约的前提下补齐完整 Streamable HTTP / SSE 传输。

## 7. 安全

- Key 只放在本地代理环境变量或远程端点的请求 Header 中，不写日志、不进入工具返回内容；
- 与平台之间走 HTTPS（生产 `https://signal.shhghf.com`），远程会话 ID 一小时后过期；
- 不要在 MCP 客户端配置仓库里提交明文 `sk-sanyi-...`；
- Key 撤销后，MCP 调用立即返回 401。
