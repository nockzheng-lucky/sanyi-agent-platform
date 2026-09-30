"""破诀机会因子回归测试（只读接入引擎已算好的信号）。"""
from app.factor_registry import registry
from app.factors.v4_jue_opportunity import _extract_opportunities


def test_extract_futures_embedded_shape():
    payload = {
        "updated_at": "2026-09-30T01:00:00+00:00",
        "jue_opportunities": {
            "opportunities": [
                {"sym": "C0", "freq": "15m", "thr": 20, "formation_walk": "走2", "break_time": "2026-09-30 09:15:00"},
            ]
        },
    }
    rows = _extract_opportunities(payload)
    assert len(rows) == 1
    assert rows[0]["sym"] == "C0"


def test_extract_crypto_list_shape():
    payload = {
        "opportunities": [
            {"sym": "SOLUSDT", "freq": "15m", "thr": 80, "formation_walk": "走1"},
        ]
    }
    rows = _extract_opportunities(payload)
    assert len(rows) == 1
    assert rows[0]["sym"] == "SOLUSDT"


def test_factor_registered_with_schema():
    spec = registry.get("futures_jue_opportunity")
    assert spec is not None
    props = spec.params_schema["properties"]
    assert "patterns" in props
    assert "frequencies" in props
    assert "walk2_break20" in props["patterns"]["items"]["enum"]
