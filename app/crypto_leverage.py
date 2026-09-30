"""币圈建议杠杆：以 ETH 的杠杆为锚，按波动率反比换算。

口径（与用户确认）：
- 波动率 = max(1h ATR14%，最近 24 根 1h K 线的振幅%)；
- 建议杠杆 = floor(ETH锚定杠杆 × ETH波动率 / 该币波动率)；
- 下限 10x，上限 50x（低于 10x 的统一写 10x）。

数据源：东京币圈服务器 GET /api/klines/{sym}/1h。
结果只做研究观察，不构成投资建议。
"""
import asyncio
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from .config import (
    CRYPTO_CONNECT_TIMEOUT_SECONDS,
    CRYPTO_LEVERAGE_ANCHOR_LEVERAGE,
    CRYPTO_LEVERAGE_ANCHOR_SYMBOL,
    CRYPTO_LEVERAGE_CACHE_SECONDS,
    CRYPTO_LEVERAGE_MAX,
    CRYPTO_LEVERAGE_MIN,
    CRYPTO_TIMEOUT_SECONDS,
    CRYPTO_VERIFY_SSL,
    CRYPTO_VOLATILITY_API_URL,
)

_CACHE: Dict[str, Dict[str, Any]] = {}
_VOLATILITY_MAP_CACHE: Dict[str, Any] = {"_at": 0.0, "items": None}
_VOLATILITY_MAP_LOCK = asyncio.Lock()
_ATR_PERIOD = 14
_DAY_BARS = 24
_MAX_CONCURRENCY = 4


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _true_range(bar: Dict[str, Any], prev_close: Optional[float]) -> Optional[float]:
    high = _finite(bar.get("high"))
    low = _finite(bar.get("low"))
    if high is None or low is None or high < low:
        return None
    ranges = [high - low]
    if prev_close is not None:
        ranges.extend([abs(high - prev_close), abs(low - prev_close)])
    return max(ranges)


def volatility_from_bars(bars: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """从 1h K 线计算 atrPct / dayRangePct / volatilityPct。"""
    rows = [bar for bar in bars if isinstance(bar, dict)]
    if len(rows) < _ATR_PERIOD + 1:
        return None

    closes = [_finite(bar.get("close")) for bar in rows]
    if any(value is None or value <= 0 for value in closes):
        return None

    trs: List[float] = []
    for index in range(len(rows) - _ATR_PERIOD, len(rows)):
        prev_close = closes[index - 1] if index > 0 else None
        tr = _true_range(rows[index], prev_close)
        if tr is None:
            return None
        trs.append(tr)
    if not trs:
        return None
    last_close = closes[-1]
    atr_pct = (sum(trs) / len(trs)) / last_close * 100.0

    day_bars = rows[-_DAY_BARS:]
    highs = [_finite(bar.get("high")) for bar in day_bars]
    lows = [_finite(bar.get("low")) for bar in day_bars]
    if any(value is None for value in highs + lows):
        return None
    day_range_pct = (max(highs) - min(lows)) / last_close * 100.0

    return {
        "atrPct": round(atr_pct, 3),
        "dayRangePct": round(day_range_pct, 3),
        "volatilityPct": round(max(atr_pct, day_range_pct), 3),
    }


def suggested_leverage(symbol_volatility_pct: float, eth_volatility_pct: float) -> int:
    """ETH=50x 锚定，波动率反比换算，取整后限制在 [10, 50]。"""
    eth_vol = float(eth_volatility_pct or 0.0)
    symbol_vol = float(symbol_volatility_pct or 0.0)
    if eth_vol <= 0 or symbol_vol <= 0:
        return CRYPTO_LEVERAGE_MIN
    raw = float(CRYPTO_LEVERAGE_ANCHOR_LEVERAGE) * eth_vol / symbol_vol
    leverage = int(math.floor(raw))
    return max(CRYPTO_LEVERAGE_MIN, min(CRYPTO_LEVERAGE_MAX, leverage))


def _parse_utc_time(value: Any) -> Optional[float]:
    """把门结构时间解析成 UTC epoch 秒。币圈东京服务器的无时区时间是 UTC。"""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    now_utc = datetime.now(timezone.utc)
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text[:-1] + "+00:00").timestamp()
        if "+" in text[10:] or text.endswith("+00:00"):
            return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text[:19], fmt)
            return parsed.replace(tzinfo=timezone.utc).timestamp()
        except (TypeError, ValueError):
            continue
    for fmt in ("%m/%d %H:%M:%S", "%m/%d %H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
        except (TypeError, ValueError):
            continue
        parsed = parsed.replace(year=now_utc.year, tzinfo=timezone.utc)
        if parsed > now_utc + timedelta(days=1):
            parsed = parsed.replace(year=now_utc.year - 1)
        return parsed.timestamp()
    return None


def _gate_formation_time(match: Dict[str, Any]) -> Optional[float]:
    for key in ("crossRaw", "xAnchorTime", "crossTime", "t1Raw", "t1Time"):
        value = _parse_utc_time(match.get(key))
        if value is not None:
            return value
    return None


def _event_signal_time(match: Dict[str, Any]) -> Optional[float]:
    for key in ("eventAt", "openAt", "closeAt", "generatedAt", "barTime"):
        value = _parse_utc_time(match.get(key))
        if value is not None:
            return value
    return None


def _is_stale_gate(match: Dict[str, Any], metrics: Dict[str, Any]) -> bool:
    """门形成/动作时间早于该币当前连续入选时间 → 入 list 之前的老门，不推。

    事件线信号同样要过这一关：合约宇宙冷纳入某币时，引擎会为历史门补发
    type=new，事件时间可能已按 T2 修正，但仍早于 selectedSince，必须过滤。
    """
    if match.get("gateType") not in ("tian", "di"):
        return False
    selected_since = metrics.get("selectedSince") or metrics.get("selectedSinceHour")
    if not selected_since:
        return False
    entry_epoch = _parse_utc_time(selected_since)
    if entry_epoch is None:
        return False
    if match.get("signalKind") == "event":
        event_epoch = _event_signal_time(match)
        if event_epoch is None:
            return False
        return event_epoch < entry_epoch
    formed_epoch = _gate_formation_time(match)
    if formed_epoch is None:
        return False
    return formed_epoch < entry_epoch


async def _fetch_volatility_map() -> Optional[List[Dict[str, Any]]]:
    """优先读东京批接口：一次返回当前合约宇宙的 1h 波动率。"""
    if not CRYPTO_VOLATILITY_API_URL:
        return None
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            CRYPTO_TIMEOUT_SECONDS,
            connect=min(CRYPTO_CONNECT_TIMEOUT_SECONDS, CRYPTO_TIMEOUT_SECONDS),
        ),
        verify=CRYPTO_VERIFY_SSL,
    ) as client:
        try:
            response = await client.get(CRYPTO_VOLATILITY_API_URL)
        except httpx.HTTPError:
            return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    items = payload.get("items") if isinstance(payload, dict) else None
    return items if isinstance(items, list) else None


async def _ensure_volatility_map() -> Optional[List[Dict[str, Any]]]:
    now = time.monotonic()
    if _VOLATILITY_MAP_CACHE["items"] is not None and now - _VOLATILITY_MAP_CACHE["_at"] < CRYPTO_LEVERAGE_CACHE_SECONDS:
        return _VOLATILITY_MAP_CACHE["items"]
    async with _VOLATILITY_MAP_LOCK:
        now = time.monotonic()
        if _VOLATILITY_MAP_CACHE["items"] is not None and now - _VOLATILITY_MAP_CACHE["_at"] < CRYPTO_LEVERAGE_CACHE_SECONDS:
            return _VOLATILITY_MAP_CACHE["items"]
        items = await _fetch_volatility_map()
        if items is not None:
            _VOLATILITY_MAP_CACHE["_at"] = time.monotonic()
            _VOLATILITY_MAP_CACHE["items"] = items
            for item in items:
                if not isinstance(item, dict) or not item.get("sym"):
                    continue
                _CACHE[str(item["sym"]).upper()] = {"_at": now, "metrics": item}
        return items


async def get_volatility(symbol: str) -> Optional[Dict[str, Any]]:
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        return None
    now = time.monotonic()
    cached = _CACHE.get(symbol)
    if cached and now - cached["_at"] < CRYPTO_LEVERAGE_CACHE_SECONDS:
        return cached["metrics"]

    items = await _ensure_volatility_map()
    if items is None:
        return None

    metrics = _CACHE.get(symbol)
    if metrics and now - metrics["_at"] < CRYPTO_LEVERAGE_CACHE_SECONDS:
        return metrics["metrics"]
    return None


async def _annotate_one(match: Dict[str, Any], eth_volatility: Optional[float]) -> Optional[Dict[str, Any]]:
    symbol = str(match.get("symbol") or "").strip().upper()
    if not symbol or not eth_volatility:
        return None
    metrics = await get_volatility(symbol)
    if not metrics:
        return None
    match["volatilityPct"] = metrics["volatilityPct"]
    match["atrPct"] = metrics["atrPct"]
    match["dayRangePct"] = metrics["dayRangePct"]
    match["suggestedLeverage"] = suggested_leverage(metrics["volatilityPct"], eth_volatility)
    match["leverageAnchor"] = {
        "symbol": CRYPTO_LEVERAGE_ANCHOR_SYMBOL,
        "leverage": CRYPTO_LEVERAGE_ANCHOR_LEVERAGE,
        "volatilityPct": round(float(eth_volatility), 3),
    }
    return metrics


async def annotate_matches(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """给币圈匹配行补充建议杠杆，并过滤“入 list 之前形成的老门”。"""
    matches = [match for match in matches if isinstance(match, dict)]
    if not matches:
        return matches

    anchor = await get_volatility(CRYPTO_LEVERAGE_ANCHOR_SYMBOL)
    if not anchor:
        return matches
    eth_volatility = anchor["volatilityPct"]

    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def guarded(match: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        async with semaphore:
            return await _annotate_one(match, eth_volatility)

    metrics_by_match = await asyncio.gather(
        *(guarded(match) for match in matches),
        return_exceptions=True,
    )

    kept: List[Dict[str, Any]] = []
    for match, metrics in zip(matches, metrics_by_match):
        if isinstance(metrics, BaseException) or not isinstance(metrics, dict):
            kept.append(match)
            continue
        if not _is_stale_gate(match, metrics):
            kept.append(match)
    return kept
