"""횡단 endpoint — /healthz 등."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import gateway.main as main_mod

    with TestClient(main_mod.app) as c:
        yield c


def test_healthz_returns_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
