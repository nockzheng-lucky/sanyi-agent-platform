"""运行时配置。

所有值都来自环境变量，默认值只保证本地骨架能启动。
生产环境请用密钥管理系统注入，不要把真实密钥写进仓库。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


DATA_DIR = Path(os.getenv("SANYI_DATA_DIR", "./data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "platform.sqlite3"

# 令牌
TOKEN_RATE_LIMIT_PER_MIN = _int("SANYI_TOKEN_RATE_LIMIT_PER_MIN", 60)
ENABLE_TOKEN_ISSUE_API = _bool("SANYI_ENABLE_TOKEN_ISSUE_API", False)
ADMIN_KEY = os.getenv("SANYI_ADMIN_KEY", "")

# 页面 Agent 的 LLM
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_MOCK = _bool("LLM_MOCK", False)

# 聊天循环
CHAT_MAX_TOOL_ROUNDS = _int("SANYI_CHAT_MAX_TOOL_ROUNDS", 6)
CHAT_MAX_MESSAGES = _int("SANYI_CHAT_MAX_MESSAGES", 20)

# 因子
FACTOR_DIMEN_GATE_MOCK = _bool("FACTOR_DIMEN_GATE_MOCK", True)

SYSTEM_NAME = "三易引擎 Agent 平台"
