"""因子实现包。每个因子一个文件，保持可独立测试、可独立上下线。"""
from .base import FactorSpec
from .crypto_market import crypto_market
from .dimen_gate_signal import dimen_gate_signal
from .gate_condition import gate_condition
from .jue_direction import jue_direction
from .wave_jue_combo import wave_jue_combo

FACTOR_SPECS = [dimen_gate_signal, jue_direction, gate_condition, wave_jue_combo, crypto_market]

__all__ = ["FactorSpec", "FACTOR_SPECS"]
