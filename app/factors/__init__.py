"""v4 基础因子包。

旧因子已全部退役；当前只注册 v4 基础因子。
"""
from .base import FactorSpec
from .v4_basic_factors import V4_BASIC_FACTOR_SPECS
from .v4_jue_opportunity import JUE_OPPORTUNITY_SPECS

FACTOR_SPECS = V4_BASIC_FACTOR_SPECS + JUE_OPPORTUNITY_SPECS

__all__ = ["FactorSpec", "FACTOR_SPECS"]
