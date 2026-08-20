"""因子实现包。每个因子一个文件，保持可独立测试、可独立上下线。"""
from .dimen_gate_15m_long import dimen_gate_15m_long
from .base import FactorSpec

FACTOR_SPECS = [dimen_gate_15m_long]

__all__ = ["FactorSpec", "FACTOR_SPECS"]
