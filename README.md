# sanyi-agent-platform

三易引擎数据 Agent 开放平台骨架（原型）。

核心模式是“出现即提示”的事件流：

```
sanyi green gate_events.sqlite3（只读，与热力图今日门信号同源）
  → 轮询器筛选当天：地门 + 5m/15m/1h + formation(门上)/open
  → 新信号写入 signal_events 并推送
  → 页面 Agent 实时弹出信号卡片
  → 用户点击卡片让 LLM 解读（DeepSeek function calling）
```

同时保留查询接口 `/api/v1/factors/evaluate`，页面 Agent 与用户自己的
Agent 共用同一契约：REST（`X-API-Token`）、MCP（`sanyi-mcp`）、
Skill（`docs/factors/`）三层开放。

成本口径：
- 轮询读 SQLite **不消耗任何 LLM token，也不扣用户额度**；
- 收费为月费订阅；因子调用和信号推送不逐次扣费，只做用量审计；
- DeepSeek token 只在用户与 Agent 实际对话/点击解读时产生。

## 目录

- `app/`：FastAPI 应用
  - `app/main.py`：入口
  - `app/factor_registry.py`：因子注册表（单一事实来源）
  - `app/factors/dimen_gate_signal.py`：地门信号因子（读取事件，不重算引擎）
  - `app/engine/`：门信号读取、交易时段、轮询、SSE 推送总线
  - `app/api/`：健康检查、令牌、因子、信号事件、聊天接口
  - `app/agent/`：LLM 客户端与 function-calling 循环
  - `app/mcp/`：面向用户 Agent 的 MCP stdio 服务
  - `app/web/`：聊天页面静态资源
- `docs/`：架构、安全合规、MCP 接入说明、Skill 文档
- `scripts/create_token.py`：签发测试令牌
- `tests/`：骨架冒烟测试

## 快速启动

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp .env.example .env
# 必填：SANYI_GATE_EVENTS_DB 指向 sanyi-green 的 gate_events.sqlite3
# 按需填写 LLM_API_KEY；本地联调可 LLM_MOCK=1
python scripts/create_token.py --name dev --quota 100000
uvicorn app.main:app --reload --port 8100
```

打开 `http://127.0.0.1:8100/chat?token=sk-...` 即可测试页面 Agent。

给用户自己的 Agent 接 MCP：
- 远程端点：`POST /api/v1/mcp`（Header `X-API-Token: sk-sanyi-...`）；
- 本地 stdio 代理：

```bash
SANYI_BASE_URL=http://127.0.0.1:8100 \
SANYI_API_TOKEN=sk-sanyi-... \
python -m app.mcp
```

详细配置见 `docs/mcp.md`。

## 当前状态

- [x] 令牌签发（SQLite，本地原型）
- [x] 因子注册表 + 统一 evaluate 接口
- [x] 门信号读取 + 交易时段 + 30 秒轮询 + 新事件去重
- [x] 页面实时信号推送（SSE）+ 页面 Agent（DeepSeek/OpenAI 兼容 function calling）
- [x] `SANYI_GATE_EVENTS_DB` 接生产 SQLite 联调
- [x] 真实 LLM 联调（DeepSeek key 到位后填 `.env`）
- [x] MCP stdio 服务端 + Skill 文档（REST / MCP / Skill 三层开放）
- [ ] 接入 New API / 支付 / 订阅（当前礼品卡为 MOCK）
