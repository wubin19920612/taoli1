"""Synthetic credentials for legacy business tests; never production data."""
import base64
import hashlib
import time

TEST_TOTP_SECRET = base64.b32encode(b"12345678901234567890").decode()
LEGACY_LABELS = ("secret", "test-password", "test-pass", "test-secret", "pw", "route-permissions-test")


def session_headers(label):
    token = hashlib.sha256(("test-session:" + label).encode()).hexdigest()
    return {"Cookie": "taoli1_session=" + token}


async def seed_sessions(store):
    from app.db.auth import session_hash

    now = time.time()
    for label in LEGACY_LABELS:
        token = session_headers(label)["Cookie"].split("=", 1)[1]
        await store.db.execute(
            "INSERT OR IGNORE INTO auth_sessions VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (session_hash(token), now, now + 86400, now, "127.0.0.1", "Test browser"),
        )
    await store.db.commit()


class IsolatedSessionLookup:
    """Real session checks for route-only tests that deliberately skip lifespan."""
    async def authenticate(self, token, now):
        from app.db.auth import AuthStore
        store = await AuthStore.open(":memory:")
        try:
            await seed_sessions(store)
            return await store.authenticate(token, now)
        finally:
            await store.close()
