# 架构说明

## 目标

用户在三易平台申领 `sk-` 令牌后：

1. 先在平台页面 `/chat` 使用 Agent 调 LLM + 三易因子；
2. 之后再开放给用户自己的 Agent（MCP / Skill / REST）。

LLM 成本由平台承担；收费口径为**月费订阅**：
- 轮询读取 gate_events.sqlite3 不调用 LLM，不消耗任何 token，也不扣用户额度；
- 因子查询/信号推送不逐次扣费，但保留 usage_logs 审计；
- DeepSeek 只在页面 Agent 实际对话/解读信号时产生 token 用量。

## 与调研对象（open.hitick.top）的对应关系

| 能力 | Orange Hitick | 三易平台骨架 |
|---|---|---|
| 账号与令牌 | New API（sk- 令牌、额度、日志） | 本地 SQLite 原型，接口契约预留 New API 替换 |
| 页面 Agent | 定制 Next.js + Dify + entry_token | FastAPI 页面 + OpenAI function calling（先最小闭环） |
| 因子入口 | `subjective-signal/evaluate` 按 factorKey 路由 | `/api/v1/factors/evaluate` 同一模式 |
| 用户自己的 Agent | MCP + Skill 文档 + HTTP | 后续按同样三层实现 |
| 计费 | 成功后扣费，扣费失败直接报错 | 三易改为月费订阅；保留成功/失败审计，不逐次扣额度 |

## 当前最小闭环

```
sanyi green gate_events.sqlite3（只读）
  → 轮询器：只在交易时段，每 30 秒读一次
  → 当天交易日窗口：今天，或昨天 21:00 之后
  → 筛选：gate_type=地门 + freq∈{5m,15m,1h}
           formation(门上) / first-action(open)
  → 新 event_id 入库 signal_events
  → 因子 dimen_gate_signal 读取最近事件

浏览器 /chat
  → 用户登录会话或旧令牌会话
  → 左侧因子列表把因子“加载到 Agent”
  → POST /api/chat（SSE）
  → LLM function calling
       ├─ sanyi_list_factors       → 因子注册表
       ├─ sanyi_update_filters     → 自然语言维护持久筛选条件
       ├─ sanyi_get_filters / sanyi_clear_filters
       └─ sanyi_evaluate_factor    → 自动合并存量筛选条件后执行
                                      ├─ 参数 JSON Schema 校验
                                      ├─ 月费制：不扣额度
                                      └─ 写 usage_logs 审计
```

“推送”与“查询”分离：
- 外部接入推送：poller → signal_events → SignalBus → `/api/v1/signal-events/stream`（SSE）；
- Agent/REST：统一走 `/api/v1/factors/evaluate` → 同一张 signal_events 或外部行情 API；
- 持续订阅：`sanyi_create_subscription` 持久化条件 →
  `/api/v1/signal-subscriptions/matches|stream` 周期评估因子并刷新聊天页左侧面板。

## 目录职责

- `app/factor_registry.py`：因子的单一事实来源；页面 Agent 与未来 MCP/REST 共用。
- `app/factors/dimen_gate_signal.py`：门信号因子；不重算引擎，只读事件。
- `app/factors/jue_direction.py`：诀与破诀因子；只读 qh HTTP API。
- `app/factors/crypto_market.py`：币圈行情因子；`shadow_only=True`，只对影子模式账号可见。
- `app/engine/gate_reader.py`：只读 gate_events.sqlite3，按当天交易日筛选目标信号。
- `app/engine/poller.py`：交易时段轮询 + event_id 去重 + 新事件写入。
- `app/db.py`：令牌哈希、额度、日志、会话、signal_events、agent_filters；原型用 SQLite。
- `app/agent/filter_store.py`：按用户持久化自然语言筛选条件。
- `app/agent/`：LLM 客户端与工具调用循环。
- `app/web/`：Agent 聊天页、因子列表页等静态资源。

## 关键设计约束

1. 令牌明文只出现一次（签发时），库里只存 SHA-256；日志永不记录令牌。
2. 页面聊天的 cookie 只放服务端会话 ID，不放令牌。
3. 工具白名单固定：LLM 只能调用因子查询工具与筛选条件维护工具，不能执行任意代码或访问文件。
4. 因子输出只给“结论 + 结构化 details”，不返回原始行情明细；机密因子可只返回“命中/未命中”。
5. 月费订阅制下不逐次扣费；所有调用仍写 usage_logs，后续据此做风控和成本核算。
6. 三易引擎数据访问必须经过 adapter，不允许页面/Agent 直连生产库。

## 后续演进

### Phase 2：真实数据联调
- 把 `SANYI_GATE_EVENTS_DB` 指到 sanyi-green 的 `gate_events.sqlite3`；
- 核对当天交易日窗口、formation(门上)/open 映射与 event_at 时区；
- 若平台和 green 不同机，再增加只读同步或内网 SQL 访问；
- 补因子级单元测试和回放样本。

### Phase 3：页面 Agent 生产化
- LLM 路由：多模型、降级、月预算、成本日报；
- 会话持久化、历史记录、分享禁用；
- 前端增加 tool_call 过程可视化、余额展示、错误重试。

### Phase 4：开放给用户自己的 Agent
- [x] MCP 服务（`sanyi_list_factors` / `sanyi_evaluate_factor`，Header `X-API-Token`）：
  - 远程端点 `POST /api/v1/mcp`（Streamable HTTP POST 子集，1 小时会话）；
  - 本地 stdio 代理 `sanyi-mcp`（`python -m app.mcp`）；
- [x] 每个因子一份 Skill Markdown 文档，供 Agent 自读自封装；
- [ ] REST/OpenAPI 在线文档与多语言 SDK 示例。

### Phase 5：账号与支付
- 用 New API 替换 SQLite 令牌层（保留 sk- 与 X-API-Token 契约）；
- 充值/订阅/兑换码；发票与对账。
