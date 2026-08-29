"""因子标准结构。

一个因子对外只有四种东西：
1. 元信息（factorKey / name / description / 参数 schema / 输出 schema）
2. 价格（cost，单位暂时用平台额度点）
3. 风险提示（riskNote，Agent 必须转述）
4. 计算函数（内部实现，三易引擎数据不直接暴露）

输出统一字段：
- factorKey
- signal        BULLISH / BEARISH / NEUTRAL / 因子自定义枚举
- score         0-100，可选
- summary       给 Agent 和用户看的结论
- generatedAt   信号生成时间，必须显式展示
- details       结构化明细，按“最小必要”原则裁剪
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional


@dataclass
class FactorContext:
    token_id: int
    request_id: Optional[str] = None


Handler = Callable[[Dict[str, Any], FactorContext], Awaitable[Dict[str, Any]]]


@dataclass
class FactorSpec:
    factor_key: str
    name: str
    description: str
    params_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    cost: int
    risk_note: str
    handler: Handler
    cache_seconds: int = 0
    status: str = "active"
    tags: List[str] = field(default_factory=list)
    shadow_only: bool = False
    domain: str = "futures"

    def descriptor(self) -> Dict[str, Any]:
        return {
            "factorKey": self.factor_key,
            "name": self.name,
            "description": self.description,
            "paramsSchema": self.params_schema,
            "outputSchema": self.output_schema,
            "cost": self.cost,
            "cacheSeconds": self.cache_seconds,
            "riskNote": self.risk_note,
            "status": self.status,
            "tags": list(self.tags),
            "shadowOnly": self.shadow_only,
            "domain": self.domain,
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
