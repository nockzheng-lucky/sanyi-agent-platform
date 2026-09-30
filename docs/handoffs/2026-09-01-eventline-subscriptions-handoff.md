# 交接文档：订阅全面事件线 + 推送基线 + 今日门信号

日期：2026-09-01
状态：已实现并部署到生产（未提交 git，工作区改动）

## 1. 核心决策

用户明确要求：所有持续订阅都必须是“事件线”，订阅时已存在的旧信号不推；
期货侧每天 15:00 切日，币圈侧使用当日事件流。快照因子只能查询，不能订阅。

落地规则：

1. `FactorSpec` 新增 `event_based: bool = True`（**新因子默认事件线**）。
2. 只有 `event_based=True` 的因子能进入 Agent 的 `sanyi_create_subscription` 白名单。
3. 快照因子显式标记 `event_based=False`，Agent 工具会拒绝订阅。
4. 所有订阅首次推送前先建 baseline：当前匹配只记录、不推送。
5. 期货今日门信号以北京时间 15:00 为交易日边界，自动滚动。

## 2. 因子状态

| factorKey | 类型 | 能否订阅 | 口径 |
|---|---|---|---|
| `futures_gate_signal` | 事件线 | ✅ | 期货今日开门/关门，15:00 切日，5m/15m/1h |
| `dimen_gate_signal` | 事件线 | ✅ | 期货地门开/地门形成（无动作门上） |
| `crypto_gate_signal` | 事件线 | ✅ | 币圈今日开门(`open`) + 形成门(`formation`)，与热力图 `scope=today` 同管道 |
| `gate_condition` | 快照 | ❌ 订阅 | 仅查询研究 |
| `wave_jue_combo` / `jue_direction` | 快照 | ❌ 订阅 | 仅查询研究 |
| `crypto_gate_condition` / `crypto_wave_jue_combo` / `crypto_market` | 快照 | ❌ 订阅 | 仅查询研究 |

## 3. 当前生产订阅（user 4）

| id | 名称 | 因子 | 状态 |
|---|---|---|---|
| 11 | 币圈15m/1h天门打开（今日事件） | crypto_gate_signal | active，`eventTypes=["open"]` |
| 14 | 期货15m/1h天门打开（今日事件） | futures_gate_signal | active，`actions=["open"]` |
| 15 | 期货15m/1h天门已关（今日事件） | futures_gate_signal | active，`actions=["close"]` |
| 16 | 期货15m/1h地门形成 | dimen_gate_signal | active |

## 4. 推送逻辑

- `subscription_pusher.py`：
  - 首轮 baseline：`signal_subscriptions.baseline_at` 为空时，当前 matches 全部 `record_match + mark_pushed`，不发 Pushplus。
  - 之后只有新 `match_key` 才推。
  - 一条信号一条 Pushplus。
  - Pushplus 返回 `900`（日限额）或 `903`（token 无效）时，该用户暂停重试 1 小时。
- 门池类老门补推保护：`_is_stale_baseline_match`，baseline 后才因 limit 扩大进入视野的老门只记录不推。
- `signal_subscriptions` 增加 `baseline_at` 列；旧库 `init_db()` 自动加列。

## 5. 期货 15:00 切日

- `futures_gate_signal._cycle_start()`：北京时间当天 15:00，若当前早于 15:00 则取昨天 15:00。
- 只保留 `open_at / close_at >= cycle_start` 的事件。
- 面板和推送都走该因子，每天 15:00 后上一交易日信号自动滚出。
- 100 条上限足够：当前周期 15m/1h 门事件约 31 条；历史单日峰值约 39 条。

## 6. 币圈今日门信号

- 平台 `crypto_gate_signal` 读 `https://167.179.69.189/api/events?scope=today`。
- 支持参数：
  - `frequencies`: 5m/15m/1h
  - `gateTypes`: tian/di
  - `eventTypes`: open / formation（默认两者）
- `open` → `OPEN`；`new` 且文本含“门上” → `FORMATION_ABOVE`，含“门下” → `FORMATION_BELOW`。
- 东京侧热力图 `scope=today` 已切到 futures 同款 sidebar pipeline；平台与热力图来源一致。
- 注意：币圈事件接口的 `date/time` 已由东京侧转成北京时间并带 `timezone=CST`，平台 `_event_beijing_iso` 兼容 CST / utc_date / 旧 UTC 三种格式。

## 7. 杠杆建议

- `app/crypto_leverage.py`：币圈信号附加 `suggestedLeverage`。
- 公式：`floor(50 × ETH波动率 / 该币波动率)`，下限 10，上限 50。
- 波动率 = `max(1h ATR14%, 24h振幅%)`。
- 数据源：东京侧 `/api/market/volatility`（批量接口，含 `selectedSince`）。
- 老门过滤：门形成时间早于该币 `selectedSince` 的门不推；事件线信号不受该过滤影响。

## 8. 前端

- 订阅面板：订阅卡片横向排列，卡片内信号纵向排列；滚轮在卡片区域自动横向滚动。
- 信号时间统一显示北京时间；开门/关门/形成门按 `gateType + status` 正确显示。
- 信号倒序，最新在上。
- 门价格显示在面板和 Pushplus。
- 静态文件：`app/web/static/chat.js`、`style.css`。改动后需用户强刷。

## 9. 部署信息

- 平台：`101.35.210.32`，目录 `/home/ubuntu/sanyi-agent-platform`，systemd `sanyi-agent-platform.service`。
  - 同步：rsync `app/` 到生产；重启 `sudo systemctl restart sanyi-agent-platform`。
- 币圈东京：`sanyi-bybit-tokyo-01`，公网绿色 web 在 `/opt/sanyi-green/current/apps/crypto`，端口 `19102`。
  - 已打补丁：
    - `runtime/klines.py`：`api_market_volatility` + `selectedSince`
    - `runtime/publisher.py`：`scope=today` 使用 sidebar pipeline，时间转北京时间
    - `web/app.py`：`/api/market/volatility` 路由
  - 备份文件在对应目录 `*.bak-*`。
  - 若未来用 git 重新部署东京 green，需要重新合入上述三个补丁。

## 10. 验证

```bash
cd /Users/gaomengyuan/dev/sanyi-agent-platform
. .venv/bin/activate
python -m pytest -q          # 当前 77 passed
python -m compileall -q app tests
```

生产检查：

```bash
ssh -l ubuntu qh.shhghf.com
cd /home/ubuntu/sanyi-agent-platform
.venv/bin/python -m compileall -q app
sudo systemctl restart sanyi-agent-platform
curl -s http://127.0.0.1:8100/api/health
```

## 11. 待办 / 已知边界

- `wave_jue_combo` / `jue_direction` 仍为快照，无法做真正事件订阅；未来要接入期货侧精确时序 API（`last_transition`、`break_index` 等）后再升级为事件因子。
- 头肩 HS 后端尚未实现。
- Pushplus 当天额度已触发 900，待额度恢复后平台会补发 `last_pushed_at IS NULL` 的积压信号；如用户不想补发，可清理对应 `subscription_match_keys`。
- 当前工作区尚未 git commit。
