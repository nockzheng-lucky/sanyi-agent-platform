import os
import tempfile

# 必须在导入 app 之前设置环境。
os.environ["SANYI_DATA_DIR"] = os.environ.get("SANYI_DATA_DIR") or tempfile.mkdtemp(prefix="sanyi-agent-test-")
os.environ["LLM_MOCK"] = "1"
os.environ["LLM_API_KEY"] = "unused-in-mock"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import issue_token  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def token_headers():
    created = issue_token(name="test-token", quota_total=100000, rate_limit_per_min=1000)
    return {"X-API-Token": created["token"]}
