#!/usr/bin/env python3
"""订阅推送链路自检。

检查项：
1. 每个 active 订阅都有 baseline_at；
2. 每个 active 订阅当前能成功评估（数据源无错误）；
3. 订阅涉及的每个周期在东京因子池中都有数据（缺失说明因子池在重建或源不可用）；
4. 每个订阅用户都绑定了可用的 Pushplus token。

用法：
  .venv/bin/python scripts/check_subscription_pipeline.py            # 只检查，退出码 0/1
  .venv/bin/python scripts/check_subscription_pipeline.py --alert    # 失败且状态翻转/超过告警间隔时给 user 4 发 Pushplus

状态文件：state/pipeline_check_state.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

BASE_DIR_FOR_IMPORTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR_FOR_IMPORTS not in sys.path:
    sys.path.insert(0, BASE_DIR_FOR_IMPORTS)

from app.composite_evaluator import evaluate_subscription
from app.config import CRYPTO_FACTOR_SCAN_URL, CRYPTO_VERIFY_SSL
from app.engine.subscription_pusher import _actor_for_user, send_pushplus_checked
from app.factors.v4_common import _norm_set, fetch_scan
from app.push_channels import get_pushplus_token
from app.signal_subscriptions import list_all_active_signal_subscriptions

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "state", "pipeline_check_state.json")
ALERT_INTERVAL_SECONDS = 3600
# 青岛→东京跨境链路偶发 ConnectTimeout/快照重建; 单次抖动不应直接推送到用户。
# 连续 2 轮(约 10 分钟)失败才告警, 避免一天到晚被瞬时网络尖峰骚扰。
FAIL_ALERT_AFTER_STREAK = 2
_TRANSIENT_MARKERS = ("ConnectTimeout", "ReadTimeout", "factor_snapshot_stale")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_transient_text(text: str) -> bool:
    return any(marker in (text or "") for marker in _TRANSIENT_MARKERS)


def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        if isinstance(state, dict):
            return state
    except Exception:
        pass
    return {"ok": True, "last_alert_at": None}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


async def _check_frequency_coverage(freq: str) -> str | None:
    try:
        rows = await fetch_scan(CRYPTO_FACTOR_SCAN_URL, freq, timeout=20.0, verify=CRYPTO_VERIFY_SSL)
    except Exception as exc:
        return "factor scan %s error: %s" % (freq, exc)
    if not rows:
        return "factor scan %s returned 0 rows (engine rebuild?)" % freq
    return None


async def _evaluate_with_retry(actor, sub):
    """评估订阅；瞬时网络错误重试一次，避免每 5 分钟一次的正常抖动被记成故障。"""
    last_item = None
    last_exc = None
    for attempt in range(2):
        try:
            item = await evaluate_subscription(actor=actor, sub=sub)
        except Exception as exc:
            last_exc = exc
            if attempt == 0 and _is_transient_text(str(exc)):
                await asyncio.sleep(2.0)
                continue
            raise
        error_text = "%s %s" % (
            item.get("error") or "",
            item.get("conditionErrors") or "",
        )
        if not _is_transient_text(error_text) or attempt == 1:
            return item
        last_item = item
        await asyncio.sleep(2.0)
    return last_item


async def _coverage_with_retry(freq: str) -> str | None:
    issue = await _check_frequency_coverage(freq)
    if issue and _is_transient_text(issue):
        await asyncio.sleep(2.0)
        issue = await _check_frequency_coverage(freq)
    return issue


async def run_checks() -> list[str]:
    issues: list[str] = []
    subs = list_all_active_signal_subscriptions()
    if not subs:
        issues.append("no active signal subscriptions")
        return issues

    for sub in subs:
        sid = sub.get("id")
        name = sub.get("name") or "sub:%s" % sid
        if not sub.get("baselineAt"):
            issues.append("sub %s (%s) baseline not set" % (sid, name))
        try:
            item = await _evaluate_with_retry(
                _actor_for_user(int(sub["userId"])),
                sub,
            )
        except Exception as exc:
            issues.append("sub %s (%s) evaluate exception: %s" % (sid, name, exc))
            continue
        if item.get("error") or item.get("conditionErrors"):
            issues.append(
                "sub %s (%s) evaluate error: %s / %s"
                % (sid, name, item.get("error"), item.get("conditionErrors"))
            )

        filters = sub.get("filters") or {}
        edge_subscription = bool(_norm_set(filters.get("edges")))
        for freq in _norm_set(filters.get("frequencies")):
            issue = await _coverage_with_retry(freq)
            if not issue:
                continue
            # edges 门边沿订阅允许部分频率暂时缺池：缺失频率只会暂时少匹配，
            # 新边沿出现时会被最高优先级补算，不算故障。只打印警告。
            if edge_subscription:
                print("  WARN sub %s (%s) %s" % (sid, name, issue))
            else:
                issues.append("sub %s (%s) %s" % (sid, name, issue))

        token = get_pushplus_token(int(sub["userId"]))
        if not token:
            issues.append("sub %s (%s) pushplus token missing/disabled" % (sid, name))

    # 去重，保持可读顺序
    seen: set[str] = set()
    uniq: list[str] = []
    for item in issues:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alert", action="store_true", help="失败时按状态翻转/1h 间隔给 user 4 发 Pushplus")
    args = parser.parse_args()

    issues = await run_checks()
    state = _load_state()
    now = _now_iso()
    state["last_check_at"] = now

    if not issues:
        state["ok"] = True
        state["fail_streak"] = 0
        _save_state(state)
        print("%s PIPELINE_OK" % now)
        return 0

    fail_streak = int(state.get("fail_streak") or 0) + 1
    state["ok"] = False
    state["fail_streak"] = fail_streak

    print("%s PIPELINE_FAIL issues=%d streak=%d" % (now, len(issues), fail_streak))
    for issue in issues:
        print("  - %s" % issue)

    should_alert = False
    if args.alert and fail_streak >= FAIL_ALERT_AFTER_STREAK:
        try:
            last = datetime.fromisoformat(str(state.get("last_alert_at") or ""))
            delta = datetime.now(timezone.utc) - last
            should_alert = fail_streak == FAIL_ALERT_AFTER_STREAK or delta.total_seconds() >= ALERT_INTERVAL_SECONDS
        except Exception:
            should_alert = True
    if should_alert:
        token = get_pushplus_token(4)
        if token:
            title = "【三易订阅】推送链路自检失败"
            content = "链路自检发现问题：\n" + "\n".join(issues[:12])
            ok, code, _mid = await send_pushplus_checked(title, content, token)
            if not ok:
                print("  ALERT_SEND_FAIL code=%s" % code)
            else:
                state["last_alert_at"] = now
                print("  ALERT_SENT")
    _save_state(state)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
