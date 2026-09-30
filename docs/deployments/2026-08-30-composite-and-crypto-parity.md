# 部署回执：组合订阅 + 币圈因子对齐（2026-08-30）

## 结果

- 状态：**deployed to 101**
- 主机：`101.35.210.32`（qh.shhghf.com）
- 服务：`sanyi-agent-platform.service`，active(running)，监听 `127.0.0.1:8100`
- 健康检查：`GET /api/health` → `{"code":0,...,"status":"up"}`

## 本次内容

1. 组合订阅条件层：
   - `subscription_conditions` 子表 + 旧订阅迁移（生产库已有 10 条 condition 记录）；
   - `app/composite_evaluator.py`：primary + context 全 AND、父级周期 offset、below/above sideRule；
   - Agent 工具 `sanyi_create_subscription` 支持 `conditions`；
   - 订阅面板与 Pushplus worker 统一走组合评估器。

2. 币圈因子对齐期货：
   - `crypto_gate_condition`：币圈门条件，数据源 `https://167.179.69.189/api/gates?view=fresh`；
   - `crypto_wave_jue_combo`：币圈走法×破诀组合，复用 `crypto_market` 数据源；
    - `crypto_gate_signal`：币圈今日开门事件，对齐期货 dimen_gate_signal 只读事件流；
   - 均为 shadow_only / domain=crypto。

## 部署过程

- 备份：`data/platform.sqlite3*` 与 `app/` 已备份到 `backups/`（前缀 `before-composite-20260830_222445`）。
- 同步：rsync（排除 `.git/.venv/data/.env` 与缓存），随后 `compileall` 通过。
- 重启：`systemctl restart sanyi-agent-platform`；旧进程因 SSE 连接未退被 systemd 超时 SIGKILL，新进程正常启动。
- 验证：生产机直连东京币圈接口，门条件 118 条命中、走法×破诀组合 3 条命中。
- 补充部署：Agent 增加 `sanyi_get_pushplus` 工具；已绑定用户创建订阅后自动推送，未绑定引导到「通知」页。
- 补充部署：币圈东京接口超时改为 30s / 连接 15s（原 10s / 连接 5s，跨公网偶发 ConnectTimeout）；订阅面板信号时间统一显示为北京时间。
- 补充部署：新增 `crypto_gate_signal`，并把生产上的币圈天门订阅迁移到该因子；`crypto_gate_condition` 保留为全部门池研究因子。
- 补充部署：Pushplus 改为一条信号一条推送，不再把多个品种合并到同一条消息。
- 补充部署：币圈门事件源（`crypto_gate_signal`）连接超时上调到 25s，并增加一次自动重试，降低跨公网偶发 ConnectTimeout。
- 补充部署：`crypto_gate_signal` 数据源改为与热力图相同的 `scope=today` sidebar pipeline，并按东京侧返回的 CST 字段解析时间，避免二次时区换算。
- 补充部署：东京绿色 web 新增 `/api/market/volatility` 批量波动率接口；平台币圈信号按 ETH=50x 锚定换算建议杠杆，下限 10x / 上限 50x；订阅面板取消“只显示前 20 条”截断。
- 补充部署：老门过滤规则——币种刚进入当前 list 时，形成时间早于入 list 时间的门不推送（东京侧在波动率批接口返回 selectedSince，平台按 cross/t1 时间过滤）。
- 补充部署：订阅推送增加 baseline 机制——订阅时已存在的信号只记录不推送，之后新出现的信号才会提醒。
- 补充部署：订阅评估不再受因子默认 limit=30 截断，拉满到 schema 上限；baseline 之后因扩 limit 才出现的老门不会补推，只有 baseline 之后新开的门才推。
- 补充部署：因子增加 `eventBased` 标记（默认 true）；Agent 订阅白名单只允许事件线因子，快照因子（gate_condition/wave_jue_combo/crypto_market 等）仅查询不可订阅；生产存量快照订阅已清理。
- 补充部署：新增 `futures_gate_signal`（期货今日门信号），按北京时间 15:00 切日；生产期货天门开/关订阅已迁移到该因子。

## 待办

- 给影子账号创建“走2破20诀 + 下方有效地门”“走B破80诀 + 上方有效天门”组合订阅；
- 精确时序组合（破诀时仍处于某走法）仍待期货侧时序 API 与头肩 HS 后端。
