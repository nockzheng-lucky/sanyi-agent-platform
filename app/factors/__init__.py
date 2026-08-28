"""因子实现包。每个因子一个文件，保持可独立测试、可独立上下线。"""
from .base import FactorSpec
from .dimen_gate_signal import dimen_gate_signal
from .jue_direction import jue_direction

FACTOR_SPECS = [dimen_gate_signal, jue_direction]

__all__ = ["FactorSpec", "FACTOR_SPECS"]
