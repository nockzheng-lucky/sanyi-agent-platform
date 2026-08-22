#!/usr/bin/env python3
"""创建/提升管理员。

用法：
  python scripts/create_admin.py --phone 13800000000 --password 'strong-password'
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.accounts import create_user, find_user_by_phone, normalize_phone, set_user_role  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="创建三易平台管理员")
    parser.add_argument("--phone", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    phone = normalize_phone(args.phone)
    if phone is None:
        raise SystemExit("手机号格式不正确")
    user = find_user_by_phone(phone)
    if user is None:
        user = create_user(phone, args.password)
        print("created user", user["id"], user["phoneMasked"])
    else:
        print("user exists", user["id"], user["phone_masked"])
    set_user_role(user["id"], "admin")
    print("role=admin set for user", user["id"])


if __name__ == "__main__":
    main()
