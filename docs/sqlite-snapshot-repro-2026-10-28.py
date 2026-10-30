import asyncio, sqlite3, aiosqlite

class _Materialized:
    """Rows read inside the same worker call as execute; statement already finished."""
    def __init__(self, cur):
        self.description, self.rowcount, self.lastrowid = cur.description, cur.rowcount, cur.lastrowid
        self._rows = cur.fetchall(); cur.close(); self.arraysize = 1
    def fetchone(self): return self._rows.pop(0) if self._rows else None
    def fetchall(self): rows, self._rows = self._rows, []; return rows
    def fetchmany(self, n=None): n = n or self.arraysize; rows, self._rows = self._rows[:n], self._rows[n:]; return rows
    def close(self): self._rows = []
    def __iter__(self): return iter(self.fetchall())

class Fixed(sqlite3.Connection):
    def execute(self, sql, parameters=(), /):
        cur = super().execute(sql, parameters)
        return _Materialized(cur) if cur.description is not None else cur

async def conn(p, factory=sqlite3.Connection):
    db = await aiosqlite.connect(p, factory=factory); db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL"); await db.execute("PRAGMA busy_timeout=5000"); return db

async def case(factory, name):
    import os
    for s in ("", "-wal", "-shm"):
        try: os.remove("t2.db"+s)
        except FileNotFoundError: pass
    a, b = await conn("t2.db", factory), await conn("t2.db")
    await a.execute("CREATE TABLE s(k TEXT PRIMARY KEY, v TEXT)")
    await a.executemany("INSERT INTO s VALUES(?,?)", [(str(i), "v") for i in range(50)]); await a.commit()
    errors = 0
    for _ in range(200):
        async def reader():
            cur = await a.execute("SELECT * FROM s")      # queue op 1
            await asyncio.sleep(0)
            rows = await cur.fetchall()                      # queue op 2
            assert len(rows) >= 50 and rows[0]["k"] is not None
        async def other_commit():
            await b.execute("INSERT OR REPLACE INTO s VALUES('o','1')"); await b.commit()
        async def writer():
            nonlocal errors
            await asyncio.sleep(0)
            try:
                await a.execute("INSERT OR REPLACE INTO s VALUES('w','1')"); await a.commit()
            except sqlite3.OperationalError:
                errors += 1
                await a.rollback()
        await asyncio.gather(reader(), other_commit(), writer())
    print(name, "errors_in_200_rounds=", errors)
    await a.close(); await b.close()

async def main():
    await case(sqlite3.Connection, "current")
    await case(Fixed, "materialized")
asyncio.run(main())
