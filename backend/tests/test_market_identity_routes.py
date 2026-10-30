from datetime import UTC, datetime

from fastapi.testclient import TestClient
from test_market_identity_validation import binding, market

from app.core.config import Settings
from app.main import create_app


def test_identity_write_auth_revision_validation_and_invalidation(tmp_path):
    app = create_app(
        settings=Settings(
            database_url=f"sqlite:///{(tmp_path / 'identity.db').as_posix()}",
            dashboard_password="test-password",
        ),
        start_background_workers=False,
    )
    with TestClient(app) as client:
        value = {
            "binding": binding(market()).model_dump(mode="json"),
            "expected_revision": 0,
            "reason": "核验官方 PRL 资料",
        }
        assert client.put("/api/market-identities/binding", json=value).status_code == 401
        headers = {"X-Dashboard-Password": "test-password"}
        response = client.put("/api/market-identities/binding", json=value, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 1
        assert (
            client.put("/api/market-identities/binding", json=value, headers=headers).status_code
            == 409
        )
        data = client.get("/api/market-identities?search=PRL", headers={"X-Dashboard-Password": "test-password"}).json()
        assert data["total"] == 1 and data["items"][0]["asset_id"] == "perle"
        value["binding"]["evidence"] = []
        value["expected_revision"] = 1
        assert (
            client.put("/api/market-identities/binding", json=value, headers=headers).status_code
            == 422
        )


def test_prl_asset_groups_exclude_false_pair_and_statistics_require_selection(tmp_path):
    app = create_app(
        settings=Settings(
            database_url=f"sqlite:///{(tmp_path / 'groups.db').as_posix()}", dashboard_password="pw"
        ),
        start_background_workers=False,
    )
    now = datetime.now(UTC)
    a = market().model_copy(update={"timestamp": now})
    b = market("hyperliquid", "io:PRL", 1.94, "io").model_copy(update={"timestamp": now})
    with TestClient(app) as client:
        for row, asset in ((a, "perle"), (b, "pearl")):
            response = client.put(
                "/api/market-identities/binding",
                headers={"X-Dashboard-Password": "pw"},
                json={
                    "binding": binding(row, asset).model_dump(mode="json"),
                    "expected_revision": 0,
                    "reason": "官方资料确认不同资产",
                },
            )
            assert response.status_code == 200
        app.state.snapshot_store.set_all_markets([a, b])
        response = client.get("/api/instruments/PRL", headers={"X-Dashboard-Password": "pw"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["identity_ambiguous"]
        assert {g["asset_id"] for g in data["asset_groups"]} == {"perle", "pearl"}
        assert not data["spreads"]
        assert client.get("/api/instruments/PRL/statistics", headers={"X-Dashboard-Password": "pw"}).status_code == 409
