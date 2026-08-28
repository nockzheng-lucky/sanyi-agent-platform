# 三易引擎 Skill：诀与破诀（80/20 诀方向）

> 本文档供用户自己的 Agent 自动阅读并封装工具，也供开发者手动接入。

## 1. 服务信息

- 平台基础域名：`https://signal.shhghf.com`（本地联调：`http://127.0.0.1:8100`）
- 鉴权请求头：`X-API-Token: sk-sanyi-...`（也接受 `Authorization: Bearer`）
- 统一返回结构：`{ "code": 0, "message": "success", "data": {} }`

## 2. 因子元信息

```text
factorKey   jue_direction
name        诀与破诀（80/20 诀方向与破诀）
cost        0（月费订阅制，不逐次扣费）
cache       30 秒
```

### 2.1 语义

```text
80诀       80 结构，未破，方向多
80诀破诀   80 结构被反向打破，方向空
20诀       20 结构，未破，方向空
20诀破诀   20 结构被反向打破，方向多
无诀       无结构
```

### 2.2 参数

```json
{
  "frequencies": ["5m", "15m", "1h", "1d", "1w", "1M"],
  "symbols": ["AU0", "CU0"],
  "states": ["80诀", "80诀破诀", "20诀", "20诀破诀"],
  "directions": ["long", "short"],
  "broken": null,
  "limit": 30
}
```

- `frequencies`：可选，默认全部周期。
- `symbols`：可选，按 qh API 的 `sym` 过滤；留空全部。
- `states`：可选，默认只取四种诀状态，不含 `无诀`。
- `directions`：可选，默认 `long / short`。
- `broken`：可选，`null`=不限；`true`=只看破诀；`false`=只看未破。
- `limit`：可选，默认 30，最大 100。

## 3. 返回重点字段

- `signal`：`LONG`（多占优）/ `SHORT`（空占优）/ `MIXED` / `NONE`
- `score`：0-100 方向一致性，50 表示多空各半
- `summary`：中文结论
- `generatedAt` / `details.updatedAt`：数据更新时间，**必须向用户展示**
- `details.counts`：多 / 空 / 无诀 / 破诀 / 待确认 / 成对确认 / 特殊走法数量
- `details.cells[]`：
  - `symbol` / `name` / `sector` / `contract`
  - `frequency` / `state` / `direction` / `side`
  - `broken`：是否破诀
  - `price` / `rsi3` / `pending` / `gap`
  - `walkState` / `walkCode` / `walkMark`：走法标记，例如 `20破·走2`、`80破·走B`
  - `pairConfirmPrev` / `pairConfirmNext`：与相邻周期成对确认
- `details.stateRules[]`：状态规则字典，Agent 可用它解释结果

## 4. HTTP 调用示例

```bash
curl -sS 'https://signal.shhghf.com/api/v1/factors/evaluate' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Token: sk-sanyi-...' \
  -d '{"factorKey":"jue_direction","params":{"frequencies":["5m","15m"],"broken":true,"limit":20}}'
```

MCP 工具调用：

```json
{
  "name": "sanyi_evaluate_factor",
  "arguments": {
    "factorKey": "jue_direction",
    "params": { "symbols": ["AU0"], "limit": 10 }
  }
}
```

## 5. Agent 使用规则

1. 用户问“诀 / 破诀 / 80诀 / 20诀 / 多空结构”时使用本因子。
2. 必须展示 `generatedAt` / `updatedAt`，并说明这是全市场结构统计，不是单品种交易指令。
3. `riskNote` 必须转述，不得删减为“无风险”。
4. `signal=MIXED` 时要明确说“多空相当”，不要强行给方向。
5. 空结果时明确说“当前筛选范围内没有诀 / 破诀状态”，不得编造。
6. 用户只问单品种时，传 `symbols` 过滤，不要返回全市场后自行挑选。

## 6. 风险提示

诀与破诀是市场结构观察指标，不构成投资建议；破诀表示原结构被反向打破，
需结合更大周期方向、流动性与风控使用。
