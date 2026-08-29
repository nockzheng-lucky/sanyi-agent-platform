#!/usr/bin/env python3
"""开启/关闭指定账号的影子模式（币圈等隐藏因子只对该账号可见）。

用法：
  python scripts/set_shadow_mode.py --phone 15822408517 --on
  python scripts/set_shadow_mode.py --phone 15822408517 --off
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.accounts import find_user_by_phone, normalize_phone, set_user_shadow_mode  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="设置影子模式账号")
    parser.add_argument("--phone", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--on", dest="enabled", action="store_true")
    group.add_argument("--off", dest="enabled", action="store_false")
    args = parser.parse_args()

    phone = normalize_phone(args.phone)
    if phone is None:
        raise SystemExit("手机号格式不正确")
    user = find_user_by_phone(phone)
    if user is None:
        raise SystemExit("用户不存在")
    set_user_shadow_mode(user["id"], args.enabled)
    print("shadow_mode=%s for user %s (%s)" % (1 if args.enabled else 0, user["id"], user["phone_masked"]))


if __name__ == "__main__":
    main()
