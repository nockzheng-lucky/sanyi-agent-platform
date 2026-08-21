"""运行时配置。

所有值都来自环境变量，默认值只保证本地骨架能启动。
生产环境请用密钥管理系统注入，不要把真实密钥写进仓库。
"""
import hashlib
import os
import secrets
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

# 手机号 HMAC 密钥：优先环境变量；本地/首次启动自动生成到 data/phone_secret（600）。
PHONE_SECRET_PATH = DATA_DIR / "phone_secret"


def _phone_hash_secret() -> str:
    env = os.getenv("SANYI_PHONE_HASH_SECRET", "").strip()
    if env:
        return env
    if PHONE_SECRET_PATH.exists():
        return PHONE_SECRET_PATH.read_text(encoding="utf-8").strip()
    value = secrets.token_hex(32)
    PHONE_SECRET_PATH.write_text(value, encoding="utf-8")
    try:
        PHONE_SECRET_PATH.chmod(0o600)
    except OSError:
        pass
    return value


PHONE_HASH_SECRET = _phone_hash_secret()

# 短信：MOCK=true 时验证码直接在返回的 debugCode 里（仅本地/内测）。
SMS_MOCK = _bool("SANYI_SMS_MOCK", True)

# 令牌
TOKEN_RATE_LIMIT_PER_MIN = _int("SANYI_TOKEN_RATE_LIMIT_PER_MIN", 60)
ENABLE_TOKEN_ISSUE_API = _bool("SANYI_ENABLE_TOKEN_ISSUE_API", False)
ADMIN_KEY = os.getenv("SANYI_ADMIN_KEY", "")

# 页面 Agent 的 LLM
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash")
LLM_MOCK = _bool("LLM_MOCK", False)

# 会话 cookie：本地 false；生产 HTTPS 反代后必须 true。
COOKIE_SECURE = _bool("SANYI_COOKIE_SECURE", False)

# 聊天循环
CHAT_MAX_TOOL_ROUNDS = _int("SANYI_CHAT_MAX_TOOL_ROUNDS", 6)
CHAT_MAX_MESSAGES = _int("SANYI_CHAT_MAX_MESSAGES", 20)

# 因子与门信号读取
# 门信号源：生产 green 的 gate_events.sqlite3（只读），与热力图“今日门信号”同源。
GATE_EVENTS_DB = os.getenv("SANYI_GATE_EVENTS_DB", "")

# 信号事件轮询：默认 30 秒一次，只在交易时段内读 SQLite。
SIGNAL_POLL_ENABLED = _bool("SANYI_SIGNAL_POLL_ENABLED", True)
SIGNAL_POLL_SECONDS = _int("SANYI_SIGNAL_POLL_SECONDS", 30)

# 交易时段（HH:MM-HH:MM，逗号分隔；支持跨零点段）。
# 覆盖全品种：白天 09:00-11:30 / 13:00-15:00，夜盘 21:00-02:30。
# 读取的是最新文件，轮询跨过某品种的休市时段不会产生新事件，无害。
TRADING_SESSIONS = os.getenv(
    "SANYI_TRADING_SESSIONS",
    "09:00-11:30,13:00-15:00,21:00-02:30",
)

# 门信号允许的级别
GATE_FREQUENCIES = ("5m", "15m", "1h")

SYSTEM_NAME = "三易引擎 Agent 平台"
