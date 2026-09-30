"""组合订阅评估器。

订阅 = 1 个 primary 条件 + 0..N 个 context 条件，条件之间是 AND。

每个 condition 仍然通过 FactorRegistry.evaluate 计算（audit=False，不重复扣费），
然后把各因子输出统一成中间行：

    symbol + normalized frequency + payload

交叉匹配规则：
- context.frequencyOffset=0 同周期；+N 向父级跳（15m→1h→1d …）；
  -N 向子级跳（15m→5m→1m …）；超出 1m/5m/15m/1h/1d/1w/1M 阶梯边界则不命中；
  30m 为绝对级别（例如 primary=30m + frequencyOffset=0），相对跳级时
  30m 特例为 +1=1h、-1=15m，不改变存量订阅的 15m+1=1h 语义；
- context.sideRule=below 时，context 门价必须低于 primary 当前价；
- context.sideRule=above 时，context 门价必须高于 primary 当前价；
- 所有 context 都至少命中一条，primary 行才会输出。

单条件订阅仍然只包一层原始因子结果，行为与旧版完全一致。
"""
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .crypto_leverage import annotate_matches
from .factor_registry import registry
from .strategy_model import (
    FREQUENCY_LEVELS,
    LAYER_EVENT,
    LEGACY_RELATIVE_FREQUENCY_LEVELS,
    MODE_POOL,
    MODE_TRIGGER,
    has_pool_layer,
    resolve_subscription_mode,
    strip_trigger_filters,
    structure_summary,
)

_DETAIL_KEYS = ("events", "cells", "gates", "coins")
_FREQUENCY_ORDER = tuple(FREQUENCY_LEVELS)
_OFFSET_LADDER = tuple(LEGACY_RELATIVE_FREQUENCY_LEVELS)
_DEFAULT_PRIMARY_FREQUENCIES = ("15m", "1h")
_MAX_SCAN_LIMIT = 100

_PRIMARY_IDENTITY_KEYS = (
    "eventId",
    "event_id",
    "symbol",
    "frequency",
    "state",
    "direction",
    "walkCode",
    "walkMark",
    "comboKey",
    "contract",
)
_GATE_IDENTITY_KEYS = (
    "symbol",
    "frequency",
    "gateType",
    "key",
    "openAt",
    "t1Time",
    "t2Time",
    "crossTime",
    "formation",
)


def _first_value(item: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            return value
    return None


def _normalize_frequency(value: Any) -> Optional[str]:
    """保留 1m(分钟) 与 1M(月) 的大小写区别，其余统一小写。"""
    text = str(value or "").strip()
    if text == "1M":
        return "1M"
    return text.lower() or None


def _normalize_symbol(value: Any) -> Optional[str]:
    text = str(value or "").strip().upper()
    return text or None


def _offset_frequency(frequency: str, offset: int) -> Optional[str]:
    """在相对频率阶梯上偏移 N 级。

    存量语义保持旧 7 级阶梯：15m+1 仍为 1h、15m-1 仍为 5m。
    30m 是新增绝对级别，不插入旧阶梯，因此不会改变任何老订阅的跳级结果；
    30m 自身的偏移按它在 15m/1h 之间的位置特例化：+1=1h、-1=15m、
    +2=1d、-2=5m。未知周期或超出阶梯边界返回 None（不参与匹配）。
    """
    value = int(offset)
    if frequency == "30m":
        if value == 0:
            return "30m"
        # 30m 在旧阶梯中位于 15m/1h 之间：正向先跳到 1h，负向先跳到 15m，
        # 再按旧阶梯继续逐级偏移。这样 ±1 指向直接父/子级，±2 指向隔级。
        if value > 0:
            try:
                index = _OFFSET_LADDER.index("1h") + value - 1
            except ValueError:
                return None
        else:
            try:
                index = _OFFSET_LADDER.index("15m") + value + 1
            except ValueError:
                return None
        if index < 0 or index >= len(_OFFSET_LADDER):
            return None
        return _OFFSET_LADDER[index]
    else:
        try:
            index = _OFFSET_LADDER.index(frequency)
        except ValueError:
            return None
    target = index + value
    if target < 0 or target >= len(_OFFSET_LADDER):
        return None
    return _OFFSET_LADDER[target]


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _error_text(exc: Exception) -> str:
    if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
        return str(exc.detail.get("message") or exc.detail)
    return "%s: %s" % (type(exc).__name__, exc)


def _fallback_condition(sub: Dict[str, Any]) -> Dict[str, Any]:
    """读取路径没有 conditions 时，从旧字段合成一条 primary 条件。"""
    return {
        "id": None,
        "factorKey": sub.get("factorKey") or "",
        "filters": sub.get("filters") or {},
        "role": "primary",
        "layer": LAYER_EVENT,
        "joinWith": None,
        "joinSymbol": "symbol",
        "frequencyOffset": 0,
        "frequencyOffsets": [0],
        "targetFrequencies": [],
        "sideRule": None,
        "sortOrder": 0,
    }


def _extract_rows(factor_key: str, result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把因子结果里的 events/cells/gates/coins 统一成中间行。"""
    details = result.get("details") or {}
    rows: List[Dict[str, Any]] = []
    for key in _DETAIL_KEYS:
        items = details.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = _normalize_symbol(_first_value(item, "symbol", "sym"))
            frequency = _normalize_frequency(_first_value(item, "frequency", "freq"))
            if not symbol or not frequency:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "frequency": frequency,
                    "kind": key,
                    "factorKey": str(result.get("factorKey") or factor_key),
                    "payload": item,
                }
            )
        if rows:
            return rows
    return rows


def _max_limit_for(spec: Any) -> Optional[int]:
    props = (spec.params_schema or {}).get("properties") or {}
    limit_schema = props.get("limit") or {}
    try:
        return int(limit_schema.get("maximum", _MAX_SCAN_LIMIT))
    except (TypeError, ValueError):
        return _MAX_SCAN_LIMIT


def _scan_filters(condition: Dict[str, Any], composite: bool) -> Dict[str, Any]:
    """订阅评估时把缺省 limit 抬到 schema 上限，避免默认 30 条截断后漏信号。

    用户显式指定 limit 时保持原样。直接调用因子 API（Agent 查询）不受影响，
    仍使用因子自己的默认 limit。
    """
    filters = dict(condition.get("filters") or {})
    if "limit" in filters:
        return filters
    spec = registry.get(condition.get("factorKey") or "")
    if spec is None or "limit" not in (spec.params_schema.get("properties") or {}):
        return filters
    max_limit = _max_limit_for(spec)
    if max_limit and max_limit > 1:
        filters["limit"] = max_limit
    return filters


async def _evaluate_condition(
    actor: Dict[str, Any],
    condition: Dict[str, Any],
    composite: bool,
) -> Dict[str, Any]:
    try:
        result = await registry.evaluate(
            token_record=actor,
            factor_key=str(condition["factorKey"]),
            params=_scan_filters(condition, composite),
            audit=False,
        )
        return {
            "condition": condition,
            "result": result,
            "rows": _extract_rows(str(condition["factorKey"]), result),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - 单个条件失败不能影响其他订阅
        return {
            "condition": condition,
            "result": None,
            "rows": [],
            "error": _error_text(exc),
        }


def _primary_price(payload: Dict[str, Any]) -> Optional[float]:
    for key in ("price", "currentPrice", "close", "last"):
        price = _number(payload.get(key))
        if price is not None:
            return price
    return None


def _gate_price(payload: Dict[str, Any]) -> Optional[float]:
    price = _number(payload.get("gatePrice"))
    if price is not None:
        return price
    return _number(payload.get("gate_price"))


def _payload_gate_type(payload: Dict[str, Any]) -> Optional[str]:
    """归一化 cell 的门类型；v4 door/spatial 用 gateType，旧因子可能用 type。"""
    value = payload.get("gateType")
    if value is None:
        value = payload.get("gate_type")
    if value is None:
        value = payload.get("type")
    if value in ("di", "dimen"):
        return "di"
    if value in ("tian", "tianmen"):
        return "tian"
    return None


def _condition_frequency_offsets(condition: Dict[str, Any]) -> List[int]:
    values = condition.get("frequencyOffsets")
    if isinstance(values, (list, tuple)):
        return [int(value) for value in values]
    return [int(condition.get("frequencyOffset") or 0)]


def _target_frequencies_for(
    primary_frequency: str,
    condition: Dict[str, Any],
) -> List[str]:
    """一个 context 条件的目标级别集合。

    支持两种结构化写法：
    - targetFrequencies: 绝对锚定，例如 ["1h"]，所有 primary 频率都看 1h；
    - frequencyOffsets: 相对偏移，例如 [1,2,3]，同一条件内任一命中即可。
    两种写法可以叠加取并集。
    """
    targets = {str(value) for value in (condition.get("targetFrequencies") or [])}
    for offset in _condition_frequency_offsets(condition):
        target = _offset_frequency(primary_frequency, offset)
        if target is not None:
            targets.add(target)
    return [freq for freq in _FREQUENCY_ORDER if freq in targets]


def _expand_context_filters(
    condition: Dict[str, Any],
    primary_condition: Dict[str, Any],
) -> Dict[str, Any]:
    """多目标 context 条件：把因子扫描周期扩到所有可能命中的级别。

    传统单 offset 条件保持原样，避免不必要地多拉数据。
    """
    offsets = _condition_frequency_offsets(condition)
    absolute_targets = [str(value) for value in (condition.get("targetFrequencies") or [])]

    filters = dict(condition.get("filters") or {})
    primary_filters = primary_condition.get("filters") if isinstance(primary_condition, dict) else {}
    base_frequencies = list(primary_filters.get("frequencies") or _DEFAULT_PRIMARY_FREQUENCIES)

    # 目标级别集合 = 绝对锚定级别 ∪ 每个 primary 频率按每个 offset 跳转后的级别。
    # offset=0 时必须包含 primary 自己的频率，避免同级别池条件漏扫。
    required = set(absolute_targets)
    for base_frequency in base_frequencies:
        for offset in offsets:
            target = _offset_frequency(str(base_frequency), offset)
            if target is not None:
                required.add(target)
    if required:
        existing = set(filters.get("frequencies") or [])
        required.update(existing)
        filters["frequencies"] = [freq for freq in _FREQUENCY_ORDER if freq in required]
    return filters


async def _evaluate_context_condition(
    actor: Dict[str, Any],
    condition: Dict[str, Any],
    primary_condition: Dict[str, Any],
    composite: bool,
) -> Dict[str, Any]:
    eval_condition = dict(condition)
    eval_condition["filters"] = _expand_context_filters(condition, primary_condition)
    return await _evaluate_condition(actor, eval_condition, composite)


def _match_context_rows(
    primary_row: Dict[str, Any],
    context_eval: Dict[str, Any],
) -> List[Dict[str, Any]]:
    condition = context_eval["condition"]
    target_frequencies = _target_frequencies_for(
        primary_row["frequency"],
        condition,
    )
    if not target_frequencies:
        return []

    side_rule = str(condition.get("sideRule") or "")
    primary_price = _primary_price(primary_row["payload"]) if side_rule else None
    primary_gate_type = _payload_gate_type(primary_row["payload"])
    matches: List[Dict[str, Any]] = []
    for context_row in context_eval["rows"]:
        if context_row["symbol"] != primary_row["symbol"]:
            continue
        if context_row["frequency"] not in target_frequencies:
            continue
        # 门类因子交叉匹配时必须同一条门（同类型）。v4 快照每个品种+周期
        # 只有最新地门/最新天门各一条，gateType 对齐即可避免 di/tian 串门；
        # 没有 gateType 的旧因子仍按 symbol+frequency 连接，保持兼容。
        context_gate_type = _payload_gate_type(context_row["payload"])
        if primary_gate_type is not None and context_gate_type is not None:
            if primary_gate_type != context_gate_type:
                continue
        if side_rule:
            gate_price = _gate_price(context_row["payload"])
            if primary_price is None or gate_price is None:
                continue
            if side_rule == "below" and gate_price >= primary_price:
                continue
            if side_rule == "above" and gate_price <= primary_price:
                continue
        matches.append(context_row["payload"])
    return matches


def _identity(payload: Dict[str, Any], keys: Tuple[str, ...]) -> str:
    parts = []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        parts.append("" if value is None else str(value))
    return ":".join(parts)


def _context_identity(payload: Dict[str, Any]) -> str:
    # gate key 优先唯一；没有 key 时用时间/形态等稳定字段，避免 currentPrice 抖动造成重复推送。
    identity = _identity(payload, _GATE_IDENTITY_KEYS)
    if identity.strip(":"):
        return identity
    stable = {
        key: value
        for key, value in payload.items()
        if key not in ("currentPrice", "updatedAt", "generatedAt")
    }
    return json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str)


def _composite_event_id(
    primary_row: Dict[str, Any],
    context_hits: List[Dict[str, Any]],
) -> str:
    identity = [
        "primary",
        _identity(primary_row["payload"], _PRIMARY_IDENTITY_KEYS),
    ]
    for hit in sorted(context_hits, key=lambda h: str(h["condition"].get("id") or 0)):
        condition = hit["condition"]
        identity.extend(
            [
                "context",
                str(condition.get("id") or ""),
                str(condition.get("factorKey") or ""),
                "offsets:%s" % ",".join(str(v) for v in _condition_frequency_offsets(condition)),
                "targets:%s" % ",".join(sorted(str(v) for v in (condition.get("targetFrequencies") or []))),
                str(condition.get("sideRule") or ""),
            ]
        )
        identity.extend(
            sorted(_context_identity(payload) for payload in hit["matches"])
        )
    raw = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "composite:%s" % hashlib.sha256(raw).hexdigest()[:24]


def _build_composite_match(
    primary_row: Dict[str, Any],
    context_hits: List[Dict[str, Any]],
) -> Dict[str, Any]:
    match = dict(primary_row["payload"])
    match["eventId"] = _composite_event_id(primary_row, context_hits)
    match["composite"] = True
    match["contexts"] = [
        {
            "factorKey": hit["condition"].get("factorKey"),
            "role": hit["condition"].get("role", "context"),
            "frequencyOffset": int(hit["condition"].get("frequencyOffset") or 0),
            "frequencyOffsets": _condition_frequency_offsets(hit["condition"]),
            "targetFrequencies": list(hit["condition"].get("targetFrequencies") or []),
            "sideRule": hit["condition"].get("sideRule"),
            "matches": list(hit["matches"]),
        }
        for hit in context_hits
    ]
    return match


def _condition_error(condition_eval: Dict[str, Any]) -> Dict[str, Any]:
    condition = condition_eval["condition"]
    return {
        "conditionId": condition.get("id"),
        "factorKey": condition.get("factorKey"),
        "role": condition.get("role"),
        "error": condition_eval["error"],
    }


def _beijing_trading_cycle_start(now: Optional[datetime] = None) -> datetime:
    """期货交易日边界：北京时间每天 15:00 切新的一天。"""
    current = now.astimezone(ZoneInfo("Asia/Shanghai")) if now else datetime.now(ZoneInfo("Asia/Shanghai"))
    start = current.replace(hour=15, minute=0, second=0, microsecond=0)
    if current < start:
        start = start - timedelta(days=1)
    return start


def _futures_gate_event_time(match: Dict[str, Any]) -> Optional[datetime]:
    for key in ("openAt", "closeAt", "deleteAt"):
        value = match.get(key)
        if not value:
            continue
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            return dt.astimezone(ZoneInfo("Asia/Shanghai"))
        except (TypeError, ValueError):
            continue
    return None


def _filter_futures_gate_subscription(
    sub: Dict[str, Any],
    matches: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """期货门池订阅只保留当前交易周期（15:00 之后）的新开/新关门。

    过去开的门对持续订阅没有意义；每天 15:00 自动滚到新的一天。
    """
    if sub.get("factorKey") != "gate_condition":
        return matches
    cycle_start = _beijing_trading_cycle_start()
    kept = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        event_time = _futures_gate_event_time(match)
        if event_time is not None and event_time >= cycle_start:
            kept.append(match)
    return kept


async def _maybe_annotate_crypto_leverage(sub: Dict[str, Any], item: Dict[str, Any]) -> None:
    """币圈订阅的信号附上建议杠杆，并过滤入 list 前形成的老门。

    基础池通常数量较大，逐币补算杠杆会把策略页拖慢；池层只展示状态，不做杠杆标注。
    """
    if not str(sub.get("factorKey") or "").startswith("crypto_"):
        return
    if item.get("mode") == MODE_POOL:
        return
    had_matches = bool(item.get("matches"))
    if had_matches:
        try:
            item["matches"] = await annotate_matches(item["matches"])
        except Exception:  # noqa: BLE001 - 杠杆建议/老门过滤是附加逻辑
            pass
    if had_matches and not item["matches"]:
        item["signal"] = "NONE"
        item["summary"] = "当前命中的门均为该品种进入合约列表前形成的历史门，已按规则过滤。"


async def _evaluate_pool_layer(
    actor: Dict[str, Any],
    conditions: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]], Optional[str]]:
    """只按状态/背景条件评估基础池。

    primary 条件去掉动作字段后作为池子的候选 universe；
    context 条件保持原样交叉匹配。返回 (matches, primary_result, condition_errors, error)。
    """
    primary_condition = strip_trigger_filters(conditions[0])
    primary_eval = await _evaluate_condition(actor, primary_condition, composite=True)
    if primary_eval["error"]:
        return [], {}, [_condition_error(primary_eval)], primary_eval["error"]

    context_evals = []
    for condition in conditions[1:]:
        context_eval = await _evaluate_context_condition(
            actor, condition, primary_condition, composite=True
        )
        context_evals.append(context_eval)
        if context_eval["error"]:
            continue
    errors = [_condition_error(ev) for ev in context_evals if ev["error"]]
    if errors:
        return [], primary_eval["result"], errors, None

    matches: List[Dict[str, Any]] = []
    for primary_row in primary_eval["rows"]:
        context_hits: List[Dict[str, Any]] = []
        matched = True
        for context_eval in context_evals:
            hits = _match_context_rows(primary_row, context_eval)
            if not hits:
                matched = False
                break
            context_hits.append({"condition": context_eval["condition"], "matches": hits})
        if matched:
            matches.append(_build_composite_match(primary_row, context_hits))
    return matches, primary_eval["result"], [], None


async def evaluate_subscription(
    actor: Dict[str, Any],
    sub: Dict[str, Any],
) -> Dict[str, Any]:
    """评估一个订阅，返回 API / pusher 共用的 snapshot item。

    新增漏斗字段：
    - mode: trigger=上穿/下穿等动作触发；pool=状态池，入池即信号。
    - pool: 基础池当前成员（持续满足背景条件就持续展示）。
    - signals: 当前触发信号（trigger 模式 = 完整 AND 命中；pool 模式为空）。
    """
    conditions = sub.get("conditions") or []
    if not conditions:
        conditions = [_fallback_condition(sub)]
    composite = len(conditions) > 1
    mode = resolve_subscription_mode(sub.get("mode"), conditions)
    show_pool = has_pool_layer(mode, conditions)

    structure = structure_summary(mode, conditions)
    item: Dict[str, Any] = {
        "id": sub.get("id"),
        "factorKey": sub.get("factorKey"),
        "name": sub.get("name"),
        "filters": sub.get("filters"),
        "conditions": list(conditions),
        "createdAt": sub.get("createdAt"),
        "updatedAt": sub.get("updatedAt"),
        "mode": mode,
        "eventConditions": structure["eventConditions"],
        "poolConditions": structure["poolConditions"],
        "structureComplete": structure["structureComplete"],
        "missingParts": structure["missingParts"],
        "signal": None,
        "summary": "",
        "generatedAt": None,
        "matches": [],
        "pool": [],
        "signals": [],
        "triggerCandidates": 0,
        "conditionErrors": [],
        "poolError": None,
        "poolConditionErrors": [],
        "error": None,
    }

    if not composite:
        evaluated = await _evaluate_condition(actor, conditions[0], composite=False)
        if evaluated["error"]:
            item["error"] = evaluated["error"]
            item["conditionErrors"] = [_condition_error(evaluated)]
            return item
        result = evaluated["result"]
        item["signal"] = result.get("signal")
        item["summary"] = result.get("summary")
        item["generatedAt"] = result.get("generatedAt")
        item["matches"] = [dict(row["payload"]) for row in evaluated["rows"]]
        item["matches"] = _filter_futures_gate_subscription(sub, item["matches"])
        if not item["matches"] and sub.get("factorKey") == "gate_condition":
            item["signal"] = "NONE"
            item["summary"] = "当前交易周期（15:00 后）还没有符合条件的期货门。"
        if mode == MODE_TRIGGER:
            await _maybe_annotate_crypto_leverage(sub, item)
        if mode == MODE_TRIGGER:
            item["triggerCandidates"] = len(item["matches"])
        if mode == MODE_POOL:
            item["pool"] = list(item["matches"])
        if mode == MODE_TRIGGER:
            item["signals"] = list(item["matches"])
        return item

    primary_eval = await _evaluate_condition(actor, conditions[0], composite=True)
    if primary_eval["error"]:
        item["error"] = primary_eval["error"]
        item["conditionErrors"] = [_condition_error(primary_eval)]
        return item
    if mode == MODE_TRIGGER:
        item["triggerCandidates"] = len(primary_eval["rows"])

    context_evals = []
    for condition in conditions[1:]:
        context_eval = await _evaluate_context_condition(
            actor, condition, conditions[0], composite=True
        )
        context_evals.append(context_eval)
        if context_eval["error"]:
            item["conditionErrors"].append(_condition_error(context_eval))

    # 任一 context 数据源异常时按 AND 语义不输出信号；错误单独展示给用户。
    usable_contexts = [ev for ev in context_evals if ev["error"] is None]
    if len(usable_contexts) != len(context_evals):
        primary_result = primary_eval["result"]
        item["signal"] = "NONE"
        item["summary"] = "组合条件评估不完整（部分条件数据源异常），本轮不输出匹配信号。"
        item["generatedAt"] = primary_result.get("generatedAt")
        return item

    matches: List[Dict[str, Any]] = []
    for primary_row in primary_eval["rows"]:
        context_hits: List[Dict[str, Any]] = []
        matched = True
        for context_eval in context_evals:
            hits = _match_context_rows(primary_row, context_eval)
            if not hits:
                matched = False
                break
            context_hits.append({"condition": context_eval["condition"], "matches": hits})
        if matched:
            matches.append(_build_composite_match(primary_row, context_hits))

    primary_result = primary_eval["result"]
    item["signal"] = primary_result.get("signal") if matches else "NONE"
    item["summary"] = "%s；组合条件命中 %d 条。" % (
        primary_result.get("summary") or "",
        len(matches),
    )
    item["generatedAt"] = primary_result.get("generatedAt")
    item["matches"] = matches
    item["matches"] = _filter_futures_gate_subscription(sub, item["matches"])
    if not item["matches"] and sub.get("factorKey") == "gate_condition":
        item["signal"] = "NONE"
        item["summary"] = "当前交易周期（15:00 后）还没有符合条件的期货门。"

    if show_pool:
        pool_matches, _pool_result, pool_errors, pool_error = await _evaluate_pool_layer(
            actor, conditions
        )
        item["poolConditionErrors"] = pool_errors
        if pool_error:
            # 池层评估失败只影响池子展示，不推翻已经算出的触发信号，也不阻塞推送。
            item["poolError"] = pool_error
            item["pool"] = []
        else:
            item["pool"] = pool_matches

    if mode == MODE_TRIGGER:
        await _maybe_annotate_crypto_leverage(sub, item)
        item["signals"] = list(item["matches"])
    return item
