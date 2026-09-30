# 交接文档（2026-09-13 深夜版）

> 新对话先读这一份。历史排障记录在同目录 `20260913-crypto-gate-handover.md`。

## 1. 环境

| 角色 | 主机 | SSH alias | 目录/服务 |
|---|---|---|---|
| 推送/Agent 平台 | qh.shhghf.com (101.35.210.32) | qh.shhghf.com | `/home/ubuntu/sanyi-agent-platform`，systemd `sanyi-agent-platform` |
| 币圈门引擎 + 看板 | 东京 167.179.69.189 | tokyo-crypto | `/opt/sanyi-green/current/apps/crypto`，supervisor `sanyi-crypto-green-engine` / `sanyi-crypto-green-web` |
| 专业看板 | bq.shhghf.com/tv | — | 东京 web 端口 19102，静态目录 `web/static` |

当前门引擎模式：**jinmen2323 严格门**（`gate_engine_mode.json`，10:20 CST 切换）。

## 2. 当前 active 订阅（user 4）

| id | 名称 | 因子/条件 | baseline |
|---|---|---|---|
| 27 | 币圈 5m/15m/1h 地门开门做多 | `crypto_door`, gateTypes=[di], edges=[open] | 2026-09-13T08:27:48Z |
| 28 | 币圈 5m/15m/1h 天门关门做空 | `crypto_door`, gateTypes=[tian], edges=[close] | 2026-09-13T08:27:48Z |

语义：`edges` 现在表示**第一次动作**，不是“开+关后的再次开/关”。

## 3. 今天已修复并验证的关键问题（新对话务必知道）

1. **图表门线画不出来**：`tv_chart_restore2.js` 已修 anchor 钳制/时序/失败重试。
2. **MACD 精度**：`/api/klines` MACD 固定 round(4) → 改 `_smart_round()`；前端 precision 8。
3. **MACD 副图比例**：`_balanceStudyPanes` 改用 `chart.setAllPanesHeight([main, macd, rsi])`。
4. **15m 因子池饥饿**：`runtime/factors.py` 因子调度改为 edge 优先 + 级别轮转，5m 不再永远占满预算。
5. **新订阅漏推老门新边沿**：东京 factor 增加 `open_at/close_at/first_action`；青岛 `_door_cell` 透传并写入 eventId；`_match_door` 的 edges 要求 `edge && first_action`。
6. **时区解析错误**：`_match_epoch` 完整时间戳按 UTC 解析（东京时间都是 UTC naive）。
7. **门价四舍五入**：6 个门检测文件 `round(gate_price,4)` 改为 `float(gate_price)`；SKY 5m 已从 0.0619 修正为 0.06187。
8. **门标注错位**：前端 v7 恢复“所有周期门画在一起”；价格轴外的门跳过绘制，缩放/平移后重绘。

当前前端版本：`chart.html` 引用 `/static/tv_chart_restore2.js?v=7`。

## 4. 推送链路（正常时）

```
东京引擎（原始 MACD/dif/hist 检测 jinmen2 + jinmen2323）
  → gate_registry_2323.json / gate_events_2323.sqlite3
  → runtime_snapshots/factor_store（v4 factor，含 open_at/close_at/first_action）
  → 青岛 evaluate_subscription (crypto_door)
  → subscription_match_keys 去重 + baseline + stale 判断
  → Pushplus（user_push_channels，用户 4 token）
```

关键日志：
- 东京引擎：`/var/log/supervisor/sanyi-crypto-green-engine.log`
- 青岛推送：`journalctl -u sanyi-agent-platform | grep PUSH_`
- 前端：东京 `/opt/sanyi-green/current/apps/crypto/client_logs/frontend.log`

## 5. 自检工具（已自动化）

```bash
ssh qh.shhghf.com
cd /home/ubuntu/sanyi-agent-platform
.venv/bin/python scripts/check_subscription_pipeline.py --alert
systemctl status sanyi-sub-check.timer
journalctl -u sanyi-sub-check.service
```

systemd timer 每 5 分钟运行；失败时按状态翻转/每小时一次给 user 4 发 Pushplus 告警。
检查项：baseline、评估错误、各周期因子池覆盖、Pushplus token。
edges 门订阅缺失某个周期只会 WARN，不会 FAIL（因子池重建期间是正常现象）。

## 6. 常用排查命令

```bash
# 订阅/去重/基线
ssh qh.shhghf.com
cd /home/ubuntu/sanyi-agent-platform
.venv/bin/python - <<'PY'
import sqlite3
con=sqlite3.connect('data/platform.sqlite3'); con.row_factory=sqlite3.Row
for r in con.execute("select id,name,status,baseline_at from signal_subscriptions where status='active'"): print(dict(r))
for r in con.execute("select * from subscription_match_keys where subscription_id in (27,28) order by last_seen_at desc limit 10"): print(dict(r))
PY

# 严格门事件 / 因子池
ssh tokyo-crypto
cd /opt/sanyi-green/current/apps/crypto
python3 - <<'PY'
import json, sqlite3
con=sqlite3.connect('state/gate_events_2323.sqlite3'); con.row_factory=sqlite3.Row
for r in con.execute("select * from gate_event order by event_seq desc limit 10"):
    print(r['event_at'], r['event_kind'], r['gate_id'])
d=json.load(open('runtime_snapshots/factor_store/factor_manifest.json'))
print('factor pairs', len(d.get('pairs') or {}), 'updated', d.get('updated_at'), 'pid', d.get('engine_pid'))
PY

# 门引擎模式 / 服务
ssh tokyo-crypto
cat /opt/sanyi-green/current/apps/crypto/gate_engine_mode.json
sudo supervisorctl status
```

## 7. 当前状态 / 已知事项（截至 2026-09-13 21:25 CST）

- 东京引擎已重启多次，当前 pid 以 `sudo supervisorctl status` 为准；
  因子池正在按 5m→15m→1h→1d→1w→1M 逐级重建，**这是正常过程**。
- 重建期间：
  - 新门边沿事件（edges 订阅）会被最高优先级补算，不会漏；
  - 其它因子查询可能暂时 503/缺周期，自检脚本会 WARN。
- chart_gates 快照已发布 SKY 5m 门价 0.06187，API 已验证。
- 当前 27/28 评估正常：0 matches、0 error（没有新的“第一次动作”事件时不推是正确行为）。
- Pushplus token user 4 已绑定且 enabled；今天 PUSH_SENT 均返回成功 pushplus_id，无 PUSH_FAIL。

## 8. 不要做 / 容易踩坑

- 不要在 symbol/interval change 里 `removeAllStudies()` 或重复 `_createStudies()`，会造成指标重复/错位。
- 不要恢复 `tv_chart.js.bak.*` / `tv_chart.js.broken-pileup-*` 旧版本。
- 不要用逐个 `pane.setHeight()` 调副图比例，必须用 `chart.setAllPanesHeight`。
- 不要把门价在检测层 `round(..., 4)`；展示层可以用 `_fmt_price/_smart_round`。
- 不要混淆时间时区：东京引擎产生的 `YYYY-MM-DD HH:MM:SS` 是 **UTC naive**；
  青岛解析完整时间戳按 UTC，短格式 `MM/DD HH:MM` 才按北京时间。
- 不要在生产服务器 git push（生产无 GitHub key）。
- 每次重启东京引擎都会清空内存 factor_map/series，重建需要时间；不要因为
  “1h 因子池暂时为空”就反复重启引擎。

## 9. 如果用户反馈“没收到推送”

按顺序查：
1. 严格门账本有没有符合订阅的 **first-action** 事件：
   `gate_events_2323.sqlite3`，event_kind=`first-action`，freq/gate_type/edge 匹配 27/28。
2. 事件品种是否在因子池 top200 内（`factor_manifest.json`）；池外是产品口径，不推。
3. 因子快照该 pair 的 `open_at/close_at/first_action` 是否与事件一致。
4. 青岛 `subscription_match_keys` 是否已存在该 eventId；`baseline_at` 是否早于事件。
5. `journalctl -u sanyi-agent-platform | grep PUSH_` 看 PUSH_SENT/PUSH_FAIL。
6. 若都正常但用户没收到，查 Pushplus token / 用户手机侧（平台侧 PUSH_SENT 已提交）。

## 10. 备份位置

- 东京：
  - `sanyi_core/gates/*.bak.gateprice-*`
  - `sanyi_core/gates/registry.py.bak.firstaction-*`
  - `sanyi_core/features/factors.py.bak.openat-*` / `.bak.firstaction-*`
  - `apps/crypto/runtime/factors.py.bak.roundrobin-*`
  - `apps/crypto/runtime/klines.py.bak.macd-precision-*`
  - `apps/crypto/web/static/tv_chart_restore2.js.bak.v5-*` / `.bak.v6-*`
  - `apps/crypto/web/static/chart.html.bak.restore2-*`
- 青岛：
  - `app/factors/v4_common.py.bak.openat-*` / `.bak.firstaction-*`
  - `app/engine/subscription_pusher.py.bak.timezone-*` / `.bak.firstaction-*`
  - `data/platform.sqlite3.bak.*`（多次 baseline 前备份）
