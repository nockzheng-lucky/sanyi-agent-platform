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
| `crypto_gate_signal` | 事件线 | ✅ | 币圈今日门四态：开(`open`) / 关(`close`) / 形成门上(`formationAbove`) / 形成门下(`formationBelow`)，与热力图 `scope=today` 同管道 |
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
| 17 | 币圈15m/1h地门形成（旧，无 MA208 条件） | crypto_gate_signal | inactive（2026-09-02 停用，由 id 18 替代） |
| 18 | 币圈5m/15m/1h地门形成（开门/门上） | crypto_gate_signal | active，`eventTypes=["open","formationAbove"]`；MA208 仅展示，不筛选 |

## 4. 推送逻辑

- `subscription_pusher.py`：
  - 首轮 baseline：`signal_subscriptions.baseline_at` 为空时，当前 matches 全部 `record_match + mark_pushed`，不发 Pushplus。
  - 之后只有新 `match_key` 才推。
  - 同一轮出现的多条新信号按用户合并成一条 Pushplus；单条时保持原格式。
  - Pushplus 返回 `900`（日限额）或 `903`（token 无效）时，该用户暂停重试 1 小时。
  - 评估完成后、逐条发送前都会实时复核订阅是否仍为 active；用户在评估期间取消订阅，本周期不会继续推送。
- 门池类老门补推保护：`_is_stale_baseline_match`，baseline 后才因 limit 扩大进入视野的老门只记录不推。
- `signal_subscriptions` 增加 `baseline_at` 列；旧库 `init_db()` 自动加列。

## 5. 期货 15:00 切日

- `futures_gate_signal._cycle_start()`：北京时间当天 15:00，若当前早于 15:00 则取昨天 15:00。
- 只保留 `open_at / close_at >= cycle_start` 的事件。
- 面板和推送都走该因子，每天 15:00 后上一交易日信号自动滚出。
- 100 条上限足够：当前周期 15m/1h 门事件约 31 条；历史单日峰值约 39 条。

## 6. 币圈今日门信号（四态精确区分）

- 平台 `crypto_gate_signal` 读 `https://167.179.69.189/api/events?scope=today`。
- 支持参数：
  - `frequencies`: 5m/15m/1h
  - `gateTypes`: tian/di
  - `eventTypes`（默认 `open / close / formation`）：
    - `open` = 开门 → `OPEN`
    - `close` = 关门 → `CLOSED`
    - `formation` = 形成门（两侧都含，兼容旧订阅）
    - `formationAbove` = 形成门·无动作门上 → `FORMATION_ABOVE`
    - `formationBelow` = 形成门·无动作门下 → `FORMATION_BELOW`
- 新订阅要精确区分四态时，Agent 应传 `eventTypes=["formationAbove"]` / `["formationBelow"]` 等精确值，不要用 `formation` 代替单侧。
- 关键数据源事实：`type=new` 事件**没有** `formation` 字段，形成侧只存在于 `text`（“门上/门下”）；而门池 `gates?view=fresh` 里的 `formation` 是“当前”状态，门开/关后可能已翻转。因子必须从事件 `text` 定形成侧，不能回退当前门池，否则 `status` 与 `formation` 会自相矛盾。
- 形成门时间口径：东京 green 已把 `type=new` 的 `date/time` 改为 gate `t2_str`（北京时间），平台 `eventAt` 与 baseline/maxAge 都按真实形成时间计算；检测延迟的老门不会再被事件订阅当作新信号补推。
- 东京侧热力图 `scope=today` 已切到 futures 同款 sidebar pipeline；平台与热力图来源一致。
- 注意：币圈事件接口的 `date/time` 已由东京侧转成北京时间并带 `timezone=CST`，平台 `_event_beijing_iso` 兼容 CST / utc_date / 旧 UTC 三种格式。

## 7. 杠杆建议

- `app/crypto_leverage.py`：币圈信号附加 `suggestedLeverage`。
- 公式：`floor(50 × ETH波动率 / 该币波动率)`，下限 10，上限 50。
- 波动率 = `max(1h ATR14%, 24h振幅%)`。
- 数据源：东京侧 `/api/market/volatility`（批量接口，含 `selectedSince`）。
- 老门过滤：门形成/动作时间早于该币 `selectedSince` 的门不推；**事件线信号也过滤**（防止合约宇宙冷纳入币种时补发的历史门被当成新信号推送）。

## 8. 前端

- 订阅面板改为手风琴分组：`期货侧` / `币圈侧` 两个可折叠区；普通账号只显示期货侧，影子账号显示两侧。
- 每个分组内部订阅卡片横向排列，卡片内信号纵向排列；滚轮在分组卡片区域自动横向滚动。
- 分组折叠状态在页面刷新轮询中保持，不会 30 秒后被重置。
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
    - `runtime/publisher.py`：`scope=today` 使用 sidebar pipeline，时间转北京时间；`type=new` 的 `date/time` 使用 gate `t2_str`（真实形成时间），并输出 `formation_t2` / `detected_at`
    - `web/app.py`：`/api/market/volatility` 路由
  - 备份文件在对应目录 `*.bak-*`（含 `runtime/publisher.py.bak-20260901_before-formation-t2-time`）。
  - 若未来用 git 重新部署东京 green，需要重新合入上述三个补丁。

## 10. 验证

```bash
cd /Users/gaomengyuan/dev/sanyi-agent-platform
. .venv/bin/activate
python -m pytest -q          # 当前 84 passed
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


## 12. 2026-09-01 追加：币圈门四态精确订阅

- 问题定位：币圈侧不是源头没有区分，`type=new` 事件的 `text` 明确含“门上/门下”；真正缺陷是：
  1. `crypto_gate_signal` 的 `eventTypes` 只暴露 `open / formation`，无法在创建订阅时精确过滤单侧形成；
  2. 因子输出 `formation` 字段曾回退到当前门池 `gates?view=fresh`，门开/关后当前 `formation` 与事件发生时不一致；
  3. 推送/面板对形成门存在重复门类型和错误时间标签。
- 已改：
  - `crypto_gate_signal` 支持 `eventTypes`: `open` / `close` / `formation` / `formationAbove` / `formationBelow`，并输出 `OPEN / CLOSED / FORMATION_ABOVE / FORMATION_BELOW` 四态；默认 `open / close / formation`。
  - `type=new` 的形成侧只从事件 `text` 解析；输出 `openAt` 只给开、`closeAt` 只给关、`eventAt` 统一给事件时间。
  - Agent 工具 schema + system prompt 明确要求新订阅用精确四态值。
  - 前端/推送文案补齐四态，修复重复“天门/地门”、错误“开门时间”标签、矛盾 formation 展示。
- 旧订阅兼容：`eventTypes=["open"]`（生产 id 11）和 `["formation"]` 语义不变；`formation` 仍表示两侧形成门。
- 订阅面板手风琴分组（期货侧/币圈侧）已于 2026-09-01 部署；普通账号只显示期货侧。
- 已部署：2026-09-01 21:42 已同步生产并重启 `sanyi-agent-platform`，健康检查通过；前端需用户强刷。


## 13. 2026-09-01 晚间追加：ACE 迟到信号定位与修复

- 现象：币圈订阅推了 ACE 1h 地门形成，但图上实际形成时间早 5 根 1h K 线以上。
- 根因：
  1. 东京引擎在合约宇宙新纳入币种（或冷重扫）时，会给历史门补发 `type=new`，事件时间写的是**检测时刻**，不是门的 T2 形成时刻。
  2. 东京 green `scope=today` 的 sidebar pipeline 虽然用 T2 过滤了“今日门”，但输出的 `date/time` 仍是检测时刻；平台因子据此把迟到检测当成刚形成，baseline 也拦不住。
  3. 平台因子 `eventId` 带展示时间，修正时间口径后会导致旧信号被当作新信号重复推送。
- 已修：
  - 东京 green `runtime/publisher.py`：`type=new` 事件的展示时间改为 gate 的 `t2_str`（UTC→北京时间），同时输出 `formation_t2` / `detected_at` 便于排查。备份 `publisher.py.bak-20260901_before-formation-t2-time`。
  - 平台 `crypto_gate_signal.eventId` 改为稳定 ID，不随展示时间变化；形成门与开门统一为 `crypto-gate:{key}:signal`（同 key 只推一次），关门为 `crypto-gate:{key}:close`。
  - 平台 `crypto_leverage` 的 selectedSince 老门过滤扩展到事件线信号：`eventAt`（形成/动作时间）早于 `selectedSince` 的币圈事件不推。
  - 已对生产 crypto 订阅做一次受控 re-baseline：服务停机期间把当前命中全部记为已推送，避免历史迟到门和重复 ID 补推；之后只有真正的新事件才推。
- 已重启：东京 `sanyi-crypto-green-web` 与平台 `sanyi-agent-platform` 均已重启并健康检查通过。
- 注意：若未来用 git 重新部署东京 green，需要重新合入本补丁。


## 14. 2026-09-01 追加：MA208 门价/现价位置过滤

- 需求：筛选“地门打开”时，要求门价在 MA208 附近或以上；门价、现价两种锚点都支持，默认出条件用门价。
- 参数（已加入 4 个因子）：
  - `ma208Anchor`: `gatePrice`（默认，门价） / `currentPrice`（现价）
  - `ma208Mode`: `near`（附近 ±ma208TolerancePct%） / `above`（以上） / `nearOrAbove`（附近或以上）
  - `ma208TolerancePct`: 默认 1（±1%），仅 near / nearOrAbove 使用
  - 不传 `ma208Mode` 则完全不启用 MA208 过滤，旧订阅/旧查询行为不变。
- 适用因子：
  - 查询：`gate_condition`、`crypto_gate_condition`
  - 事件订阅：`futures_gate_signal`、`crypto_gate_signal`
- 输出新增：`ma208`、`gateMa208DistancePct`、`currentMa208DistancePct`；面板与 Pushplus 显示为“门价高于MA208 x%” / “门价低于MA208 x%” / “门价贴MA208”。
- Agent 工具与系统提示已支持该字段；前端筛选文案已显示。
- 已部署：2026-09-01 生产已同步并重启 `sanyi-agent-platform`。


## 15. 2026-09-02 追加：TRX 误推定位

- 现象：用户新建 id 18（5m/15m/1h 地门形成/开门，门价>MA208）后收到 TRX 推送，认为不符合 MA208。
- 定位：该 TRX 是 **旧订阅 id 17**（15m/1h 地门形成，无 MA208 条件）推的 15m 形成门，不是新订阅 id 18 推的。
  - TRX 15m：`gate_price=0.321`、`ma208=0.33`，门价低于 MA208，新订阅 id 18 已正确排除。
  - TRX 5m：`gate_price=0.321`、`ma208=0.32`，门价高于 MA208，所以 id 18 仍会保留 5m。
- 处理：id 17 已置为 `inactive`，避免旧条件继续推送；当前有效 MA208 订阅仅 id 18。
- 取消仍推送的防护：推送 worker 在评估完成后、逐条发送前会实时复核订阅是否仍 active；用户在评估期间取消订阅，本周期也不会继续发送。


## 16. 2026-09-02 追加：ONG 同门重复推送定位

- 现象：ONG 15m 地门在订阅 id 18 上被推了两次，一次 `open`、一次 `new`。
- 原因：id 18 的 `eventTypes=["open","formationAbove"]` 是“形成或开门”，但旧 `eventId` 把 open/new 当成两个 key；同一门先形成后开门时会先后命中两次。
- 修复：`crypto_gate_signal` 将形成门与开门统一为同一语义族 `crypto-gate:{key}:signal`，同 key 只推一次；关门仍保留 `crypto-gate:{key}:close` 独立语义。
- 语义优先级：同一 key 同时出现多个 signal 族事件时，`FORMATION_ABOVE`（门上形成）> `OPEN`（开门）> `FORMATION_BELOW`（门下形成），与产品规则一致：门上形成的第一动作是关门，不应被开门事件覆盖。
- 已对 id 18 做受控 re-baseline，旧 open/new 两把 key 清理为当前一把；生产已重启。


## 17. 2026-09-02 晚间追加：MA208 改为仅展示 + 15:00 后无实际推送定位

- 用户澄清：MA208 只用于展示，不应作为 id 18 的筛选条件。
- 处理：id 18 已移除 `ma208Mode/ma208Anchor`，只保留 `frequencies/gateTypes/eventTypes`；MA208 与距离百分比继续在面板和 Pushplus 展示。
- 15:00–19:40 的推送排查：
  - 之前看到的 16:01–16:09 `last_pushed_at` 是 baseline 陈旧标记（只记录不发送），不是真实推送；15:00 后 id 18 实际没有成功发送过 Pushplus。
  - 币圈事件流并非没有信号，15:00 后有 17 条地门形成/开门候选，但都被 MA208 筛选拦下，或门已离开 fresh 池无法取到 MA208。
- 已按新条件（不加 MA208 筛选）对 id 18 重新 baseline，当前 21 条命中全部标记为已存在、不补推；后续新门事件按新条件正常推送。