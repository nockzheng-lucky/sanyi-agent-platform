def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_factors_requires_token(client):
    resp = client.get("/api/v1/factors")
    assert resp.status_code == 401


def test_factor_flow(client, token_headers):
    resp = client.get("/api/v1/factors", headers=token_headers)
    assert resp.status_code == 200
    body = resp.json()
    keys = [f["factorKey"] for f in body["data"]["factors"]]
    assert "dimen_gate_signal" in keys

    resp = client.post(
        "/api/v1/factors/evaluate",
        headers=token_headers,
        json={"factorKey": "dimen_gate_signal", "params": {"maxAgeMinutes": 120}},
    )
    assert resp.status_code == 200
    result = resp.json()["data"]
    assert result["factorKey"] == "dimen_gate_signal"
    assert isinstance(result["details"]["events"], list)
    assert result["generatedAt"]

    # 月费制：因子调用不逐次扣额度，只审计。
    me = client.get("/api/v1/me", headers=token_headers).json()["data"]["token"]
    assert me["quotaUsed"] == 0


def test_factor_invalid_params(client, token_headers):
    resp = client.post(
        "/api/v1/factors/evaluate",
        headers=token_headers,
        json={"factorKey": "dimen_gate_signal", "params": {"maxAgeMinutes": -1}},
    )
    assert resp.status_code == 422


def test_unknown_factor(client, token_headers):
    resp = client.post(
        "/api/v1/factors/evaluate",
        headers=token_headers,
        json={"factorKey": "no_such_factor", "params": {}},
    )
    assert resp.status_code == 404
