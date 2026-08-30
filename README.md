# sanyi-agent-platform

三易引擎数据 Agent 开放平台骨架（原型）。

核心模式是“门信号也是一个因子”：

```
sanyi green gate_events.sqlite3（只读，与热力图今日门信号同源）
  → 轮询器筛选当天：地门 + 5m/15m/1h + formation(门上)/open
  → 新信号写入 signal_events
  → 因子 dimen_gate_signal 读取最近门信号
  → 用户在左侧因子列表加载因子，Agent 用自然语言查询/组合筛选条件
```

Agent 页不再内置实时信号卡片；门信号与“诀与破诀”一样，统一走
因子列表 → 加载到 Agent → 自然语言查询的流程。

同时保留查询接口 `/api/v1/factors/evaluate`，页面 Agent 与用户自己的
Agent 共用同一契约：REST（`X-API-Token`）、MCP（`sanyi-mcp`）、
Skill（`docs/factors/`）三层开放。

成本口径：
- 轮询读 SQLite **不消耗任何 LLM token，也不扣用户额度**；
- 收费为月费订阅；因子调用和信号查询不逐次扣费，只做用量审计；
- DeepSeek token 只在用户与 Agent 实际对话时产生。

## 目录

- `app/`：FastAPI 应用
  - `app/main.py`：入口
  - `app/factor_registry.py`：因子注册表（单一事实来源）
  - `app/factors/dimen_gate_signal.py`：地门信号因子（读取事件，不重算引擎）
  - `app/factors/jue_direction.py`：诀与破诀因子（qh 全品种诀方向）
    - `app/factors/crypto_market.py`：币圈行情因子（影子模式专用）
  - `app/engine/`：门信号读取、交易时段、轮询、SSE 总线（供外部接入）
  - `app/agent/filter_store.py`：Agent 自然语言筛选条件存储
  - `app/api/`：健康检查、令牌、因子、信号事件、聊天接口
  - `app/agent/`：LLM 客户端与 function-calling 循环
  - `app/mcp/`：面向用户 Agent 的 MCP stdio 服务
  - `app/web/`：聊天页面静态资源
- `docs/`：架构、安全合规、MCP 接入说明、Skill 文档
- `scripts/create_token.py`：签发测试令牌
- `scripts/set_shadow_mode.py`：设置影子模式账号
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
- [x] 页面 Agent（DeepSeek/OpenAI 兼容 function calling）
- [x] 因子列表 + 加载到 Agent + 自然语言组合/调整筛选条件
- [x] 持续信号订阅：Agent 确认后，聊天页左侧订阅面板持续显示匹配信号
- [x] Pushplus 推送：按订阅动态推送新匹配信号（用户绑定自己的 token）
- [x] 因子：地门信号 `dimen_gate_signal`、诀与破诀 `jue_direction`
- [x] 因子：门条件 `gate_condition`、走法×破诀组合 `wave_jue_combo`（当前快照版）
- [x] 影子模式：158 管理员专属币圈行情因子 `crypto_market`
- [x] `SANYI_GATE_EVENTS_DB` 接生产 SQLite 联调
- [x] 真实 LLM 联调（DeepSeek key 到位后填 `.env`）
- [x] MCP stdio 服务端 + Skill 文档（REST / MCP / Skill 三层开放）
- [ ] 接入 New API / 支付 / 订阅（当前礼品卡为 MOCK）
