# sanyi-agent-platform

三易引擎数据 Agent 开放平台骨架（原型）。

先跑通一条最小链路：

```
用户令牌 sk-xxx
  → 页面 Agent (/chat)  → LLM(function calling)
  → 因子服务 /api/v1/factors/evaluate
  → 三易引擎因子（首个：15 分钟地门开·做多信号）
```

后续再开放 MCP / Skill / REST 给用户自己的 Agent。

## 目录

- `app/`：FastAPI 应用
  - `app/main.py`：入口
  - `app/factor_registry.py`：因子注册表（单一事实来源）
  - `app/factors/dimen_gate_15m_long.py`：第一个因子（占位实现，规则待填）
  - `app/api/`：健康检查、令牌、因子、聊天接口
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
# 按需填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
python scripts/create_token.py --name dev --quota 100000
uvicorn app.main:app --reload --port 8100
```

打开 `http://127.0.0.1:8100/chat?token=sk-...` 即可测试页面 Agent。

## 当前状态

- [x] 令牌签发（SQLite，本地原型）
- [x] 因子注册表 + 统一 evaluate 接口
- [x] 页面 Agent（OpenAI 兼容 function calling，SSE 流式）
- [ ] 首个因子真实规则（等待业务定义后填充）
- [ ] MCP 服务端
- [ ] Skill 文档自动生成
- [ ] 接入 New API / 支付 / 订阅
