"""v4 基础因子注册（期货 + 币圈各一组）。

每个因子 = 一个可独立查询/订阅的基础条件组，Agent 可以用 conditions 组合：
例如 primary=crypto_door(15m open) + context=crypto_segment(1h 走2)。

所有基础因子均为 event_based=True；eventId 按 symbol+freq+状态值生成，
状态变化才产生新事件，不会每个轮询周期重复推送。
"""

from __future__ import annotations

from functools import partial
from typing import Any, Dict, List

from ..config import (
    CRYPTO_FACTOR_SCAN_TIMEOUT_SECONDS,
    CRYPTO_FACTOR_SCAN_URL,
    CRYPTO_VERIFY_SSL,
    FUTURES_FACTOR_SCAN_TIMEOUT_SECONDS,
    FUTURES_FACTOR_SCAN_URL,
)
from .base import FactorSpec
from . import v4_common
from .v4_common import (
    _match_boll,
    _match_door,
    _match_gate_rsi_first,
    _match_jue,
    _match_jue_rsi_first,
    _match_ma,
    _match_ma_triple,
    _match_macd,
    _match_price,
    _match_rsi,
    _match_segment,
    _match_spatial,
    _match_tf,
    evaluate_group,
)

_LEVELS = list(v4_common.FREQ_LEVELS)
from .v4_common import ALL_MA_RELATION_KEYS

_BETWEEN_MA_NAMES = ("ma52", "ma208", "ma832")


def _between_filter_fields(description: str) -> Dict[str, Any]:
    """“夹在两条均线之间”的 AND 条件。above=标的在其上方的均线，below=标的在其下方的均线。"""
    return {
        "between": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "above": {"type": "string", "enum": list(_BETWEEN_MA_NAMES)},
                    "below": {"type": "string", "enum": list(_BETWEEN_MA_NAMES)},
                },
                "required": ["above", "below"],
                "additionalProperties": False,
            },
            "description": description,
        }
    }


def _relation_filter_fields(relations_description: str) -> Dict[str, Any]:
    """MA 组内/跨组统一关系筛选字段，两个均线因子共用同一套语义。

    relations 中 ma169_ma208 表示左侧 MA169 相对右侧 MA208；
    配合 relationStates=above 即 MA169>MA208。
    """
    return {
        "relations": {
            "type": "array",
            "items": {"type": "string", "enum": list(ALL_MA_RELATION_KEYS)},
            "description": relations_description,
        },
        "relationStates": {
            "type": "array",
            "items": {"type": "string", "enum": ["above", "near", "below"]},
            "description": "关系状态过滤：above=左侧在右侧上方；below=左侧在右侧下方；near=贴线（偏离绝对值≤tolerancePct）。",
        },
        "relationCrosses": {
            "type": "array",
            "items": {"type": "string", "enum": ["cross_up", "cross_down", "none"]},
            "description": "价格穿均线，或均线间金叉/死叉过滤。",
        },
        "requireAllRelations": {
            "type": "boolean",
            "description": "true=选中的每个关系都必须满足 relationStates/relationCrosses；默认 false=任一满足。",
        },
        "tolerancePct": {
            "type": "number",
            "minimum": 0,
            "maximum": 50,
            "default": 0.05,
            "description": "near 容差（%），默认 0.05。",
        },
    }


def _params(extra: Dict[str, Any]) -> Dict[str, Any]:
    properties = {
        "frequencies": {
            "type": "array",
            "items": {"type": "string", "enum": _LEVELS},
            "description": "可选。周期过滤，例如 [\"15m\", \"1h\"]；支持 1m/5m/15m/30m/1h/1d/1w/1M；默认 15m / 1h。",
        },
        "symbols": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选。只返回这些品种/交易对。",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1000,
            "default": 100,
            "description": "可选。最多返回多少条，默认 100。",
        },
    }
    properties.update(extra)
    return {"type": "object", "properties": properties, "additionalProperties": False}


_OUTPUT = {
    "type": "object",
    "properties": {
        "factorKey": {"type": "string"},
        "signal": {"type": "string", "enum": ["MATCH", "NONE"]},
        "score": {"type": "number"},
        "summary": {"type": "string"},
        "generatedAt": {"type": "string", "format": "date-time"},
        "details": {
            "type": "object",
            "properties": {
                "updatedAt": {"type": ["string", "null"]},
                "matchedCells": {"type": "integer"},
                "returnedCells": {"type": "integer"},
                "cells": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["updatedAt", "matchedCells", "returnedCells", "cells"],
        },
        "riskNote": {"type": "string"},
    },
    "required": ["factorKey", "signal", "score", "summary", "generatedAt", "details", "riskNote"],
}


def _make(
    key: str,
    domain: str,
    group: str,
    name: str,
    description: str,
    extra: Dict[str, Any],
    matcher,
    url: str,
    verify: bool = True,
) -> FactorSpec:
    return FactorSpec(
        factor_key=key,
        name=name,
        description=description,
        params_schema=_params(extra),
        output_schema=_OUTPUT,
        cost=0,
        cache_seconds=15,
        risk_note="基础因子仅描述最新已收盘状态，不构成投资建议；信号订阅会在状态变化时提醒。",
        handler=partial(evaluate_group, key, domain, url, matcher=matcher, verify=verify),
        tags=[domain, group, "v4", "basic"],
        shadow_only=False,
        domain=domain,
        event_based=True,
        kind="basic",
        group=group,
    )


def _build_specs(domain: str, url: str, verify: bool = True, label: str = "") -> List[FactorSpec]:
    return [
        _make("%s_price" % domain, domain, "price", "%s · 价格 K 线" % label,
              "最新已收盘 K 线的高/低/收盘价与阴阳方向；可过滤 yang/yin/undetermined。",
              {"directions": {"type": "array", "items": {"type": "string", "enum": ["yang", "yin", "undetermined"]}}},
              _match_price, url, verify),
        _make("%s_ma" % domain, domain, "ma", "%s · 均线 MA52/208/832" % label,
              "MA52/MA208/MA832 与收盘价及三者之间的上下/交叉关系；"
              "relations 字段同时支持 MA25/144/169 与 MA52/208/832 的跨组比较，"
              "例如 ma169_ma208 + above 表示 MA169>MA208。"
              "要表达“收盘价夹在两条均线之间”，请用 between 字段（AND 语义）。",
              {
                  "maNames": {"type": "array", "items": {"type": "string", "enum": ["ma52", "ma208", "ma832"]}},
                  "priceSides": {"type": "array", "items": {"type": "string", "enum": ["above", "below", "equal"]}},
                  "priceCrosses": {"type": "array", "items": {"type": "string", "enum": ["cross_up", "cross_down", "none"]}},
                  "pairCrosses": {"type": "array", "items": {"type": "string"}},
                  **_between_filter_fields(
                      "收盘价夹在两条均线之间：above=价格在其上方的均线，below=价格在其下方的均线。"
                      "例如 [{\"above\":\"ma208\",\"below\":\"ma832\"}] 表示 MA832>收盘价>MA208。"
                      "多条规则为 OR；每条规则内部为 AND。"
                  ),
                  **_relation_filter_fields(
                      "要查询/订阅的 MA 关系子集。左侧相对右侧；"
                      "ma169_ma208 + relationStates=above 表示 MA169>MA208。"
                  ),
                  "ma52AboveMa208": {
                      "type": ["boolean", "null"],
                      "description": "可选。true=只看 MA52 在 MA208 上方；false=只看 MA52 在 MA208 下方；null=不启用。数据不足时不命中 true/false。",
                  },
                  "ma52AboveMa832": {
                      "type": ["boolean", "null"],
                      "description": "可选。true=只看 MA52 在 MA832 上方；false=只看 MA52 在 MA832 下方；null=不启用。数据不足时不命中 true/false。",
                  },
                  "ma208AboveMa832": {
                      "type": ["boolean", "null"],
                      "description": "可选。true=只看 MA208 在 MA832 上方；false=只看 MA208 在 MA832 下方；null=不启用。数据不足时为 null，不命中 true/false。",
                  },
                  "allowMissingMaRelation": {
                      "type": ["boolean", "null"],
                      "description": "可选。默认 false。为 true 且任一均线上下/关系条件已启用时，该关系数据不足(null)也放行，供推送标注“未确认”。",
                  },
              },
              _match_ma, url, verify),

          _make("%s_ma_triple" % domain, domain, "ma", "%s · 均线 MA25/144/169" % label,
                "MA25/MA144/MA169 与收盘价的六种相对位置关系（价格-单线、均线-均线）"
                "及复杂排列：priceZone/maOrder/alignment/signature。"
                "relations 同时支持 MA25/144/169 与 MA52/208/832 的跨组比较，"
                "例如 ma169_ma208 + relationStates=above 表示 MA169>MA208。"
                "requireAllRelations=true 时所有选中关系都必须满足；near 按 tolerancePct 判定。",
                {
                    **_relation_filter_fields(
                        "要查询/订阅的关系子集；缺省表示 MA25/144/169 六种关系全部输出。"
                        "跨组键如 ma169_ma208 表示左侧 MA169 相对右侧 MA208。"
                    ),
                    "priceZones": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["above_all", "below_all", "inside"]},
                        "description": "收盘价相对三条均线：above_all=全部在上；below_all=全部在下；inside=有上有下。",
                    },
                    "maOrders": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["bull", "bear", "mixed"]},
                        "description": "三线排列：bull=MA25>MA144>MA169；bear=MA25<MA144<MA169；其余 mixed。",
                    },
                    "alignments": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["bull", "bear", "mixed"]},
                        "description": "完整排列：bull=收盘价>MA25>MA144>MA169；bear=收盘价<MA25<MA144<MA169；其余 mixed。",
                    },
                    "signatures": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "六位签名过滤，如 [\"++++++\", \"------\"]。顺序：[C-25,C-144,C-169,25-144,25-169,144-169]，每位置 +/0/-/x。",
                    },
                },
                _match_ma_triple, url, verify),
        _make("%s_macd" % domain, domain, "macd", "%s · MACD" % label,
              "DIF/DEA/HIST 最新值、正负域与零轴穿越方向。",
              {
                  "difSides": {"type": "array", "items": {"type": "string", "enum": ["positive", "negative", "zero"]}},
                  "histSides": {"type": "array", "items": {"type": "string", "enum": ["positive", "negative", "zero"]}},
                  "difCrosses": {"type": "array", "items": {"type": "string", "enum": ["cross_up", "cross_down", "none"]}},
                  "histCrosses": {"type": "array", "items": {"type": "string", "enum": ["cross_up", "cross_down", "none"]}},
              },
              _match_macd, url, verify),
        _make("%s_boll" % domain, domain, "boll", "%s · 布林带 BOLL(52,2)" % label,
              "布林带(52,2) 上/中/下轨最新值、收盘价相对轨道位置，"
              "以及最新已收盘 K 线穿越上/中/下轨的方向。"
              "下穿布林上轨用 upperCrosses=[\"cross_down\"]；"
              "上穿布林下轨用 lowerCrosses=[\"cross_up\"]。",
              {
                  "upperCrosses": {"type": "array", "items": {"type": "string", "enum": ["cross_up", "cross_down", "none"]}},
                  "midCrosses": {"type": "array", "items": {"type": "string", "enum": ["cross_up", "cross_down", "none"]}},
                  "lowerCrosses": {"type": "array", "items": {"type": "string", "enum": ["cross_up", "cross_down", "none"]}},
                  "bollPositions": {"type": "array", "items": {"type": "string", "enum": ["above_upper", "inside", "below_lower"]}},
              },
              _match_boll, url, verify),
        _make("%s_rsi" % domain, domain, "rsi", "%s · RSI3 进攻" % label,
              "RSI3 最新值，以及是否 >80（attack_long）或 <20（attack_short）。",
              {"attacks": {"type": "array", "items": {"type": "string", "enum": ["long", "short"]}}},
              _match_rsi, url, verify),
        _make("%s_gate_rsi_first" % domain, domain, "rsi", "%s · 开门后子级首次RSI攻击" % label,
              "门开门之后，其子级别 RSI3 第一次跌破 20（short）或升破 80（long）的事件。"
              "同一扇门只产生一次 firstAttackNow 事件，后续再次穿越不会重复。"
              "events=[\"now\"] 只返回第一次攻击发生的那根 K 线；events=[\"elapsed\"] 查询已发生过首次攻击的门。",
              {
                  "gateTypes": {"type": "array", "items": {"type": "string", "enum": ["di", "tian"]}},
                  "directions": {"type": "array", "items": {"type": "string", "enum": ["short", "long"]}},
                  "events": {"type": "array", "items": {"type": "string", "enum": ["now", "elapsed"]}},
              },
              _match_gate_rsi_first, url, verify),
        _make("%s_jue_rsi_first" % domain, domain, "jue", "%s · 破诀后-2级别首次RSI攻击" % label,
              "成走2·破20（walk2_break20）之后，-2 级别第一次 RSI3 跌破 20；"
              "成走B·破80（walkB_break80）之后，-2 级别第一次 RSI3 升破 80。"
              "同一破诀只产生一次 firstAttackNow 事件，不重复。"
              "events=[\"now\"] 只返回首次攻击发生的那根 K 线。",
              {
                  "patterns": {"type": "array", "items": {"type": "string", "enum": ["walk2_break20", "walkB_break80"]}},
                  "events": {"type": "array", "items": {"type": "string", "enum": ["now", "elapsed"]}},
              },
              _match_jue_rsi_first, url, verify),
        _make("%s_segment" % domain, domain, "segment", "%s · 走势段" % label,
              "当前二进门段：1/2/3/A/B/C。",
              {"walkCodes": {"type": "array", "items": {"type": "string", "enum": ["1", "2", "3", "A", "B", "C"]}}},
              _match_segment, url, verify),
        _make("%s_jue" % domain, domain, "jue", "%s · 诀" % label,
              "最新 RSI3 诀是否存在、方向、价格、成立/破诀时间。",
              {
                  "directions": {"type": "array", "items": {"type": "string", "enum": ["long", "short"]}},
                  "broken": {"type": "boolean"},
              },
              _match_jue, url, verify),
        _make("%s_door" % domain, domain, "door", "%s · 门" % label,
              "地门/天门的最新状态、门上/门下、开门/关门边沿及 T2 形成组合。",
              {
                  "gateTypes": {"type": "array", "items": {"type": "string", "enum": ["di", "tian"]}},
                  "liveStatuses": {"type": "array", "items": {"type": "string"}},
                  "edges": {"type": "array", "items": {"type": "string", "enum": ["open", "close"]}},
                  "formations": {"type": "array", "items": {"type": "string", "enum": ["门上", "门下"]}},
              },
              _match_door, url, verify),
        _make("%s_spatial" % domain, domain, "spatial", "%s · 门价与均线空间关系" % label,
              "地门/天门价格相对 MA52/MA208/MA832 的上下关系。"
              "要表达“门价夹在两条均线之间”，请用 between 字段（AND 语义）。",
              {
                  "gateTypes": {"type": "array", "items": {"type": "string", "enum": ["di", "tian"]}},
                  "maNames": {"type": "array", "items": {"type": "string", "enum": ["ma52", "ma208", "ma832"]}},
                  "positions": {"type": "array", "items": {"type": "string", "enum": ["above", "below"]}},
                  **_between_filter_fields(
                      "门价夹在两条均线之间：above=门价在其上方的均线，below=门价在其下方的均线。"
                      "例如 [{\"above\":\"ma208\",\"below\":\"ma832\"}] 表示 MA832>门价>MA208。"
                      "多条规则为 OR；每条规则内部为 AND。设置后不再使用 positions 的 OR 过滤。"
                  ),
              },
              _match_spatial, url, verify),
        _make("%s_tf" % domain, domain, "tf", "%s · 跨级别状态" % label,
              "父级/子级的走势段与天地门状态，适合组合订阅时做 context 条件。",
              {
                  "roles": {"type": "array", "items": {"type": "string", "enum": ["parent", "child"]}},
                  "walkCodes": {"type": "array", "items": {"type": "string", "enum": ["1", "2", "3", "A", "B", "C"]}},
                  "liveStatuses": {"type": "array", "items": {"type": "string"}},
              },
              _match_tf, url, verify),
    ]


FUTURES_BASIC_FACTORS = _build_specs(
    "futures", FUTURES_FACTOR_SCAN_URL, verify=True, label="期货"
)
CRYPTO_BASIC_FACTORS = _build_specs(
    "crypto",
    CRYPTO_FACTOR_SCAN_URL,
    verify=CRYPTO_VERIFY_SSL,
    label="币圈",
)

V4_BASIC_FACTOR_SPECS = FUTURES_BASIC_FACTORS + CRYPTO_BASIC_FACTORS
