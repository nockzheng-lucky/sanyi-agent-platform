# 架构说明

## 目标

用户在三易平台申领 `sk-` 令牌后：

1. 先在平台页面 `/chat` 使用 Agent 调 LLM + 三易因子；
2. 之后再开放给用户自己的 Agent（MCP / Skill / REST）。

LLM 成本由平台承担；因子调用按额度扣费；元信息接口免费。

## 与调研对象（open.hitick.top）的对应关系

| 能力 | Orange Hitick | 三易平台骨架 |
|---|---|---|
| 账号与令牌 | New API（sk- 令牌、额度、日志） | 本地 SQLite 原型，接口契约预留 New API 替换 |
| 页面 Agent | 定制 Next.js + Dify + entry_token | FastAPI 页面 + OpenAI function calling（先最小闭环） |
| 因子入口 | `subjective-signal/evaluate` 按 factorKey 路由 | `/api/v1/factors/evaluate` 同一模式 |
| 用户自己的 Agent | MCP + Skill 文档 + HTTP | 后续按同样三层实现 |
| 计费 | 成功后扣费，扣费失败直接报错 | 已实现同样规则 |

## 当前最小闭环

```
浏览器 /chat?token=sk-...
  → 服务端校验令牌，发 HttpOnly 会话 cookie
  → POST /api/chat（SSE）
  → LLM function calling
       ├─ sanyi_list_factors       → GET /api/v1/factors 的同一注册表
       └─ sanyi_evaluate_factor    → POST /api/v1/factors/evaluate
                                        ├─ 参数 JSON Schema 校验
                                        ├─ 短 TTL 缓存
                                        ├─ 三易引擎因子计算（当前 MOCK）
                                        ├─ 成功后扣额度
                                        └─ 写 usage_logs
```

## 目录职责

- `app/factor_registry.py`：因子的单一事实来源；页面 Agent 与未来 MCP/REST 共用。
- `app/factors/*.py`：每个因子一个文件；真实计算只允许出现在这里，不直接暴露三易引擎数据。
- `app/db.py`：令牌哈希、额度、日志、会话；原型用 SQLite。
- `app/agent/`：LLM 客户端与工具调用循环。
- `app/web/`：聊天页静态资源。

## 关键设计约束

1. 令牌明文只出现一次（签发时），库里只存 SHA-256；日志永不记录令牌。
2. 页面聊天的 cookie 只放服务端会话 ID，不放令牌。
3. 工具白名单固定：LLM 只能调用 `sanyi_list_factors` / `sanyi_evaluate_factor`。
4. 因子输出只给“结论 + 结构化 details”，不返回原始行情明细；机密因子可只返回“命中/未命中”。
5. 成功后才扣费；扣费失败返回 402，不返回结果。
6. 三易引擎数据访问必须经过 adapter，不允许页面/Agent 直连生产库。

## 后续演进

### Phase 2：因子真实接入
- 在 `app/factors/dimen_gate_15m_long.py` 替换 MOCK；
- 增加 `app/engine_adapter.py`，只读三易引擎/缓存，带超时、熔断、降级；
- 补因子级单元测试和回放样本。

### Phase 3：页面 Agent 生产化
- LLM 路由：多模型、降级、月预算、成本日报；
- 会话持久化、历史记录、分享禁用；
- 前端增加 tool_call 过程可视化、余额展示、错误重试。

### Phase 4：开放给用户自己的 Agent
- MCP 服务（`sanyi_list_factors` / `sanyi_evaluate_factor`，Header `X-API-Token`）；
- 每个因子一份 Skill Markdown 文档，供 Agent 自读自封装；
- REST/OpenAPI 文档与多语言 SDK 示例。

### Phase 5：账号与支付
- 用 New API 替换 SQLite 令牌层（保留 sk- 与 X-API-Token 契约）；
- 充值/订阅/兑换码；发票与对账。
