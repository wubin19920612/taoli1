import sqlite3

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.account_connection import AccountConnectionSecretPayload
from app.services.account_connections import (
    AccountConnectionConfigurationError,
    AccountConnectionService,
    AccountCredentialCipher,
)


def settings(tmp_path, *, password: str = "secret", key: str | None = None) -> Settings:
    return Settings(
        dashboard_password=password,
        account_credentials_master_key=key or Fernet.generate_key().decode(),
        database_url=f"sqlite:///{(tmp_path / 'accounts.db').as_posix()}",
    )


def headers() -> dict[str, str]:
    return {"X-Dashboard-Password": "secret"}


def test_account_credential_cipher_round_trip_and_rejects_missing_or_invalid_key() -> None:
    payload = AccountConnectionSecretPayload(api_key="key", api_secret="secret")
    cipher = AccountCredentialCipher(Fernet.generate_key().decode())

    encrypted = cipher.encrypt(payload)

    assert "key" not in encrypted
    assert "secret" not in encrypted
    assert cipher.decrypt(encrypted) == payload
    for value in ("", "not-a-fernet-key"):
        unavailable = AccountCredentialCipher(value)
        assert unavailable.ready is False
        with pytest.raises(AccountConnectionConfigurationError):
            unavailable.encrypt(payload)


def binance_payload(label: str, suffix: str) -> dict:
    return {
        "exchange": "binance",
        "account_label": label,
        "enabled": True,
        "include_spot": True,
        "include_futures": True,
        "api_key": f"key-{suffix}",
        "api_secret": f"secret-{suffix}",
    }


def test_account_connection_api_requires_nonempty_dashboard_password(tmp_path) -> None:
    no_password = settings(tmp_path, password="")
    with TestClient(create_app(settings=no_password)) as client:
        response = client.get("/api/account-connections")
    assert response.status_code == 503

    with TestClient(create_app(settings=settings(tmp_path))) as client:
        response = client.get("/api/account-connections")
    assert response.status_code == 401


def test_multiple_same_exchange_connections_are_encrypted_masked_and_persistent(tmp_path) -> None:
    app_settings = settings(tmp_path)
    with TestClient(create_app(settings=app_settings)) as client:
        first = client.post(
            "/api/account-connections",
            headers=headers(),
            json=binance_payload("主账户", "abcd"),
        )
        second = client.post(
            "/api/account-connections",
            headers=headers(),
            json=binance_payload("子账户", "wxyz"),
        )
        listed = client.get("/api/account-connections", headers=headers())

    assert first.status_code == 201
    assert second.status_code == 201
    body = listed.json()
    assert [item["account_label"] for item in body["connections"]] == ["主账户", "子账户"]
    serialized = listed.text
    assert "key-abcd" not in serialized
    assert "secret-abcd" not in serialized
    assert "encrypted_credentials" not in serialized
    assert body["connections"][0]["credential_hint"] == "****abcd"

    with TestClient(create_app(settings=app_settings)) as client:
        restored = client.get("/api/account-connections", headers=headers()).json()
    assert len(restored["connections"]) == 2


def test_blank_secret_update_preserves_encrypted_credentials(tmp_path) -> None:
    app_settings = settings(tmp_path)
    database_path = tmp_path / "accounts.db"
    with TestClient(create_app(settings=app_settings)) as client:
        created = client.post(
            "/api/account-connections",
            headers=headers(),
            json=binance_payload("旧名称", "1234"),
        ).json()
        response = client.put(
            f"/api/account-connections/{created['id']}",
            headers=headers(),
            json={"account_label": "新名称", "api_key": "", "api_secret": ""},
        )
    assert response.status_code == 200
    assert response.json()["account_label"] == "新名称"

    db = sqlite3.connect(database_path)
    encrypted = db.execute(
        "SELECT encrypted_credentials FROM account_connections WHERE id = ?",
        (created["id"],),
    ).fetchone()[0]
    db.close()
    credentials = AccountCredentialCipher(
        app_settings.account_credentials_master_key
    ).decrypt(encrypted)
    assert credentials == AccountConnectionSecretPayload(
        api_key="key-1234",
        api_secret="secret-1234",
    )


def test_hyperliquid_connections_require_public_address_and_keep_exact_dex(tmp_path) -> None:
    with TestClient(create_app(settings=settings(tmp_path))) as client:
        invalid = client.post(
            "/api/account-connections",
            headers=headers(),
            json={
                "exchange": "hyperliquid",
                "account_label": "HL",
                "include_spot": False,
                "include_futures": True,
                "dex": "xyz",
                "public_address": "not-an-address",
            },
        )
        created = client.post(
            "/api/account-connections",
            headers=headers(),
            json={
                "exchange": "hyperliquid",
                "account_label": "HL xyz",
                "include_spot": False,
                "include_futures": True,
                "dex": "xyz",
                "public_address": "0x111111111111111111111111111111111111abcd",
            },
        )

    assert invalid.status_code == 422
    assert created.status_code == 201
    assert created.json()["dex"] == "xyz"
    assert created.json()["credential_hint"] == "0x1111...abcd"
    assert "11111111111111111111111111111111" not in created.text


@pytest.mark.asyncio
async def test_account_connection_service_closes_cached_providers_on_shutdown() -> None:
    class Provider:
        closed = False

        async def close(self) -> None:
            self.closed = True

    provider = Provider()
    service = AccountConnectionService(
        repository=None,  # type: ignore[arg-type]
        master_key=Fernet.generate_key().decode(),
        dashboard_password="secret",
    )
    service._provider_cache["account_1"] = ("stamp", [provider])  # type: ignore[list-item]

    await service.aclose()

    assert provider.closed is True
    assert service._provider_cache == {}
