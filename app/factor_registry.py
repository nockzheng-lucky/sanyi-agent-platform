"""因子注册表：元信息 + 参数校验 + 缓存 + 成功后扣费 + 审计日志。

单一事实来源是 app/factors/ 下注册的 FactorSpec。
页面 Agent 和未来 MCP/REST 接入都走同一套 evaluate，避免多套逻辑分叉。
"""
import asyncio
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from jsonschema import Draft7Validator, ValidationError

from .accounts import charge_user_key
from .db import charge_after_success, log_usage
from .factors import FACTOR_SPECS, FactorSpec
from .factors.base import FactorContext, now_iso


class FactorRegistry:
    def __init__(self) -> None:
        self._specs: Dict[str, FactorSpec] = {}
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        for spec in FACTOR_SPECS:
            self.register(spec)

    def register(self, spec: FactorSpec) -> None:
        if spec.factor_key in self._specs:
            raise ValueError("duplicate factorKey: %s" % spec.factor_key)
        Draft7Validator.check_schema(spec.params_schema)
        Draft7Validator.check_schema(spec.output_schema)
        self._specs[spec.factor_key] = spec

    def descriptors(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        out = []
        for spec in self._specs.values():
            if spec.status != "active" and not include_inactive:
                continue
            out.append(spec.descriptor())
        return out

    def get(self, factor_key: str) -> Optional[FactorSpec]:
        return self._specs.get(factor_key)

    def validate_params(self, spec: FactorSpec, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            Draft7Validator(spec.params_schema).validate(params or {})
        except ValidationError as exc:
            path = "/".join(str(p) for p in exc.absolute_path)
            where = path + " " if path else ""
            raise HTTPException(
                status_code=422,
                detail={
                    "code": 422,
                    "message": "参数不合法：%s%s" % (where, exc.message),
                    "data": None,
                },
            )
        return params or {}

    def _cache_key(self, token_id: int, factor_key: str, params: Dict[str, Any]) -> str:
        payload = json.dumps(params, sort_keys=True, ensure_ascii=False)
        return "%s:%s:%s" % (
            token_id,
            factor_key,
            hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )

    async def evaluate(
        self,
        token_record: dict,
        factor_key: str,
        params: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        spec = self.get(factor_key)
        if spec is None:
            raise HTTPException(
                status_code=404,
                detail={"code": 404, "message": "因子不存在：%s" % factor_key, "data": None},
            )
        if spec.status != "active":
            raise HTTPException(
                status_code=403,
                detail={"code": 403, "message": "因子已下线：%s" % factor_key, "data": None},
            )

        params = self.validate_params(spec, params)
        ctx = FactorContext(token_id=token_record["id"], request_id=request_id)

        # 短 TTL 缓存：同样的参数在 cache_seconds 内直接复用，降低三易引擎压力。
        loop = asyncio.get_running_loop()
        if spec.cache_seconds > 0:
            cache_key = self._cache_key(token_record["id"], factor_key, params)
            cached = self._cache.get(cache_key)
            if cached and (loop.time() - cached[0]) < spec.cache_seconds:
                return cached[1]

        result = await spec.handler(params, ctx)
        result = self._normalize(spec, result)
        if spec.cache_seconds > 0:
            self._cache[cache_key] = (loop.time(), result)

        # 成功后扣费；扣费失败按调研到的 Hitick 规则：直接报错，不返回结果。
        if token_record.get("_table") == "user_keys":
            charged = charge_user_key(token_record["id"], spec.cost)
        else:
            charged = charge_after_success(token_record["id"], spec.cost)
        if not charged:
            raise HTTPException(
                status_code=402,
                detail={"code": 402, "message": "额度不足，扣费失败", "data": None},
            )

        log_usage(
            token_id=token_record["id"],
            user_id=token_record.get("_user_id"),
            key_id=token_record.get("_key_id"),
            service="factor",
            action="evaluate",
            factor_key=factor_key,
            cost=spec.cost,
            status="ok",
            request_id=request_id,
            detail=json.dumps({"params": params, "summary": result.get("summary", "")}, ensure_ascii=False)[:2000],
        )
        return result

    @staticmethod
    def _normalize(spec: FactorSpec, result: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(result or {})
        result.setdefault("factorKey", spec.factor_key)
        result.setdefault("signal", "NEUTRAL")
        result.setdefault("score", 0)
        result.setdefault("summary", "")
        result.setdefault("generatedAt", now_iso())
        result.setdefault("riskNote", spec.risk_note)
        result["details"] = result.get("details") or {}
        # 不让因子偷换 factorKey，避免审计日志与返回不一致。
        if result["factorKey"] != spec.factor_key:
            result["factorKey"] = spec.factor_key
        return result


registry = FactorRegistry()
