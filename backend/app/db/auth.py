"""Dedicated connection and serialized transactions for authentication state."""
import asyncio
import hashlib
import secrets
import sqlite3
from contextlib import asynccontextmanager

import aiosqlite

from app.core.totp import matching_counter

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_sessions (
    id_hash TEXT PRIMARY KEY, created_at REAL NOT NULL, expires_at REAL NOT NULL,
    last_seen_at REAL NOT NULL, ip TEXT NOT NULL, user_agent TEXT NOT NULL, revoked_at REAL
);
CREATE INDEX IF NOT EXISTS auth_sessions_expiry ON auth_sessions(expires_at);
CREATE TABLE IF NOT EXISTS auth_totp_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1), last_counter INTEGER NOT NULL
);
INSERT OR IGNORE INTO auth_totp_state VALUES (1, -1);
CREATE TABLE IF NOT EXISTS auth_failures (ip TEXT NOT NULL, failed_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS auth_failures_time ON auth_failures(failed_at);
CREATE TABLE IF NOT EXISTS auth_locks (scope TEXT PRIMARY KEY, until_at REAL NOT NULL);
"""


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthStore:
    def __init__(self, db):
        self.db = db
        self.lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: str):
        db = await aiosqlite.connect(path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=5000")
        await db.executescript(AUTH_SCHEMA)
        await db.commit()
        return cls(db)

    async def close(self):
        await self.db.close()

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def login(self, *, password_ok, secret, code, ip, browser, now, days):
        """Consume a code and issue its session atomically, across processes too."""
        async with self.transaction():
            await self.db.execute("DELETE FROM auth_failures WHERE failed_at <= ?", (now - 900,))
            await self.db.execute("DELETE FROM auth_locks WHERE until_at <= ?", (now,))
            await self.db.execute("DELETE FROM auth_sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL", (now,))
            async with self.db.execute(
                "SELECT 1 FROM auth_locks WHERE scope IN ('global', ?)", ("ip:" + ip,),
            ) as cursor:
                if await cursor.fetchone():
                    return None, False
            async with self.db.execute("SELECT last_counter FROM auth_totp_state WHERE singleton = 1") as cursor:
                last = (await cursor.fetchone())[0]
            candidate = matching_counter(secret, code, now, last)
            counter = candidate if password_ok else None
            if counter is None:
                await self.db.execute("INSERT INTO auth_failures VALUES (?, ?)", (ip, now))
                async with self.db.execute(
                    "SELECT COUNT(*), COALESCE(SUM(ip = ?), 0) FROM auth_failures", (ip,),
                ) as cursor:
                    total, per_ip = await cursor.fetchone()
                locked = False
                for scope, count, limit in (("global", total, 20), ("ip:" + ip, per_ip, 5)):
                    if count >= limit:
                        await self.db.execute("INSERT OR REPLACE INTO auth_locks VALUES (?, ?)", (scope, now + 900))
                        locked = True
                return None, locked
            await self.db.execute("UPDATE auth_totp_state SET last_counter = ? WHERE singleton = 1", (counter,))
            token = secrets.token_hex(32)
            await self.db.execute(
                "INSERT INTO auth_sessions VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (session_hash(token), now, now + days * 86400, now, ip, browser),
            )
            return token, False

    async def authenticate(self, token, now):
        if not token or len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
            return None
        hashed = session_hash(token)
        async with self.lock:
            async with self.db.execute(
                "SELECT last_seen_at FROM auth_sessions WHERE id_hash = ? AND expires_at > ? AND revoked_at IS NULL",
                (hashed, now),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
            # Authentication reads must not take the global SQLite writer lock.
            # Last-seen is approximate (one minute); lock contention only skips this metadata update.
            if now - row[0] >= 60:
                try:
                    await self.db.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE id_hash = ?", (now, hashed))
                    await self.db.commit()
                except sqlite3.OperationalError as exc:
                    await self.db.rollback()
                    if getattr(exc, "sqlite_errorcode", None) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                        raise
                except BaseException:
                    await self.db.rollback()
                    raise
        return hashed

    async def sessions(self, current, now):
        async with self.lock:
            async with self.db.execute(
                "SELECT * FROM auth_sessions WHERE expires_at > ? AND revoked_at IS NULL ORDER BY created_at DESC",
                (now,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [{**{key: row[key] for key in ("created_at", "expires_at", "last_seen_at", "ip", "user_agent")},
                 "current": row["id_hash"] == current} for row in rows]

    async def revoke(self, current, now, *, all_sessions=False):
        async with self.transaction():
            if all_sessions:
                await self.db.execute("UPDATE auth_sessions SET revoked_at = ? WHERE revoked_at IS NULL", (now,))
            else:
                await self.db.execute("UPDATE auth_sessions SET revoked_at = ? WHERE id_hash = ?", (now, current))
