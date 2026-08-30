# 交接文档：走法破诀 × 门条件的交叉匹配

日期：2026-08-30
状态：已研判，未实现。等待用户确认后在新对话实现。

## 1. 用户最新需求

Agent 已经复述了需求，用户要的是**同一信号内完成交叉匹配**，而不是拆成 4 个订阅后人工交叉。

### 1.1 多头信号

- 期货全市场，不含币圈
- 走法 = 走2
- 状态 = 20诀破诀（空转多）
- 下方存在有效**地门**
- 地门状态：`已开` 或 `无动作·门上`

### 1.2 空头信号

- 期货全市场，不含币圈
- 走法 = 走B
- 状态 = 80诀破诀（多转空）
- 上方存在有效**天门**
- 天门状态：`已关` 或 `无动作·门下`

### 1.3 周期

用户问过 5m/15m/1h 是否足够；尚未最终确认。默认先按 `5m/15m/1h` 设计，参数可扩展。

---

## 2. 研判结论：能做到

**可以做到，不需要拆成 4 个订阅。**

正确做法是新增一个**组合因子**，例如：

```text
factorKey: futures_wave_jue_gate_cross
```

它内部同时读取：

1. qh `/api/market/jue-direction`：走法 + 破诀当前快照
2. qh `/api/gates?view=all`：门类型 + 门生命周期 + 门价 + 当前价

然后在服务端按 `symbol + parent_freq` 做交叉匹配，输出一条组合信号。这样 Agent 和订阅系统都把它当成**一个因子**，一个订阅即可，Pushplus 也会推送组合结果。

---

## 3. 当前平台状态（新对话先读这里）

### 3.1 平台仓库

```text
/Users/gaomengyuan/dev/sanyi-agent-platform
branch: main
HEAD: 90cf236 feat(chat): add server-side chat history
```

启动/测试：

```bash
cd /Users/gaomengyuan/dev/sanyi-agent-platform
. .venv/bin/activate
python -m pytest -q
```

### 3.2 当前期货因子（domain=futures）

```text
dimen_gate_signal
jue_direction
gate_condition
wave_jue_combo
```

- `wave_jue_combo`：当前快照组合
  - `walk2_break20`：走2 + 20诀破诀
  - `walkB_break80`：走B + 80诀破诀
  - `walkC_break20`：走C + 20诀破诀
- `gate_condition`：门条件
  - `gateTypes`: `tian` / `di`
  - `liveStatuses`: `已开` / `开+关` / `无动作·门上` / `无动作·门下` / `已关` / `删除`

### 3.3 订阅 / 推送 / 历史

- 订阅按单因子创建：`app/signal_subscriptions.py`
- 订阅面板 SSE：`app/api/signal_subscriptions.py`
- Pushplus 是用户级通道：`app/push_channels.py`
- 服务端聊天历史：`app/chat_history.py`
- Agent 自然语言筛选：`app/agent/filter_store.py`

### 3.4 期货侧因子 API（已部署）

- 公网：`https://qh.shhghf.com/api/factors/catalog`
- 单品种：`GET /api/factors/{sym}/{freq}`
- 生产 Green release：
  - 当前：`/home/ubuntu/projects/sanyi-green/releases/20260830-factors-api`
  - 回滚：`/home/ubuntu/projects/sanyi-green/releases/20260826-rtgap-fix`
- 相关分支：
  ```text
  /Users/gaomengyuan/dev/sanyi/.worktrees/futures-factor-api
  branch: codex/futures-factor-api
  HEAD: 63a520e6
  ```

---

## 4. 数据字段与交叉匹配规则

### 4.1 `wave_jue_combo` 输出 cell 关键字段

```text
symbol, name, sector, contract, frequency, state, direction, side,
broken, price, rsi3, walkState, walkCode, walkMark,
updatedAt, generatedAt, comboKey, comboLabel
```

### 4.2 `gate_condition` 输出 gate 关键字段

```text
symbol, name, contract, frequency, gateType, gatePrice,
currentPrice, liveStatus, formation, openAt, closeAt, deleteAt,
isFirst, key, t1Time, t2Time, crossTime, xAbove, xCrosses,
openEdge, closeEdge, pullbackConfirmed
```

### 4.3 交叉匹配算法（建议）

对每条 combo cell：

1. 取 cell 的 `symbol`
2. 计算父级周期：
   ```text
   5m  -> 15m
   15m -> 1h
   1h  -> 1d
   1d  -> 1w
   1w  -> 1M
   ```
3. 在 `gate_condition` 门池中查找同一 `symbol` + 父级 `frequency` 的门：

   **多头：**
   ```text
   gateType = di
   liveStatus in [已开, 无动作·门上]
   gatePrice < currentPrice   # 门在下方
   ```

   **空头：**
   ```text
   gateType = tian
   liveStatus in [已关, 无动作·门下]
   gatePrice > currentPrice   # 门在上方
   ```

4. 命中则输出组合信号；可保留门对象和走法破诀 cell 的完整字段。

### 4.4 周期“+1 级”待确认

用户原文是“下方存在有效的 +1 级别门”。上面映射是建议口径，实现前需要用户确认：

- `15m` 信号看 `1h` 门，还是看同周期 `15m` 门？
- `1h` 信号看 `1d` 门？

---

## 5. 建议实现方案

### 5.1 新因子

```text
factorKey: futures_wave_jue_gate_cross
name: 走法破诀×门交叉（多空）
domain: futures
```

参数建议：

```json
{
  "frequencies": ["5m", "15m", "1h"],
  "combos": ["walk2_break20", "walkB_break80"],
  "limit": 30
}
```

输出：

```json
{
  "factorKey": "futures_wave_jue_gate_cross",
  "signal": "LONG / SHORT / MIXED / NONE",
  "summary": "命中 N 条：多头 M / 空头 K ...",
  "details": {
    "matchedCells": 5,
    "cells": [
      {
        "symbol": "RB0",
        "frequency": "15m",
        "side": "long",
        "combo": "walk2_break20",
        "gate": {
          "frequency": "1h",
          "gateType": "di",
          "liveStatus": "已开",
          "gatePrice": 3500.0,
          "currentPrice": 3520.0
        }
      }
    ]
  }
}
```

### 5.2 数据读取与隔离

- 复用 `jue_direction._fetch_raw()` 的模块级短缓存
- 新增/复用 `gate_condition._fetch_gates()` 的模块级短缓存
- 只在因子被调用时请求，不做后台轮询
- 数据源异常只影响该因子，不阻断平台其他功能

### 5.3 订阅

- 该组合因子注册后，Agent 工具会自动出现
- 用户确认条件后，调用 `sanyi_create_subscription` 即可创建**一个**订阅
- 左侧订阅面板和 Pushplus 按组合因子输出推送

### 5.4 测试

新增测试：

- `tests/test_futures_wave_jue_gate_cross.py`
- monkeypatch `jue_direction._fetch_raw` 和 `gate_condition._fetch_gates`
- 覆盖：
  - 多头命中（di 下方门 + walk2 break20）
  - 空头命中（tian 上方门 + walkB break80）
  - 父级周期映射
  - 无门 / 门状态不匹配 / 门在错误方向时不命中
  - REST evaluate 通过
  - 普通用户可见、币圈影子隔离不受影响

---

## 6. 精确时序升级路径（后续再做）

当前交叉匹配仍是**最新快照**。用户还要求过精确顺序：

- `走2破20诀`：破诀时仍处于走2
- `走C破20诀`
- `走2破20诀 + 头肩顶`

期货侧 `/api/factors/{sym}/{freq}` 已经上线，其中包含：

```text
factors.wave.last_transition
factors.jue.rsi3.break_index / formed_index / bars_since_break
factors.gate.latest
```

后续可新增精确时序组合因子，但**头肩 HS 后端尚未实现**：
文档有 `POST /api/factors/hs/condition` 和 `/hs/universe`，但代码还没有。

---

## 7. 待用户确认事项（实现前必须确认）

1. 周期范围：是否只做 `5m/15m/1h`？
2. “+1 级门”映射：是否采用 `15m→1h`、`1h→1d` 的父级周期？
3. 门状态：
   - 多头地门：`已开` + `无动作·门上`，是否足够？
   - 空头天门：`已关` + `无动作·门下`，是否足够？
4. “有效门”的判定：是否排除 `删除` 状态即可？
5. 如果同品种同父周期有多个门，是否取**最近一个**门对象？

确认后，新对话直接实现组合因子，再按正常订阅流程创建订阅。

---

## 8. 已知边界

- 当前 `wave_jue_combo` 和 `gate_condition` 都是最新快照，不区分破诀/走法先后
- 平台订阅目前按单因子创建；组合因子是解决交叉匹配的最短路径
- 币圈不在本需求范围，且币圈因子只对影子账号可见
