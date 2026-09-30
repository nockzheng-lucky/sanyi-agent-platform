"""v4 基础因子公共实现。

数据源是东京/期货 green 的只读批量投影：
    GET /api/factors/scan?freq=15m&limit=2000

返回的每个 pair 快照展开成一行 cell；订阅去重依赖稳定的 eventId。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Iterable, List, Optional

import httpx
from fastapi import HTTPException

from .base import FactorContext, now_iso

FREQ_LEVELS = ("1m", "5m", "15m", "30m", "1h", "1d", "1w", "1M")
DEFAULT_FREQS = ("15m", "1h")
_CACHE: Dict[Any, Any] = {}
_CACHE_TTL_SECONDS = 5.0


def _norm_set(values: Any) -> set:
    return {str(v).strip() for v in (values or []) if str(v).strip()}


_MA_BASIC_NAMES = ("ma52", "ma208", "ma832")


def _parse_between_rules(params: Dict[str, Any]) -> List[tuple]:
    """解析 between 参数：返回 [(above_ma, below_ma), ...]，过滤非法值。"""
    rules = []
    for raw in params.get("between") or []:
        if not isinstance(raw, dict):
            continue
        above = str(raw.get("above") or "").strip().lower()
        below = str(raw.get("below") or "").strip().lower()
        if above in _MA_BASIC_NAMES and below in _MA_BASIC_NAMES:
            rules.append((above, below))
    return rules


async def fetch_scan(
    url: str,
    freq: str,
    timeout: float = 30.0,
    verify: bool = True,
    allow_stale: bool = False,
    stale_max_age: float = 600.0,
) -> List[Dict[str, Any]]:
    """拉取单周期因子快照。

    allow_stale=True 时，网络抖动/上游暂不可用会回退到最近一次成功缓存
    (默认 10 分钟内)，避免订阅面板频繁红字。自检脚本保持 allow_stale=False，
    仍能真实报告数据源故障。
    """
    if not url:
        raise HTTPException(status_code=503, detail={"code": 503, "message": "v4 因子数据源未配置", "data": None})
    key = (url.rstrip("/"), freq)
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    connect_timeout = min(15.0, max(3.0, timeout))
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=connect_timeout),
                verify=verify,
            ) as client:
                try:
                    response = await client.get(url.rstrip("/"), params={"freq": freq, "limit": 2000})
                except httpx.HTTPError as exc:
                    raise HTTPException(
                        status_code=502,
                        detail={"code": 502, "message": "v4 因子数据源不可用：%s" % exc.__class__.__name__, "data": None},
                    )
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail={"code": 502, "message": "v4 因子数据源返回 HTTP %s" % response.status_code, "data": None})
            try:
                payload = response.json()
            except ValueError:
                raise HTTPException(status_code=502, detail={"code": 502, "message": "v4 因子数据源返回了非 JSON 内容", "data": None})
            if not isinstance(payload, dict):
                raise HTTPException(status_code=502, detail={"code": 502, "message": "v4 因子数据源响应结构不正确", "data": None})
            if payload.get("status") == "unavailable":
                raise HTTPException(status_code=502, detail={"code": 502, "message": "v4 因子快照暂不可用：%s" % payload.get("reason"), "data": None})
            rows = payload.get("factors")
            if not isinstance(rows, list):
                raise HTTPException(status_code=502, detail={"code": 502, "message": "v4 因子数据源缺少 factors 列表", "data": None})
            _CACHE[key] = (time.monotonic(), rows)
            return rows
        except HTTPException as exc:
            transient = "不可用" in str(exc.detail.get("message") or "")
            if attempt == 0 and transient:
                await asyncio.sleep(0.6)
                continue
            if allow_stale and cached is not None:
                age = time.monotonic() - cached[0]
                if age <= stale_max_age:
                    print(
                        "[v4] factor stale fallback url=%s freq=%s age=%.1fs reason=%s"
                        % (url, freq, age, exc.detail.get("message")),
                        flush=True,
                    )
                    return cached[1]
            raise

    # 理论不可达；防御性保留。
    if allow_stale and cached is not None:
        return cached[1]
    raise HTTPException(status_code=502, detail={"code": 502, "message": "v4 因子数据源不可用", "data": None})


async def _fetch_for_freqs(url: str, frequencies: Iterable[str], verify: bool) -> List[Dict[str, Any]]:
    freqs = list(dict.fromkeys(frequencies))
    # 事件类订阅禁止 600s stale 因子缓存回退：数据源失败时 defer/重试，不拿旧快照当新事件。
    results = await asyncio.gather(
        *(fetch_scan(url, freq, verify=verify, allow_stale=False) for freq in freqs)
    )
    rows: List[Dict[str, Any]] = []
    for result in results:
        rows.extend(result)
    return rows


def _base(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    contract = snapshot.get("contract")
    return {
        "symbol": str(snapshot.get("sym") or ""),
        "name": str(snapshot.get("name") or snapshot.get("sym") or ""),
        "frequency": str(snapshot.get("freq") or ""),
        "lastBarTime": snapshot.get("last_bar_time"),
        "contract": str(contract) if contract else None,
    }


def _finish(factor_key: str, cells: List[Dict[str, Any]], updated_at: Any = None) -> Dict[str, Any]:
    if cells:
        signal = "MATCH"
        score = min(100, 20 + len(cells) * 2)
        summary = "命中 %d 个基础因子条件。" % len(cells)
    else:
        signal = "NONE"
        score = 0
        summary = "当前没有符合条件的基础因子。"
    return {
        "factorKey": factor_key,
        "signal": signal,
        "score": score,
        "summary": summary,
        "generatedAt": now_iso(),
        "details": {
            "updatedAt": updated_at or now_iso(),
            "matchedCells": len(cells),
            "returnedCells": len(cells),
            "cells": cells,
        },
        "riskNote": "基础因子仅描述最新已收盘状态，不构成投资建议。",
    }


def _match_price(snapshot: Dict[str, Any], params: Dict[str, Any], factor_key: str, domain: str) -> Optional[Dict[str, Any]]:
    factors = snapshot.get("factors") or {}
    price = factors.get("price") or {}
    direction = str(price.get("direction") or "")
    directions = _norm_set(params.get("directions"))
    if directions and direction not in directions:
        return None
    cell = _base(snapshot)
    cell.update({
        "factorKey": factor_key,
        "close": price.get("close"),
        "high": price.get("high"),
        "low": price.get("low"),
        "direction": direction,
        "eventId": "v4:%s:price:%s:%s:%s" % (domain, cell["symbol"], cell["frequency"], direction),
    })
    return cell


def _normalize_cross(value: Any) -> str:
    """统一上/下穿取值。

    上游对 MA-MA 关系给的是 ma52_cross_up_ma208 这种带名字的格式，
    而订阅参数 schema 用 cross_up / cross_down / none，必须归一化后再匹配。
    """
    if not value:
        return "none"
    text = str(value).strip()
    if text in ("cross_up", "cross_down", "none"):
        return text
    if "cross_up" in text:
        return "cross_up"
    if "cross_down" in text:
        return "cross_down"
    return "none"


def _match_ma(snapshot: Dict[str, Any], params: Dict[str, Any], factor_key: str, domain: str) -> Optional[Dict[str, Any]]:
    ma = (snapshot.get("factors") or {}).get("ma") or {}
    ma_names = _norm_set(params.get("maNames"))
    price_sides = _norm_set(params.get("priceSides"))
    price_crosses = _norm_set(params.get("priceCrosses"))
    pair_crosses = _norm_set(params.get("pairCrosses"))
    pair_side_filters = (
        ("ma52AboveMa208", "ma52_above_ma208"),
        ("ma52AboveMa832", "ma52_above_ma832"),
        ("ma208AboveMa832", "ma208_above_ma832"),
    )
    allow_missing_relation = bool(params.get("allowMissingMaRelation"))
    for param_name, actual_key in pair_side_filters:
        expected = params.get(param_name)
        if not isinstance(expected, bool):
            continue
        actual = ma.get(actual_key)
        if actual is None:
            # 数据不足(null)的品种: allowMissingMaRelation=true 时放行, 由推送层标注“未确认”;
            # 默认严格口径下不命中, 避免把“没有确认”误当成“确认通过”。
            if not allow_missing_relation:
                return None
        elif bool(actual) != expected:
            return None
    if ma_names:
        side_hits = [ma.get("%s_price_side" % name) for name in ma_names if ma.get("%s_price_side" % name)]
        if not side_hits:
            return None
    if price_sides:
        ok = any(ma.get("%s_price_side" % name) in price_sides for name in ma_names or ("ma52", "ma208", "ma832"))
        if not ok:
            return None
    if price_crosses:
        ok = any(ma.get("%s_price_cross" % name) in price_crosses for name in ma_names or ("ma52", "ma208", "ma832"))
        if not ok:
            return None
    if pair_crosses:
        ok = any(
            _normalize_cross(ma.get(key)) in pair_crosses
            for key in ("ma52_ma208_cross", "ma52_ma832_cross", "ma208_ma832_cross")
        )
        if not ok:
            return None

    # between：收盘价夹在两条均线之间，规则内 AND、规则间 OR。
    between_rules = _parse_between_rules(params)
    between_match = None
    if between_rules:
        for above_name, below_name in between_rules:
            if (
                ma.get("%s_price_side" % above_name) == "above"
                and ma.get("%s_price_side" % below_name) == "below"
            ):
                between_match = {"above": above_name, "below": below_name}
                break
        if between_match is None:
            return None

    # 统一关系筛选：MA 因子也能筛 25/144/169 与 52/208/832 的跨组关系。
    # 未启用这些字段时，输出和 eventId 与旧版完全一致，不扰动存量订阅。
    tolerance_pct = float(params.get("tolerancePct") or 0.0)
    if tolerance_pct < 0:
        tolerance_pct = 0.0
    relation_info = _build_ma_relation_info(snapshot, tolerance_pct)
    scope_relations = _relation_filter_scope(
        params,
        relation_info,
        default_relations=_MA_CROSS_RELATIONS,
        allowed_relations=ALL_MA_RELATION_KEYS,
        allow_missing_relation=allow_missing_relation,
    )
    relation_missing = False
    if scope_relations is not None:
        if not scope_relations:
            return None
        for relation in scope_relations:
            if relation_info[relation].get("state") is None:
                if allow_missing_relation:
                    relation_missing = True
                else:
                    return None

    cell = _base(snapshot)
    event_tail = "|".join(sorted("%s=%s" % (k, ma.get(k)) for k in sorted(ma)))
    if scope_relations is not None:
        event_tail += "|relations=" + "|".join(
            sorted(
                "%s=%s|%s" % (
                    relation,
                    relation_info[relation].get("state"),
                    relation_info[relation].get("cross"),
                )
                for relation in set(scope_relations)
            )
        )
    if between_match is not None:
        event_tail += "|between=%s:%s" % (between_match["above"], between_match["below"])
    cell.update({
        "factorKey": factor_key,
        "ma": {k: v for k, v in ma.items() if k.startswith("ma")},
        "maRelationMissing": bool(
            allow_missing_relation
            and (
                any(
                    isinstance(params.get(param_name), bool)
                    and ma.get(actual_key) is None
                    for param_name, actual_key in pair_side_filters
                )
                or relation_missing
            )
        ),
        "eventId": "v4:%s:ma:%s:%s:%s" % (domain, cell["symbol"], cell["frequency"], event_tail),
    })
    if between_match is not None:
        cell["between"] = between_match
    if scope_relations is not None:
        cell["relations"] = {
            relation: relation_info[relation]
            for relation in sorted(set(scope_relations))
        }
    return cell


_MA_TRIPLE_RELATIONS = (
    "price_ma25",
    "price_ma144",
    "price_ma169",
    "ma25_ma144",
    "ma25_ma169",
    "ma144_ma169",
)
_MA_TRIPLE_RELATION_FIELDS = {
    "price_ma25": ("ma25_price_side", "ma25_price_cross", "ma25_price_pct"),
    "price_ma144": ("ma144_price_side", "ma144_price_cross", "ma144_price_pct"),
    "price_ma169": ("ma169_price_side", "ma169_price_cross", "ma169_price_pct"),
    "ma25_ma144": ("ma25_ma144_side", "ma25_ma144_cross", "ma25_ma144_pct"),
    "ma25_ma169": ("ma25_ma169_side", "ma25_ma169_cross", "ma25_ma169_pct"),
    "ma144_ma169": ("ma144_ma169_side", "ma144_ma169_cross", "ma144_ma169_pct"),
}
_MA_CROSS_RELATIONS = (
    "ma25_ma52",
    "ma25_ma208",
    "ma25_ma832",
    "ma144_ma52",
    "ma144_ma208",
    "ma144_ma832",
    "ma169_ma52",
    "ma169_ma208",
    "ma169_ma832",
)
ALL_MA_RELATION_KEYS = _MA_TRIPLE_RELATIONS + _MA_CROSS_RELATIONS
_MA_CROSS_RELATION_FIELDS = {
    "ma25_ma52": ("ma25_ma52_side", "ma25_ma52_cross", "ma25_ma52_pct"),
    "ma25_ma208": ("ma25_ma208_side", "ma25_ma208_cross", "ma25_ma208_pct"),
    "ma25_ma832": ("ma25_ma832_side", "ma25_ma832_cross", "ma25_ma832_pct"),
    "ma144_ma52": ("ma144_ma52_side", "ma144_ma52_cross", "ma144_ma52_pct"),
    "ma144_ma208": ("ma144_ma208_side", "ma144_ma208_cross", "ma144_ma208_pct"),
    "ma144_ma832": ("ma144_ma832_side", "ma144_ma832_cross", "ma144_ma832_pct"),
    "ma169_ma52": ("ma169_ma52_side", "ma169_ma52_cross", "ma169_ma52_pct"),
    "ma169_ma208": ("ma169_ma208_side", "ma169_ma208_cross", "ma169_ma208_pct"),
    "ma169_ma832": ("ma169_ma832_side", "ma169_ma832_cross", "ma169_ma832_pct"),
}
_MA_RELATION_FIELDS = {**_MA_TRIPLE_RELATION_FIELDS, **_MA_CROSS_RELATION_FIELDS}
_MA_TRIPLE_SIGNATURE_LABELS = {
    "price_ma25": "C-25",
    "price_ma144": "C-144",
    "price_ma169": "C-169",
    "ma25_ma144": "25-144",
    "ma25_ma169": "25-169",
    "ma144_ma169": "144-169",
}


def _pct_or_none(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    import math
    if not math.isfinite(number):
        return None
    return round(number, 4)


def _near_relation_state(pct: Optional[float], tolerance_pct: float) -> Optional[str]:
    if pct is None:
        return None
    if pct > tolerance_pct:
        return "above"
    if pct < -tolerance_pct:
        return "below"
    return "near"



def _build_ma_relation_info(snapshot: Dict[str, Any], tolerance_pct: float) -> Dict[str, Dict[str, Any]]:
    """统一读取 MA25/144/169 与 MA52/208/832 的组内、跨组关系。

    上游把跨组关系放在 factors.ma_triple 下，命名规则与三线组内关系一致：
    ma25_ma52_side / ma25_ma52_cross / ma25_ma52_pct。旧订阅只看组内六关系时
    eventId 与输出形状完全不变；只有显式筛跨组关系时才把新字段纳入 eventId。
    """
    mt = (snapshot.get("factors") or {}).get("ma_triple") or {}
    relation_info: Dict[str, Dict[str, Any]] = {}
    for relation in ALL_MA_RELATION_KEYS:
        side_key, cross_key, pct_key = _MA_RELATION_FIELDS[relation]
        pct = _pct_or_none(mt.get(pct_key))
        relation_info[relation] = {
            "state": _near_relation_state(pct, tolerance_pct),
            "stateRaw": mt.get(side_key),
            "crossRaw": mt.get(cross_key),
            "cross": _normalize_cross(mt.get(cross_key)),
            "pct": pct,
        }
    return relation_info


def _relation_filter_scope(
    params: Dict[str, Any],
    relation_info: Dict[str, Dict[str, Any]],
    default_relations: Iterable[str],
    allowed_relations: Optional[Iterable[str]] = None,
    allow_missing_relation: bool = False,
) -> Optional[List[str]]:
    """按 relations/relationStates/relationCrosses 计算本次关系范围。

    requireAllRelations=true 时所有候选关系都必须通过；默认 false 时任一通过即可。
    allow_missing_relation=true 时，状态因历史不足为 null 的关系视为通过（用于“未确认”放行）。
    返回 None 表示没有启用关系筛选；返回 [] 表示筛选已启用但不命中。
    """
    requested_relations = _norm_set(params.get("relations"))
    relation_states = _norm_set(params.get("relationStates"))
    relation_crosses = _norm_set(params.get("relationCrosses"))
    require_all_relations = bool(params.get("requireAllRelations"))
    if not (requested_relations or relation_states or relation_crosses):
        return None
    allowed = set(allowed_relations or ())
    candidates = list(requested_relations or set(default_relations))
    matched_relations: List[str] = []
    for relation in candidates:
        info = relation_info[relation]
        if allowed and relation not in allowed:
            continue
        state_missing = relation_states and info.get("state") is None
        state_ok = (
            not relation_states
            or info.get("state") in relation_states
            or (allow_missing_relation and state_missing)
        )
        cross_ok = (not relation_crosses or info.get("cross") in relation_crosses)
        if relation_states and relation_crosses:
            passed = state_ok and cross_ok
        else:
            passed = state_ok if relation_states else cross_ok
        if passed:
            matched_relations.append(relation)
        elif require_all_relations:
            return []
    if not matched_relations:
        return []
    return list(requested_relations or matched_relations)

def _match_ma_triple(snapshot: Dict[str, Any], params: Dict[str, Any], factor_key: str, domain: str) -> Optional[Dict[str, Any]]:
    """MA25/144/169 组内六关系 + 与 MA52/208/832 的跨组关系 + 三线排列/完整排列。

    关系状态使用 tolerancePct 判 near；上游 ma_triple 的严格字段也一并透出。
    eventId 只包含本次筛选涉及的关系/汇总状态，避免无关关系变化造成误推送。
    旧订阅未显式选择跨组关系时，输出与 eventId 保持原样，不受新增字段影响。
    """
    mt = (snapshot.get("factors") or {}).get("ma_triple") or {}
    tolerance_pct = float(params.get("tolerancePct") or 0.0)
    if tolerance_pct < 0:
        tolerance_pct = 0.0

    relation_info = _build_ma_relation_info(snapshot, tolerance_pct)

    signature_values: List[str] = []
    for relation in _MA_TRIPLE_RELATIONS:
        state = relation_info[relation]["state"]
        signature_values.append(
            {"above": "+", "near": "0", "below": "-"}.get(state or "", "x")
        )
    signature_near = "".join(signature_values)
    signature_strict = mt.get("signature") or ""

    price_zone = mt.get("price_zone")
    ma_order = mt.get("ma_order")
    alignment = mt.get("alignment")

    price_zones = _norm_set(params.get("priceZones"))
    ma_orders = _norm_set(params.get("maOrders"))
    alignments = _norm_set(params.get("alignments"))
    signatures = _norm_set(params.get("signatures"))
    if price_zones and price_zone not in price_zones:
        return None
    if ma_orders and ma_order not in ma_orders:
        return None
    if alignments and alignment not in alignments:
        return None
    if signatures and signature_near not in signatures and signature_strict not in signatures:
        return None

    scope_relations = _relation_filter_scope(
        params,
        relation_info,
        default_relations=_MA_TRIPLE_RELATIONS,
        allowed_relations=ALL_MA_RELATION_KEYS,
    )
    if scope_relations is None:
        scope_relations = list(_MA_TRIPLE_RELATIONS)
    elif not scope_relations:
        return None

    event_parts = []
    for relation in sorted(set(scope_relations)):
        info = relation_info[relation]
        event_parts.append(
            "%s=%s|%s" % (relation, info.get("state"), info.get("cross"))
        )
    if price_zones or ma_orders or alignments or signatures:
        event_parts.extend([
            "zone=%s" % price_zone,
            "order=%s" % ma_order,
            "alignment=%s" % alignment,
            "signature=%s" % signature_near,
        ])
    if not event_parts:
        event_parts = [
            "signature=%s" % signature_near,
            "zone=%s" % price_zone,
            "order=%s" % ma_order,
            "alignment=%s" % alignment,
        ]

    output_relations = {
        relation: relation_info[relation]
        for relation in _MA_TRIPLE_RELATIONS
    }
    # 只有显式筛/查跨组关系时才把新键放进输出，保证旧订阅的 payload 形状不变。
    output_relations.update({
        relation: relation_info[relation]
        for relation in sorted(set(scope_relations))
        if relation in _MA_CROSS_RELATIONS
    })

    cell = _base(snapshot)
    cell.update({
        "factorKey": factor_key,
        "close": ((snapshot.get("factors") or {}).get("price") or {}).get("close"),
        "relations": output_relations,
        "signature": signature_near,
        "signatureStrict": signature_strict,
        "priceZone": price_zone,
        "maOrder": ma_order,
        "alignment": alignment,
        "eventId": "v4:%s:ma_triple:%s:%s:%s" % (
            domain,
            cell["symbol"],
            cell["frequency"],
            "|".join(sorted(event_parts)),
        ),
    })
    return cell


def _match_macd(snapshot: Dict[str, Any], params: Dict[str, Any], factor_key: str, domain: str) -> Optional[Dict[str, Any]]:
    macd = (snapshot.get("factors") or {}).get("macd") or {}
    dif_sides = _norm_set(params.get("difSides"))
    hist_sides = _norm_set(params.get("histSides"))
    dif_crosses = _norm_set(params.get("difCrosses"))
    hist_crosses = _norm_set(params.get("histCrosses"))
    if dif_sides and str(macd.get("dif_side") or "") not in dif_sides:
        return None
    if hist_sides and str(macd.get("hist_side") or "") not in hist_sides:
        return None
    if dif_crosses and str(macd.get("dif_cross_zero") or "") not in dif_crosses:
        return None
    if hist_crosses and str(macd.get("hist_cross_zero") or "") not in hist_crosses:
        return None
    cell = _base(snapshot)
    cell.update({
        "factorKey": factor_key,
        "dif": macd.get("dif"),
        "dea": macd.get("dea"),
        "hist": macd.get("hist"),
        "difSide": macd.get("dif_side"),
        "histSide": macd.get("hist_side"),
        "difCrossZero": macd.get("dif_cross_zero"),
        "histCrossZero": macd.get("hist_cross_zero"),
        "eventId": "v4:%s:macd:%s:%s:%s:%s:%s:%s" % (
            domain, cell["symbol"], cell["frequency"],
            macd.get("dif_side"), macd.get("hist_side"),
            macd.get("dif_cross_zero"), macd.get("hist_cross_zero")),
    })
    return cell


def _match_boll(snapshot: Dict[str, Any], params: Dict[str, Any], factor_key: str, domain: str) -> Optional[Dict[str, Any]]:
    boll = (snapshot.get("factors") or {}).get("boll") or {}
    if not isinstance(boll, dict) or "upper_cross" not in boll:
        return None
    upper_crosses = _norm_set(params.get("upperCrosses"))
    mid_crosses = _norm_set(params.get("midCrosses"))
    lower_crosses = _norm_set(params.get("lowerCrosses"))
    upper_cross = str(boll.get("upper_cross") or "")
    mid_cross = str(boll.get("mid_cross") or "")
    lower_cross = str(boll.get("lower_cross") or "")
    if upper_crosses and upper_cross not in upper_crosses:
        return None
    if mid_crosses and mid_cross not in mid_crosses:
        return None
    if lower_crosses and lower_cross not in lower_crosses:
        return None

    boll_positions = _norm_set(params.get("bollPositions"))
    if boll_positions:
        upper_side = str(boll.get("upper_side") or "")
        lower_side = str(boll.get("lower_side") or "")
        ok = any([
            "above_upper" in boll_positions and upper_side == "above",
            "inside" in boll_positions and upper_side != "above" and lower_side != "below",
            "below_lower" in boll_positions and lower_side == "below",
        ])
        if not ok:
            return None

    price = (snapshot.get("factors") or {}).get("price") or {}
    cell = _base(snapshot)
    cell.update({
        "factorKey": factor_key,
        "close": price.get("close"),
        "upper": boll.get("upper"),
        "mid": boll.get("mid"),
        "lower": boll.get("lower"),
        "upperSide": boll.get("upper_side"),
        "midSide": boll.get("mid_side"),
        "lowerSide": boll.get("lower_side"),
        "upperCross": upper_cross,
        "midCross": mid_cross,
        "lowerCross": lower_cross,
        "eventId": "v4:%s:boll:%s:%s:%s:%s:%s" % (
            domain, cell["symbol"], cell["frequency"],
            upper_cross, mid_cross, lower_cross),
    })
    return cell


def _match_rsi(snapshot: Dict[str, Any], params: Dict[str, Any], factor_key: str, domain: str) -> Optional[Dict[str, Any]]:
    rsi = (snapshot.get("factors") or {}).get("rsi") or {}
    attacks = _norm_set(params.get("attacks"))
    if "long" in attacks and not rsi.get("attack_long"):
        return None
    if "short" in attacks and not rsi.get("attack_short"):
        return None
    cell = _base(snapshot)
    cell.update({
        "factorKey": factor_key,
        "value": rsi.get("value"),
        "attackLong": bool(rsi.get("attack_long")),
        "attackShort": bool(rsi.get("attack_short")),
        "eventId": "v4:%s:rsi:%s:%s:%s:%s:%s" % (
            domain, cell["symbol"], cell["frequency"],
            bool(rsi.get("attack_long")), bool(rsi.get("attack_short")), rsi.get("value")),
    })
    return cell


def _match_gate_rsi_first(snapshot: Dict[str, Any], params: Dict[str, Any], factor_key: str, domain: str) -> List[Dict[str, Any]]:
    entries = (snapshot.get("factors") or {}).get("gate_rsi_first") or []
    gate_types = _norm_set(params.get("gateTypes")) or {"di", "tian"}
    directions = _norm_set(params.get("directions")) or {"short", "long"}
    events = _norm_set(params.get("events")) or {"now"}
    cells = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        gate_type = str(entry.get("gateType") or "")
        direction = str(entry.get("direction") or "")
        if gate_type not in gate_types:
            continue
        if directions and direction not in directions:
            continue
        mode = "now" if entry.get("firstAttackNow") else "elapsed"
        if events and mode not in events:
            continue
        cell = _base(snapshot)
        cell.update({
            "factorKey": factor_key,
            "gateKey": entry.get("gateKey"),
            "gateType": gate_type,
            "gateFreq": entry.get("gateFreq") or cell.get("frequency"),
            "gateOpenAt": entry.get("gateOpenAt"),
            "childFreq": entry.get("childFreq"),
            "direction": direction,
            "threshold": entry.get("threshold"),
            "firstAttackTime": entry.get("firstAttackTime"),
            "firstAttackIdx": entry.get("firstAttackIdx"),
            "firstAttackNow": bool(entry.get("firstAttackNow")),
            "mode": mode,
            "childRsi": entry.get("childRsi"),
            "childRsiLast": entry.get("childRsiLast"),
            "eventId": "v4:%s:gate_rsi_first:%s:%s:%s:%s:%s:%s" % (
                domain, cell["symbol"], cell["frequency"], entry.get("gateKey"),
                direction, entry.get("firstAttackTime"), mode),
        })
        cells.append(cell)
    return cells


def _match_jue_rsi_first(snapshot: Dict[str, Any], params: Dict[str, Any], factor_key: str, domain: str) -> List[Dict[str, Any]]:
    entries = (snapshot.get("factors") or {}).get("jue_rsi_first") or []
    patterns = _norm_set(params.get("patterns")) or {"walk2_break20", "walkB_break80"}
    events = _norm_set(params.get("events")) or {"now"}
    cells = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pattern = str(entry.get("pattern") or "")
        if pattern not in patterns:
            continue
        mode = "now" if entry.get("firstAttackNow") else "elapsed"
        if events and mode not in events:
            continue
        cell = _base(snapshot)
        cell.update({
            "factorKey": factor_key,
            "pattern": pattern,
            "patternLabel": entry.get("patternLabel"),
            "jueThr": entry.get("jueThr"),
            "jueFormationWalk": entry.get("jueFormationWalk"),
            "jueBreakTime": entry.get("jueBreakTime"),
            "gateFreq": entry.get("gateFreq") or cell.get("frequency"),
            "childFreq": entry.get("childFreq"),
            "side": entry.get("side"),
            "attackDirection": entry.get("attackDirection"),
            "firstAttackTime": entry.get("firstAttackTime"),
            "firstAttackIdx": entry.get("firstAttackIdx"),
            "firstAttackNow": bool(entry.get("firstAttackNow")),
            "mode": mode,
            "childRsi": entry.get("childRsi"),
            "childRsiLast": entry.get("childRsiLast"),
            "eventId": "v4:%s:jue_rsi_first:%s:%s:%s:%s:%s:%s" % (
                domain, cell["symbol"], cell["frequency"], pattern,
                entry.get("jueBreakTime"), entry.get("firstAttackTime"), mode),
        })
        cells.append(cell)
    return cells


def _match_segment(snapshot: Dict[str, Any], params: Dict[str, Any], factor_key: str, domain: str) -> Optional[Dict[str, Any]]:
    segment = (snapshot.get("factors") or {}).get("segment") or {}
    code = str(segment.get("current") or "")
    walk_codes = _norm_set(params.get("walkCodes"))
    if walk_codes and code not in walk_codes:
        return None
    if not code:
        return None
    cell = _base(snapshot)
    cell.update({
        "factorKey": factor_key,
        "walkCode": code,
        "eventId": "v4:%s:segment:%s:%s:%s" % (domain, cell["symbol"], cell["frequency"], code),
    })
    return cell


def _match_jue(snapshot: Dict[str, Any], params: Dict[str, Any], factor_key: str, domain: str) -> Optional[Dict[str, Any]]:
    jue = (snapshot.get("factors") or {}).get("jue") or {}
    if not jue.get("exists"):
        return None
    directions = _norm_set(params.get("directions"))
    direction = str(jue.get("direction") or "")
    if directions and direction not in directions:
        return None
    if "broken" in params and isinstance(params["broken"], bool) and bool(jue.get("broken")) != params["broken"]:
        return None
    cell = _base(snapshot)
    cell.update({
        "factorKey": factor_key,
        "direction": direction,
        "price": jue.get("price"),
        "triggerTime": jue.get("trigger_time"),
        "formedTime": jue.get("formed_time"),
        "broken": bool(jue.get("broken")),
        "breakTime": jue.get("break_time"),
        "breakByGap": bool(jue.get("break_by_gap")),
        "eventId": "v4:%s:jue:%s:%s:%s:%s:%s" % (
            domain, cell["symbol"], cell["frequency"], direction,
            bool(jue.get("broken")), jue.get("price")),
    })
    return cell


def _door_first_action(door: Dict[str, Any]) -> bool:
    """兼容东京因子快照字段名：历史门用 is_first，新 registry 用 first_action_edge。"""
    if not isinstance(door, dict):
        return False
    return bool(door.get("first_action") or door.get("is_first"))


def _door_cell(snapshot: Dict[str, Any], kind: str, door: Dict[str, Any], factor_key: str, domain: str) -> Optional[Dict[str, Any]]:
    if not door.get("exists"):
        return None
    cell = _base(snapshot)
    gate_type = "di" if kind == "dimen" else "tian"
    price = (snapshot.get("factors") or {}).get("price") or {}
    first_action = _door_first_action(door)
    cell.update({
        "factorKey": factor_key,
        "gateType": gate_type,
        "gatePrice": door.get("price"),
        "barLow": price.get("low"),
        "barClose": price.get("close"),
        "liveStatus": door.get("status"),
        "formation": door.get("formation"),
        "openEdge": bool(door.get("open_edge")),
        "closeEdge": bool(door.get("close_edge")),
        "openAt": door.get("open_at"),
        "closeAt": door.get("close_at"),
        "firstAction": first_action,
        "t1Time": door.get("t1_time"),
        "t2Time": door.get("t2_time"),
        "crossTime": door.get("cross_time"),
        "formedParentSegment": door.get("formed_parent_segment"),
        "formedSelfSegment": door.get("formed_self_segment"),
        "formedCombo": door.get("formed_combo"),
        "eventId": "v4:%s:door:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s" % (
            domain, cell["symbol"], cell["frequency"], gate_type,
            door.get("status"), door.get("formation"),
            door.get("t1_time"), door.get("t2_time"), door.get("cross_time"),
            bool(door.get("open_edge")), bool(door.get("close_edge")),
            first_action,
            door.get("open_at"), door.get("close_at")),
    })
    return cell


def _match_door(snapshot: Dict[str, Any], params: Dict[str, Any], factor_key: str, domain: str) -> List[Dict[str, Any]]:
    doors = (snapshot.get("factors") or {}).get("door") or {}
    gate_types = _norm_set(params.get("gateTypes")) or {"di", "tian"}
    live_statuses = _norm_set(params.get("liveStatuses"))
    edges = _norm_set(params.get("edges"))
    formations = _norm_set(params.get("formations"))
    cells = []
    for kind in ("dimen", "tian"):
        door = doors.get(kind) or {}
        if not isinstance(door, dict) or not door.get("exists"):
            continue
        gate_type = "di" if kind == "dimen" else "tian"
        if gate_type not in gate_types:
            continue
        status = str(door.get("status") or "")
        if live_statuses and status not in live_statuses:
            continue
        if edges:
            # edges 语义 = 门的“第一次动作”，不是开+关之后的再次开/关。
            first_action = _door_first_action(door)
            if "open" in edges and not (door.get("open_edge") and first_action):
                continue
            if "close" in edges and not (door.get("close_edge") and first_action):
                continue
        if formations and str(door.get("formation") or "") not in formations:
            continue
        cell = _door_cell(snapshot, kind, door, factor_key, domain)
        if cell:
            cells.append(cell)
    return cells


def _match_spatial(snapshot: Dict[str, Any], params: Dict[str, Any], factor_key: str, domain: str) -> List[Dict[str, Any]]:
    spatial = (snapshot.get("factors") or {}).get("spatial") or {}
    raw_gate_types = _norm_set(params.get("gateTypes")) or {"di", "tian"}
    gate_types = set()
    for value in raw_gate_types:
        if value == "di":
            gate_types.add("dimen")
        elif value == "tian":
            gate_types.add("tianmen")
    ma_names = _norm_set(params.get("maNames")) or {"ma52", "ma208", "ma832"}
    positions = _norm_set(params.get("positions"))
    between_rules = _parse_between_rules(params)
    cells = []
    for gate_kind in ("dimen", "tianmen"):
        if gate_kind not in gate_types:
            continue
        if between_rules:
            # 规则内 AND（同一条门必须同时满足上方/下方两条均线），规则间 OR。
            for above_name, below_name in between_rules:
                above = spatial.get("%s_above_%s" % (gate_kind, above_name))
                below = spatial.get("%s_below_%s" % (gate_kind, below_name))
                if above is not True or below is not True:
                    continue
                cell = _base(snapshot)
                cell.update({
                    "factorKey": factor_key,
                    "gateType": "di" if gate_kind == "dimen" else "tian",
                    "gateKind": gate_kind,
                    "maName": "%s_%s" % (above_name, below_name),
                    "above": bool(above),
                    "below": bool(below),
                    "between": {"above": above_name, "below": below_name},
                    "eventId": "v4:%s:spatial:%s:%s:%s:%s:%s:between" % (
                        domain, cell["symbol"], cell["frequency"], gate_kind,
                        above_name, below_name),
                })
                cells.append(cell)
            continue
        for ma_name in ma_names:
            above = spatial.get("%s_above_%s" % (gate_kind, ma_name))
            below = spatial.get("%s_below_%s" % (gate_kind, ma_name))
            if above is not True and below is not True:
                continue
            if positions and not (
                ("above" in positions and above is True)
                or ("below" in positions and below is True)
            ):
                continue
            cell = _base(snapshot)
            cell.update({
                "factorKey": factor_key,
                "gateType": "di" if gate_kind == "dimen" else "tian",
                "gateKind": gate_kind,
                "maName": ma_name,
                "above": bool(above),
                "below": bool(below),
                "eventId": "v4:%s:spatial:%s:%s:%s:%s:%s:%s" % (
                    domain, cell["symbol"], cell["frequency"], gate_kind,
                    ma_name, bool(above), bool(below)),
            })
            cells.append(cell)
    return cells


def _match_tf(snapshot: Dict[str, Any], params: Dict[str, Any], factor_key: str, domain: str) -> List[Dict[str, Any]]:
    tf = (snapshot.get("factors") or {}).get("tf") or {}
    roles = _norm_set(params.get("roles")) or {"parent", "child"}
    walk_codes = _norm_set(params.get("walkCodes"))
    live_statuses = _norm_set(params.get("liveStatuses"))
    cells = []
    for role in ("parent", "child"):
        if role not in roles:
            continue
        item = tf.get(role) or {}
        if not isinstance(item, dict):
            continue
        segment_code = str((item.get("segment") or {}).get("current") or "")
        door = item.get("door") or {}
        dimen_status = str(door.get("dimen_status") or "")
        tian_status = str(door.get("tian_status") or "")
        if walk_codes and segment_code not in walk_codes:
            continue
        if live_statuses and not (dimen_status in live_statuses or tian_status in live_statuses):
            continue
        cell = _base(snapshot)
        cell.update({
            "factorKey": factor_key,
            "role": role,
            "relatedFrequency": item.get("freq"),
            "walkCode": segment_code or None,
            "dimenStatus": dimen_status or None,
            "tianStatus": tian_status or None,
            "eventId": "v4:%s:tf:%s:%s:%s:%s:%s:%s" % (
                domain, cell["symbol"], cell["frequency"], role,
                segment_code, dimen_status, tian_status),
        })
        cells.append(cell)
    return cells


def _filter_symbols(rows: List[Dict[str, Any]], params: Dict[str, Any]) -> List[Dict[str, Any]]:
    symbols = _norm_set(params.get("symbols"))
    if not symbols:
        return rows
    return [row for row in rows if str(row.get("symbol") or "").upper() in symbols]


async def evaluate_group(
    factor_key: str,
    domain: str,
    url: str,
    params: Dict[str, Any],
    ctx: FactorContext,
    matcher,
    verify: bool = True,
) -> Dict[str, Any]:
    raw_frequencies = [
        str(value).strip()
        for value in (params.get("frequencies") or [])
        if str(value).strip()
    ]
    frequencies = list(dict.fromkeys(raw_frequencies)) or list(DEFAULT_FREQS)
    frequency_set = set(frequencies)
    symbols = _norm_set(params.get("symbols"))
    limit = int(params.get("limit") or 100)
    snapshots = await _fetch_for_freqs(url, frequencies, verify)
    # 上游会对每个 pair 计算 bar 新鲜度。stale 的 pair 绝不参与匹配，
    # 避免旧 K 线把错误状态带进筛选/订阅（例如币圈某币 1h 快照停更后
    # 仍然以旧的“走2”命中条件）。
    snapshots = [
        snapshot for snapshot in snapshots
        if isinstance(snapshot, dict) and str(snapshot.get("bar_status") or "ok") != "stale"
    ]
    # 未指定 symbols 时，每个请求的周期都应该有因子池数据；某个周期整体为空
    # 通常意味着东京引擎因子池还没重建完。
    # - edges 门边沿订阅：允许部分频率先匹配（缺失频率只是暂时少匹配，边沿事件
    #   有 open_at/close_at + first_action 保护，不会补推老信号）；
    # - 其它因子：报 503，订阅 pusher 会推迟 baseline 并在下一轮重试，
    #   避免把“空因子池”误当成合法的首轮 baseline。
    edges = _norm_set(params.get("edges"))
    if not symbols and not edges:
        seen_freqs = {str(s.get("freq") or "") for s in snapshots if isinstance(s, dict)}
        missing = sorted(f for f in frequencies if f not in seen_freqs)
        if missing:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": 503,
                    "message": "v4 因子快照缺少周期数据: %s" % ",".join(missing),
                    "data": None,
                },
            )
    cells: List[Dict[str, Any]] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        sym = str(snapshot.get("sym") or "").upper()
        if symbols and sym not in symbols:
            continue
        freq = str(snapshot.get("freq") or "")
        if freq not in frequency_set:
            continue
        matches = matcher(snapshot, params, factor_key, domain)
        if isinstance(matches, list):
            cells.extend(matches)
        elif matches is not None:
            cells.append(matches)
        if limit and len(cells) >= limit:
            cells = cells[:limit]
            break
    cells = cells[:limit] if limit else cells
    updated_at = max((s.get("computed_at") or "" for s in snapshots if isinstance(s, dict)), default=None)
    return _finish(factor_key, cells, updated_at)
