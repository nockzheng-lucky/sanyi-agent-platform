# 交接文档：走法破诀 × 门条件的交叉匹配

日期：2026-08-30
状态：已研判，未实现。等待用户确认后在新对话实现。

**2026-08-30 用户补充澄清**：他不希望每类叠加都写成一个新固定因子，希望平台支持
“AI 自动组合现有因子订阅的叠加”，即一个**定制化条件**，而不是新因子。本交接文档
已按这个方向更新，第 5 节是最终推荐架构。

## 1. 用户最新需求

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

## 2. 研判结论：能做到，而且应该做成“组合订阅条件”

**可以做到，而且比新固定因子更符合产品方向。**

正确做法不是新增 `futures_wave_jue_gate_cross` 这种写死因子，而是把订阅层升级为：

```text
订阅 = 一个或多个因子条件 + 连接规则（AND）
```

例如用户当前需求：

```json
{
  "name": "走2破20诀 + 下方有效+1级地门",
  "combineMode": "AND",
  "conditions": [
    {
      "factorKey": "wave_jue_combo",
      "filters": {"combos": ["walk2_break20"], "frequencies": ["5m", "15m", "1h"]},
      "role": "primary"
    },
    {
      "factorKey": "gate_condition",
      "filters": {"gateTypes": ["di"], "liveStatuses": ["已开", "无动作·门上"]},
      "joinWith": "primary",
      "joinOn": {"symbol": "symbol", "frequencyOffset": 1},
      "sideRule": "below"
    }
  ]
}
```

Agent 只需要描述自然语言，LLM 生成上面的条件 JSON，平台按条件组合执行。
以后“走2破20 + 头肩顶”“走B破80 + 天门”等叠加都不需要新因子，只新增条件。

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

## 5. 最终推荐：组合订阅条件层（不是新因子）

### 5.1 数据模型

保留 `signal_subscriptions`，新增子表：

```text
subscription_conditions
  id
  subscription_id
  factor_key
  filters_json
  role              -- primary / context
  join_with         -- 关联到哪个 condition 的 role
  join_symbol       -- 默认 symbol == symbol
  frequency_offset  -- 0 同周期，1 表示 +1 级父周期
  side_rule         -- below / above / null
  sort_order
```

订阅对象示例：

```json
{
  "id": 1,
  "name": "走2破20诀 + 下方有效+1级地门",
  "conditions": [
    {
      "role": "primary",
      "factorKey": "wave_jue_combo",
      "filters": {"combos": ["walk2_break20"], "frequencies": ["5m", "15m", "1h"]}
    },
    {
      "role": "context",
      "joinWith": "primary",
      "factorKey": "gate_condition",
      "filters": {"gateTypes": ["di"], "liveStatuses": ["已开", "无动作·门上"]},
      "frequencyOffset": 1,
      "sideRule": "below"
    }
  ]
}
```

### 5.2 评估流程

1. 对每个 condition 调用 `registry.evaluate(..., audit=False)`
2. 将各因子结果统一成中间行：
   ```text
   symbol, frequency, side, kind, payload
   ```
3. 以 `primary` 行为主表：
   - 关联 `context` 行：`symbol` 相同 + `frequency` 按 offset 映射
   - `sideRule=below`：context 门价 < primary 当前价
   - `sideRule=above`：context 门价 > primary 当前价
4. 只输出同时命中所有 context 的 primary 行
5. 前端订阅面板 / Pushplus 显示的是组合后的信号

### 5.3 Agent 工具改造

扩展 `sanyi_create_subscription`：

```json
{
  "name": "走2破20诀 + 下方有效地门",
  "conditions": [
    {
      "factorKey": "wave_jue_combo",
      "filters": {"combos": ["walk2_break20"]}
    },
    {
      "factorKey": "gate_condition",
      "filters": {"gateTypes": ["di"], "liveStatuses": ["已开", "无动作·门上"]},
      "join": {"frequencyOffset": 1, "sideRule": "below"}
    }
  ]
}
```

系统提示中增加规则：

- 用户要求“叠加 / 同时满足 / 交叉匹配”时，创建一个订阅、多个 conditions
- 不是创建多个订阅让用户自行交叉

### 5.4 兼容与隔离

- 单因子订阅：`conditions` 只有一个元素，行为不变
- 普通用户看不到币圈因子，组合条件也只能引用自己可见的因子
- 因子结果统一转换失败或数据源异常时，该条件记为 error，不影响其他订阅
- 推送去重仍按组合结果行生成 match_key

### 5.5 测试

新增测试文件：

```text
tests/test_composite_subscriptions.py
```

覆盖：

- 单条件兼容旧订阅
- 双条件 AND：symbol + parent frequency
- `below / above` 方向过滤
- 条件不匹配时不输出
- 组合结果出现在 `/api/v1/signal-subscriptions/matches`
- Pushplus worker 对组合结果只推一次
- Agent 工具 `sanyi_create_subscription` 接受 conditions

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

确认后，新对话直接实现“组合订阅条件层”，再按正常订阅流程创建订阅。

---

## 8. 已知边界

- 当前 `wave_jue_combo` 和 `gate_condition` 都是最新快照，不区分破诀/走法先后
- 平台订阅目前按单因子创建；组合订阅条件层是解决交叉匹配的通用路径
- 币圈不在本需求范围，且币圈因子只对影子账号可见
