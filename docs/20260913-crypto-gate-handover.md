# 最新交接请先读同目录 20260913-crypto-gate-handover-v2.md

# 币圈门/推送/专业看板 交接文档（2026-09-13）

## 1. 环境
- 信号推送平台：qh.shhghf.com，服务 `sanyi-agent-platform`（/home/ubuntu/sanyi-agent-platform）
- 币圈门引擎：tokyo-crypto（167.179.69.189），supervisor 服务
  - `sanyi-crypto-green-engine`：门检测 + 因子快照 + 发布
  - `sanyi-crypto-green-web`：专业看板/API（bq.shhghf.com/tv）
- Pushplus：用户 158****8517，token 尾号 `a0c0`，接口正常

## 2. 业务口径（用户确认）
- 默认门引擎：**jinmen2323（严格门 = 2323 结构形成的门）**
- 严格门是现行门的子集：严格门 ⊆ jinmen2（现行门）
- 面板与推送必须走同一套门：`_active_registry()` + `latest_valid`
- 每 (sym, freq, type) 只保留最新一扇门（天门/地门各一）
- 诀（jue）已从看板移除，只保留门
- 专业看板合约池 = 全量 761 个 USDT 永续（可搜索、可画门）
- 推送因子池 = 按 24h 成交额筛选的前 200：
  - min_turnover = 1,000,000
  - max_spread_bps = 25
  - 不排除股票/商品、不看 OI、不看上市天数

## 3. 已完成的修复
### 东京引擎/Web 后端
1. `runtime/contracts.py`：面板宇宙（full 761）与推送因子池（top200）解耦。
2. `runtime/factors.py`：
   - 旧快照重算前不再丢弃；
   - 门边沿 pair 优先计算（edge priority）；
   - 合约观测缺口时保留 factor_map。
3. `runtime/engine.py` / `web/app.py`：
   - 门引擎模式变化时清空因子缓存，避免旧模式缓存继续推送；
   - 因子门池使用 `latest_valid`；
   - 冷启动诀投影跳过，热态随 bar 补齐。
4. `runtime/publisher.py`：chart_gates 快照使用 `latest_valid`。
5. `runtime/chart_gates.py`：
   - 图表层再按 (sym,freq,type) 只保留最新门；
   - 不再计算/返回 jue。
6. Web 配置：
   - `SANYI_CRYPTO_CHART_GATES_SNAPSHOT_MAX_AGE=3600`，避免引擎发布间隙被判 stale 导致门消失。
7. 引擎配置：
   - `SANYI_CRYPTO_FULL_CONTRACT_UNIVERSE=1`
   - `SANYI_CRYPTO_FACTOR_TOP_N=200`
   - `SANYI_CRYPTO_FACTOR_MIN_TURNOVER=1000000`
   - `SANYI_CRYPTO_FACTOR_MAX_SPREAD_BPS=25`
   - `SANYI_CRYPTO_FACTOR_BUDGET_SECONDS=5`
   - `SANYI_CRYPTO_COLD_SYMBOL_BATCH=50`
   - `SANYI_CRYPTO_SKIP_COLD_RECOMPUTE=1`

### 青岛推送平台
1. `app/engine/subscription_pusher.py`：
   - 修复币圈 v4 门时间完整格式被误判为老门导致不推送的 bug；
   - 增加 PUSH_SENT/PUSH_FAIL 日志（含 pushplus message id）。
2. 推送模式与东京 `_active_registry()` 对齐；模式变化会重建因子缓存。

## 4. 当前前端状态（重要）
- 专业看板已回退到稳定版 JS，MACD/RSI 恢复正常：
  - `web/static/tv_chart_restore.js`（内容 = 改动前稳定版）
  - `chart.html` 引用 `/static/tv_chart_restore.js?v=1`
- 该稳定版仍有两个旧问题：
  1. 门线锚点 T1 早于当前可视窗口时会被 `drawable_on_current_bars === false` 跳过；
  2. 切换级别时门线可能在 K 线加载前绘制，导致 `Cannot create horizontal_ray shape`。
- 因此：BTC 等品种在 5m/15m/1h 图上可能看不到门，1d 正常。
- 之前的激进版本（gatesonly2~8、tv_chart.js 新版）已备份：
  - `tv_chart.js.bak.*`
  - `tv_chart.js.broken-pileup-*`
  - 不要恢复这些版本（会造成指标重复/错位）。

## 5. 剩余问题与下一步（只改门线，不再碰指标）
目标：在 `tv_chart_restore.js` 基础上做最小修改：
1. 在 `syncOverlays` 门线绘制处：
   - 去掉 `if (g.drawable_on_current_bars === false) return;`
   - `anchor = g.t1_time`；若 `anchor < bars[0].time`，把 anchor 设为 `bars[0].time`；
   - 若 `anchor > bars[bars.length-1].time`，跳过；
   - T1 缺失仍跳过。
2. 调整绘制时序：
   - `chart-ready`：在 `loadKlines(...).then(...)` 完成后再 `scheduleOverlaySync`；
   - `symbol-change` / `interval-change`：在 `runKlineEvent(...)` 的 afterLoad 回调里先 `chart.resetData()`，再 `scheduleOverlaySync`；
   - **不要**新增/删除指标，不要调用 `_createStudies` 或 `removeAllStudies`，不要扩展价格轴。
3. 发布方式：
   - 复制为新文件 `tv_chart_restore2.js`，修改 `chart.html` 引用，强制 APP 壳加载新文件（壳对 query 缓存不可靠）。
4. 验证：
   - BTC 5m/15m/1h/1d 均能看到最新门线；
   - MACD/RSI 仍然正常；
   - 通过 `client_logs/frontend.log` 观察门 shape 成功/失败。

## 6. 数据事实（供核对）
- 严格门下 BTC 当前只有 4 扇最新门：
  - 5m 天门、1d 地门、1d 天门、1w 天门（无 15m/1h 门）
- CHIP 严格门 1h = 0 扇；昨晚看到的 CHIP 1h 关门属于 jinmen2（现行门）
- 严格门是子集，面板和推送统一用严格门后，CHIP 1h 不再显示是正常现象

## 7. 关键文件备份位置
- 东京：`/opt/sanyi-green/backups/crypto-*.py`、`*.before-*.py`
- 前端：`/opt/sanyi-green/current/apps/crypto/web/static/tv_chart.js.bak.*`
- 青岛：`/home/ubuntu/sanyi-agent-platform/backups/subscription_pusher.before-*.py`

## 8. 2026-09-13 午后追加：图表门线修复 + 15m 推送核查

### 已完成
- 前端新文件 `tv_chart_restore2.js`（chart.html 引用 v=3）：
  - 去掉 `drawable_on_current_bars === false` 的提前 return；
  - T1 锚点早于当前窗口首根 K 时钳到 `bars[0].time`，晚于末根 K 跳过，T1 缺失跳过；
  - `chart-ready` 改为 `loadKlines(...).then(...)` 完成后再 `scheduleOverlaySync`；
  - symbol/interval change 在 afterLoad 里先 `chart.resetData()` 再 `scheduleOverlaySync`；
  - createShape 失败记 `tv-gate-shape-error`，并清 draw key 允许 800ms retry / 120s 兜底重绘；
  - 未新增/删除指标，未动 `_createStudies`/`removeAllStudies`/价格轴。
  - 备份：`chart.html.bak.restore2-*`、`tv_chart_restore.js.bak.restore2-*`（东京 static 目录）。
- 东京引擎因子调度修复（`runtime/factors.py` + `web/app.py`）：
  - 原排序 5m 新 bar 每 tick 都排在 15m/1h/1d 前面，5s 预算被 5m 池（约 194 pair）吃满，低频因子在切门模式后永远补不回来；
  - 改为 edge pair 优先 + 级别轮转交错（`_factor_round_robin_cursor`）；
  - 备份：`runtime/factors.py.bak.roundrobin-*`、`web/app.py.bak.roundrobin-*`；引擎已重启，因子池正在按 5m→15m→1h→... 逐级重建。
- 青岛订阅 25/26 已做严格门 re-baseline：
  - 清空旧 jinmen2 match_keys，`baseline_at=2026-09-13T05:07:09Z`；
  - DB 备份 `data/platform.sqlite3.bak.*before-strict-rebaseline`。

### 信号核查结论
- `gate_engine_mode.json` 显示切到 jinmen2323 的时间是 `2026-09-13 02:20:22 UTC`（北京 10:20:22）。
- 严格门 15m 开门事件（strict ledger `first-action open`）：北京 00:00–09:53 共 29 条；10:20 切换后到当天中午为 0 条。
- 青岛推送日志：10:13:43 之前按旧 jinmen2 口径有 14 条 PUSH_SENT；之后无 PUSH_SENT，与“切严格门后没有新的 15m 开门事件”一致。因此今天上午没有漏推严格门 15m 开门。
- CHIP 严格门 5m 已关地门后端存在：`jinmen2323_CHIPUSDT_5m_di_0912_1815`，门价 0.0486，T1 在当前 5m 窗口内；数据正常，刷新前端后应能看到。

### 待观察
- 因子池重建中（当前 5m 已满 197，15m/1h 逐步补齐）。edge pair 永远最优先，新 15m 开/关门即使对应 pair 尚未进入因子池也会当 tick 计算并推送。
- 需要用户强刷 `bq.shhghf.com/tv`，验证 BTC 5m/15m/1h/1d 门线与 MACD/RSI。

### 2026-09-13 午后补充：MACD 副图“走形”修复
- 现象：门线修复后 MACD 仍显得走形。用 Playwright + OCR/像素对比定位：
  - MACD 数值与 `/api/klines` 后端序列逐点一致，指标计算和查值没问题；
  - 真正问题是 pane 比例错误：逐个 `pane.setHeight()` 每次调用都触发 TV 重排，
    得到 `主图 58% / MACD 10% / RSI 32%`，RSI 抢走主图高度，视觉上 MACD 被压变形。
- 修复：`_balanceStudyPanes` 改用 `chart.setAllPanesHeight([main, macd, rsi])` 一次性设置，
  比例恢复 `80% / 10% / 10%`（实测 610/77/76），刷新后布局稳定。
- 发布：`tv_chart_restore2.js?v=4`（chart.html 已改引用）；旧版备份
  `tv_chart_restore2.js.bak.v3-*`。
- 验证：Playwright 生产页实测 panes=[610,77,76]；MACD 副图像素与后端序列一致；
  RSI3 显示正常（如 22.21）。用户需再次强刷。

### 2026-09-13 午后补充：MACD 小数精度修复（SAND 15m 柱子拉不开）
- 根因：`runtime/klines.py` 的 `/api/klines` 对 MACD 固定 `round(..., 4)`；SAND 15m
  的 DIF/DEA/HIST 量级约 5e-5，四舍五入后只剩 0.0001 粒度和大量 0.0，前端 MACD
  自然变成“全平横线”，且图例/坐标轴 precision=4 显示成 0.0000。
- 修复：
  - `runtime/klines.py`：MACD 输出改 `_smart_round()`（>=100 两位、>=1 四位、
    >=0.01 六位、其余八位），SAND 现在能输出 0.00005510 这一量级的真实波动；
  - `runtime/ws.py`：WS 指标快照同样改 `_smart_round()`；
  - 前端 `tv_chart_restore2.js`：MACD study `format.precision` 4 → 8，
    `chart.html` 引用升至 `v=5`；
  - 已重启 `sanyi-crypto-green-web`。备份：
    `runtime/klines.py.bak.macd-precision-*`、`runtime/ws.py.bak.macd-precision-*`、
    `tv_chart_restore2.js.bak.v3-*`。
- 验证：SAND 15m MACD 图例显示 `0.00005510 / 0.00003476 / 0.00002034`，
  坐标轴 `0.00020000`；与 Bybit 0.00004 量级一致。
- 门检测不受影响：门引擎（jinmen2 / jinmen2323）在 `_engine_tick` 里直接使用
  `_snap['dif']/_snap['dea']/_snap['hist']` 的原始 numpy 数组做检测，从未经过
  `/api/klines` 的 round(4)。因此这次是展示/因子 API 的显示精度 bug，不是门形成
  逻辑的精度 bug。SAND 严格门当前有 15m 已关地门/15m 门下天门等，属正常门池。

### 2026-09-13 傍晚：新订阅 27/28 全链路核查与修复
- 新订阅：27=币圈 5m/15m/1h 地门开门做多；28=币圈 5m/15m/1h 天门关门做空。
- 核查发现两个会漏推的 bug：
  1. 东京因子快照 `door` 没有输出 `open_at/close_at`，青岛 pusher 只能拿
     `t1/t2/crossTime` 判断 baseline 新旧；于是“订阅前形成、订阅后开门/关门”的
     门全部被 `_is_stale_baseline_match` 判为老门，新边沿被静默吞掉。
  2. `_match_epoch` 把 `YYYY-MM-DD HH:MM:SS` 的 naive 时间当成北京时间解析；
     v4 因子的时间实际是东京引擎 UTC naive，整体偏了 +8h，即使补了
     `openAt/closeAt` 也会被误判为 baseline 之前。
- 修复：
  - `/opt/sanyi-green/current/sanyi_core/sanyi_core/features/factors.py`：
    `_door_factors` 增加 `open_at/close_at`（备份 `factors.py.bak.openat-*`）；
  - 青岛 `app/factors/v4_common.py`：door cell 输出 `openAt/closeAt`，
    `eventId` 追加 `open_at:close_at`，同门再次开/关也能产生新事件 ID；
  - 青岛 `app/engine/subscription_pusher.py`：`_match_epoch` 对完整时间戳按
    UTC 解析，短格式 `MM/DD HH:MM` 仍按北京时间；
  - 重启东京引擎 + 青岛平台；订阅 27/28 清空旧 match_keys 并重新 baseline
    （baseline_at=2026-09-13T07:43:35Z）。
- 当前链路状态：
  - 东京因子快照已带 `open_at/close_at`，青岛 evaluator 已能看到
    `openAt/closeAt`；
  - 现有 WAVES 15m close 信号 `closeAt < baseline` 被正确标记为 stale，不会误补推；
  - 下一个 baseline 之后的新开/关门边沿（在因子 top200 池内）应正常 PUSH_SENT。
- 注意：因子池按 24h 成交额 top200 + spread<=25bps 筛选；池外品种的门事件不会推，
  这是产品口径，不是故障。

### 2026-09-13 傍晚补充：edges 订阅只推“第一次动作”
- 现象：27/28 推送了 AVAX/W 5m 地门“开+关”。用户要求 edges=open/close 表示
  **第一次动作**，开+关后的再次开/关不要推。
- 根因：因子快照只有 open_edge/close_edge（最新一根 K 是否再次穿越门价），
  没有“本次穿越是不是第一次动作”的标志；青岛 _match_door 只要 edge=true 就匹配。
- 修复：
  - 东京 `sanyi_core/gates/registry.py`：recompute_lifecycle 记录
    `first_action_edge`（只有 `_emit_gate_action` 首次动作去重返回 True 才为 True）；
  - 东京 `sanyi_core/features/factors.py`：door 因子输出 `first_action`；
  - 青岛 `v4_common.py`：door cell 输出 `firstAction` 并写入 eventId；
    `_match_door` 的 `edges` 现在要求 `edge=true AND first_action=true`。
  - 订阅 27/28 已再次清空 match_keys 并重新 baseline。
- 备份：registry.py/features.py `*.bak.firstaction-*`；v4_common/pusher
  `*.bak.firstaction-*`。
- 验证：当前 WAVES 15m tian closeEdge=true 但 firstAction=false，evaluator 不再
  输出；27/28 当前均为 0 matches、0 error。

### 2026-09-13 自动化兜底
- 新增自检脚本 `scripts/check_subscription_pipeline.py`：
  - 检查每个 active 订阅 baseline、评估错误、各周期因子池覆盖、Pushplus token；
  - 手动运行：`.venv/bin/python scripts/check_subscription_pipeline.py`；
  - 失败时 `--alert` 会按状态翻转/每小时一次给 user 4 发 Pushplus 告警。
- 已安装 systemd timer：
  - `sanyi-sub-check.timer` 每 5 分钟运行一次；
  - `systemctl status sanyi-sub-check.timer` / `journalctl -u sanyi-sub-check.service` 查看。
- 这样以后新建/删除订阅后，链路问题会自动暴露并告警，不再依赖人工逐次排查。

### 2026-09-13 晚间：PEPE 5m 天门标注错位修复（v6）
- 现象：PEPE 5m 天门推送正常，但看板标注价格位置飘。
- 根因：
  1. 前端把 1d/15m/5m 所有最新门都画在当前 5m 图上，同价/近价门标签重叠，
     价格轴外的门线被裁掉后只剩“飘着的文字标注”；
  2. 门价离可见价格轴太远时，horizontal_ray 的线不可见但文字仍会渲染；
  3. 价格范围变化时 draw key 不变，旧标注不会主动重绘。
- 修复（`tv_chart_restore2.js?v=6`）：
  - `gatesSelectedForOverlay(raw, viewFreq)`：只画当前图表周期的门；
  - 画门前读取主图可见价格范围，门价超出可见范围（10% span 与 1% 价格取大者）
    时跳过，不再产生无线的漂浮标注；
  - 订阅 `onVisibleRangeChanged`：缩放/平移后 invalidate + 重绘门线，
    进入范围自动出现、离开范围自动移除。
- 验证：PEPE 5m 当前只画 5m 门；0.0033 天门线可见且与标签同价，0.0037 地门
  等价格轴外门被跳过（frontend.log 有 `out of price range` 诊断）。
- 备份：`tv_chart_restore2.js.bak.v5-*`。

### 2026-09-13 深夜：门价 T1 点四舍五入修复（v7）
- 用户确认：所有周期门必须画在一起（v6 的“只画当前周期”已回退）。
- 真根因：门检测代码把 `gate_price` 固定 `round(gate_price, 4)` 后写入 registry。
  低价币门价如 SKY 5m 实际 0.06187 被存成 0.0619，图表标注/线价与实际
  K 线最低/收盘价对不上（用户看到的 019 vs 187）。
- 修复：
  - `sanyi_core/gates/{di_men,tian_men,di_men_v2,tian_men_v2,di_men_2323,tian_men_2323}.py`
    全部改为 `"gate_price": float(gate_price)` / `float(geometry.gate_price)`；
  - d1/d2 等 MACD 差字段仍按原样保留；
  - 引擎已重启。registry 对同状态门会 `dict.update(raw_gate)`，下一轮检测后旧门价
    自动更新为全精度（已验证 SKY 5m 地门 0.0619 → 0.06187）。
  - 前端 `tv_chart_restore2.js?v=7`：恢复全周期门同图绘制；保留“价格轴外跳过 +
    可见范围变化重绘”，避免无线漂浮标注。
- 备份：`*.bak.gateprice-*`、`tv_chart_restore2.js.bak.v6-*`。
- 待观察：新引擎首个 chart_gates 快照发布后，SKY 5m 标注应从 0.0619 变为 0.06187。
- 已验证：chart_gates 快照发布后 SKY 5m 地门返回 0.06187（全精度），
  `/api/chart/gates/SKYUSDT/5m` 已确认。
