"""对外数据契约。

统一返回 {code, message, data}，与调研的 Orange Hitick 保持同一种风格，
后续让 Agent 和开发者都容易处理。
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: Any = None


class FactorDescriptor(BaseModel):
    factorKey: str
    name: str
    description: str
    paramsSchema: Dict[str, Any]
    outputSchema: Dict[str, Any]
    cost: int
    cacheSeconds: int
    riskNote: str
    status: str = "active"
    tags: List[str] = Field(default_factory=list)


class FactorEvaluateRequest(BaseModel):
    factorKey: str
    params: Dict[str, Any] = Field(default_factory=dict)
    requestId: Optional[str] = None


class TokenIssueRequest(BaseModel):
    name: str
    quotaTotal: int = 0
    rateLimitPerMin: int = 60
    expiresInDays: Optional[int] = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    requestId: Optional[str] = None
    # 用户在“因子列表”页加载到 Agent 的因子；仅作为系统提示上下文，不绕过工具校验。
    factorKeys: List[str] = Field(default_factory=list, max_length=20)
