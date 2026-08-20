# sanyi-agent-platform

三易引擎数据 Agent 开放平台骨架（原型）。

核心模式是“出现即提示”的事件流：

```
sanyi 引擎 gate_registry.json（只读）
  → 轮询器筛选：地门 + 5m/15m/1h + 已开/无动作·门上
  → 新信号写入 signal_events 并推送
  → 页面 Agent 实时弹出信号卡片
  → 用户点击卡片让 LLM 解读（DeepSeek function calling）
```

同时保留查询接口 `/api/v1/factors/evaluate`，供页面 Agent 工具调用，
后续再以 MCP / Skill / REST 开放给用户自己的 Agent。

## 目录

- `app/`：FastAPI 应用
  - `app/main.py`：入口
  - `app/factor_registry.py`：因子注册表（单一事实来源）
  - `app/factors/dimen_gate_signal.py`：地门信号因子（读取事件，不重算引擎）
  - `app/engine/`：门信号读取、交易时段、轮询、SSE 推送总线
  - `app/api/`：健康检查、令牌、因子、信号事件、聊天接口
  - `app/agent/`：LLM 客户端与 function-calling 循环
  - `app/web/`：聊天页面静态资源
- `docs/`：架构、安全合规、Skill 文档
- `scripts/create_token.py`：签发测试令牌
- `tests/`：骨架冒烟测试

## 快速启动

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp .env.example .env
# 必填：SANYI_GATE_REGISTRY_FILE 指向 sanyi 引擎的 gate_registry.json
# 按需填写 LLM_API_KEY；本地联调可 LLM_MOCK=1
python scripts/create_token.py --name dev --quota 100000
uvicorn app.main:app --reload --port 8100
```

打开 `http://127.0.0.1:8100/chat?token=sk-...` 即可测试页面 Agent。

## 当前状态

- [x] 令牌签发（SQLite，本地原型）
- [x] 因子注册表 + 统一 evaluate 接口
- [x] 门信号读取 + 交易时段 + 30 秒轮询 + 新事件去重
- [x] 页面实时信号推送（SSE）+ 页面 Agent（DeepSeek/OpenAI 兼容 function calling）
- [ ] `SANYI_GATE_REGISTRY_FILE` 接真实文件联调
- [ ] 真实 LLM 联调（DeepSeek key 到位后填 `.env`）
- [ ] MCP 服务端 + Skill 文档生成
- [ ] 接入 New API / 支付 / 订阅
