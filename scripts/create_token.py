#!/usr/bin/env python3
"""签发一个本地测试令牌。

用法：
  python scripts/create_token.py --name dev --quota 100000 --days 30
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import issue_token  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="签发三易平台测试令牌")
    parser.add_argument("--name", default="dev", help="令牌备注名")
    parser.add_argument("--quota", type=int, default=100000, help="额度点数；-1 表示不限")
    parser.add_argument("--rate", type=int, default=60, help="每分钟请求上限")
    parser.add_argument("--days", type=int, default=None, help="有效天数；不填则长期有效")
    args = parser.parse_args()

    created = issue_token(
        name=args.name,
        quota_total=args.quota,
        rate_limit_per_min=args.rate,
        expires_in_days=args.days,
    )
    print("令牌已创建，只显示这一次，请立即保存：")
    print(created["token"])
    print("name=%s id=%s quota=%s rate=%s/min" % (
        created["name"],
        created["id"],
        created["quota_total"],
        created["rate_limit_per_min"],
    ))


if __name__ == "__main__":
    main()
