import asyncio
import json
import os
import re
import sqlite3
import sys
import tarfile
import time
from contextlib import closing
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite
import httpx
import pytest
from auth_helpers import TEST_TOTP_SECRET

from app.core.config import Settings
from app.core.totp import totp_at
from app.main import create_app
from app.models.mobile_inbox import MAX_IMAGE_BYTES
from app.services import mobile_inbox

INBOX = "/api/mobile/inbox"
PNG = b"\x89PNG\r\n\x1a\n" + b"png contents"
JPEG = b"\xff\xd8\xff\xe0jpeg contents"
WEBP = b"RIFF\x10\x00\x00\x00WEBPwebp contents"


@pytest.fixture
async def inbox_app(tmp_path):
    settings = Settings(
        environment="production",
        dashboard_password="fixture",
        dashboard_totp_secret=TEST_TOTP_SECRET,
        dashboard_api_token="a" * 64,
        database_url=f"sqlite:///{tmp_path / 'radar.db'}",
        mobile_inbox_dir=str(tmp_path / "inbox"),
    )
    app = create_app(settings=settings, start_background_workers=False)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=transport, base_url="https://test",
    ) as client:
        yield app, client


async def _login(client, counter, scope="full"):
    response = await client.post("/api/auth/login", json={
        "password": "fixture", "code": totp_at(TEST_TOTP_SECRET, counter),
        "scope": scope,
    })
    assert response.status_code == 200, response.text


@pytest.fixture
async def signed_in(inbox_app):
    app, client = inbox_app
    await _login(client, int(time.time() // 30))
    return app, client


async def _upload(client, *, client_id=None, images=(), **fields):
    parts = [("client_id", (None, client_id if client_id is not None else str(uuid4())))]
    parts.extend((name, (None, value)) for name, value in fields.items())
    parts.extend(("images", ("../../client-name.svg", image, "image/svg+xml")) for image in images)
    return await client.post(INBOX, files=parts)


def _item(response):
    assert response.status_code == 200, response.text
    return response.json()


async def test_all_routes_require_session(inbox_app):
    _, client = inbox_app
    item_url = f"{INBOX}/{uuid4()}"
    client.headers["X-Api-Token"] = "a" * 64
    responses = [await _upload(client, text="secret"), await client.get(INBOX),
                 await client.get(f"{item_url}/images/1"), await client.delete(item_url)]
    assert [response.status_code for response in responses] == [401] * 4


async def test_mobile_readonly_session_can_create_and_delete_without_unlock(inbox_app):
    _, client = inbox_app
    await _login(client, int(time.time() // 30))
    item = _item(await _upload(client, text="delete from mobile"))
    assert (await client.post("/api/auth/logout")).status_code == 200
    await _login(client, int(time.time() // 30) + 1, "mobile_readonly")
    assert _item(await client.get(INBOX)) == [item]
    created = await _upload(client, images=(PNG,))
    deleted = await client.delete(f"{INBOX}/{item['id']}")
    assert (created.status_code, deleted.status_code) == (200, 200)


async def test_text_and_multiple_images_idempotent_and_safe_download(signed_in, monkeypatch):
    app, client = signed_in
    text_only = _item(await _upload(client, text="  分享原文\n下一行  ", source_app="bad/应用"))
    assert text_only["text"] == "  分享原文\n下一行  "
    assert text_only["source_app"] is None and text_only["image_count"] == 0
    assert not app.state.mobile_inbox.root.exists()

    client_id = str(uuid4())
    payload = {"client_id": client_id, "images": (PNG, JPEG, WEBP), "note": "请研究",
               "source_app": "app.share_1"}
    write_count = 0
    write_image = mobile_inbox._write_image

    async def track_write(part, path):
        nonlocal write_count
        write_count += 1
        return await write_image(part, path)

    monkeypatch.setattr(mobile_inbox, "_write_image", track_write)
    first, other = await asyncio.gather(_upload(client, **payload), _upload(client, **payload))
    created = _item(first)
    assert _item(other) == created
    assert write_count == len(payload["images"])
    assert not app.state.mobile_inbox._client_locks
    assert _item(await _upload(client, client_id=client_id.upper(), text="changed")) == created
    assert set(created) == {"id", "created_at", "text", "note", "source_app", "image_count",
                            "status", "result", "synced_at"}
    assert created["id"] != client_id and created["text"] is None
    assert created["image_count"] == 3 and created["status"] == "new"
    assert created["note"] == "请研究" and created["source_app"] == "app.share_1"
    assert created["result"] is None and created["synced_at"] is None

    root = app.state.mobile_inbox.root
    item_dir = root / created["id"]
    assert list(root.iterdir()) == [item_dir]
    assert {path.name for path in item_dir.iterdir()} == {"1.png", "2.jpg", "3.webp"}
    url = f"{INBOX}/{created['id']}"
    for index, (media_type, contents) in enumerate(zip(("png", "jpeg", "webp"), payload["images"]), 1):
        response = await client.get(f"{url}/images/{index}")
        assert response.status_code == 200
        assert response.headers["content-type"] == f"image/{media_type}"
        assert response.content == contents
    for invalid in (f"{url}/images/%2e%2e%2f1", f"{INBOX}/%2e%2e%2fprivate/images/1",
                    f"{url}/images/0", f"{url}/images/4"):
        assert (await client.get(invalid)).status_code in (404, 422)

    assert _item(await client.delete(url)) == {"ok": True}
    assert not item_dir.exists()
    assert (await client.delete(url)).status_code == 404
    assert (await client.get(f"{url}/images/1")).status_code == 404
    assert _item(await client.get(INBOX)) == [text_only]


@pytest.mark.parametrize("fields", [
    {}, {"text": "  "}, {"client_id": "private/path", "text": "some text"},
    {"text": "注" * 20001}, {"note": "注" * 2001},
])
async def test_invalid_form_uses_chinese_422(signed_in, fields):
    _, client = signed_in
    response = await _upload(client, **fields)
    assert response.status_code == 422
    assert response.json() == {"detail": mobile_inbox.INVALID_FORM}
    assert _item(await client.get(INBOX)) == []


async def test_invalid_multipart_filename_uses_chinese_422(signed_in):
    app, client = signed_in
    prefix = _client_id_part(uuid4())
    body = prefix + (
        b'Content-Disposition: form-data; name="images"; filename*0="utf-8\'broken"\r\n\r\n'
        + PNG + b"\r\n--inbox-boundary--\r\n"
    )
    response = await client.post(INBOX, headers=MULTIPART, content=body)
    assert response.status_code == 422
    assert response.json() == {"detail": mobile_inbox.INVALID_FORM}
    assert not app.state.mobile_inbox.root.exists()


async def test_image_before_client_id_is_rejected_without_writing(signed_in):
    app, client = signed_in
    response = await client.post(INBOX, files=[
        ("images", ("image.png", PNG, "image/png")), ("client_id", (None, str(uuid4()))),
    ])
    assert response.status_code == 422
    assert not app.state.mobile_inbox.root.exists()


async def test_field_limits_source_sanitization_and_list_pagination(signed_in, monkeypatch):
    _, client = signed_in
    monkeypatch.setattr(mobile_inbox, "time_ns", lambda: 1_796_000_000_000_000_000)
    first = _item(await _upload(client, text="📝" * 20000, note="注" * 2000, source_app="a" * 200))
    second = _item(await _upload(client, note="note only", source_app="a" * 201))
    third = _item(await _upload(client, images=(PNG,), source_app="a" * 1000))
    assert len(first["text"]) == 20000 and len(first["note"]) == 2000
    assert first["source_app"] == "a" * 200
    assert second["source_app"] is None and third["source_app"] is None
    assert _item(await client.get(INBOX, params={"limit": 2})) == [third, second]
    assert _item(await client.get(INBOX, params={"before": second["created_at"]})) == [first]
    assert _item(await client.get(INBOX, params={"before": first["created_at"]})) == []
    for params in ({"limit": 0}, {"limit": 201}, {"before": "private"}):
        response = await client.get(INBOX, params=params)
        assert response.status_code == 422
        assert response.json() == {"detail": mobile_inbox.INVALID_FORM}


@pytest.mark.parametrize(("images", "status"), [((PNG,) * 10, 422), ((PNG, b"not an image"), 415)])
async def test_rejected_images_leave_no_files_or_rows(signed_in, images, status):
    app, client = signed_in
    assert (await _upload(client, images=images)).status_code == status
    assert not list(app.state.mobile_inbox.root.iterdir())
    assert _item(await client.get(INBOX)) == []


async def test_stream_stops_reading_above_image_size_limit(signed_in):
    app, client = signed_in
    boundary = "streaming-boundary"
    content_type = f"multipart/form-data; boundary={boundary}"
    sent = 0

    async def body():
        nonlocal sent
        yield (f"--{boundary}\r\nContent-Disposition: form-data; name=\"client_id\"\r\n\r\n"
               f"{uuid4()}\r\n--{boundary}\r\n"
               f"Content-Disposition: form-data; name=\"images\"; filename=\"large.png\"\r\n\r\n").encode()
        yield PNG
        for _ in range(MAX_IMAGE_BYTES // mobile_inbox.CHUNK_SIZE + 4):
            sent += mobile_inbox.CHUNK_SIZE
            yield b"x" * mobile_inbox.CHUNK_SIZE
        pytest.fail("Server consumed the entire oversized request")

    response = await client.post(INBOX, headers={"content-type": content_type}, content=body())
    assert response.status_code == 413
    assert MAX_IMAGE_BYTES <= sent <= MAX_IMAGE_BYTES + 3 * mobile_inbox.CHUNK_SIZE
    assert not list(app.state.mobile_inbox.root.iterdir())
    assert _item(await client.get(INBOX)) == []
    at_limit = _item(await _upload(client, images=(PNG.ljust(MAX_IMAGE_BYTES, b"x"),)))
    stored = app.state.mobile_inbox.root / at_limit["id"] / "1.png"
    assert stored.stat().st_size == MAX_IMAGE_BYTES


async def test_database_failure_after_rename_cleans_up_files(signed_in):
    app, client = signed_in
    await app.state.mobile_inbox.db.execute("""
        CREATE TEMP TRIGGER reject_inbox BEFORE INSERT ON mobile_inbox_items
        BEGIN SELECT RAISE(ABORT, 'test failure'); END
    """)
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        await _upload(client, images=(PNG,))
    assert not list(app.state.mobile_inbox.root.iterdir())
    assert _item(await client.get(INBOX)) == []


async def test_quota_is_per_session_and_counts_only_new_successes(inbox_app, monkeypatch):
    app, client = inbox_app
    clock = time.time_ns()
    monkeypatch.setattr(mobile_inbox, "time_ns", lambda: clock)
    counter = int(time.time() // 30)
    await _login(client, counter)
    client_id = str(uuid4())
    first = _item(await _upload(client, client_id=client_id, text="keep"))
    removed = _item(await _upload(client, text="remove later"))
    for _ in range(117):
        _item(await _upload(client, text="more"))
    assert (await _upload(client)).status_code == 422
    inbox = app.state.mobile_inbox
    check_quota = inbox._check_quota
    both_checked = asyncio.Barrier(2)

    async def check_then_wait(session):
        await check_quota(session)
        await both_checked.wait()

    with monkeypatch.context() as patch:
        patch.setattr(inbox, "_check_quota", check_then_wait)
        attempts = await asyncio.wait_for(
            asyncio.gather(_upload(client, text="last"), _upload(client, text="over")), timeout=10,
        )
    assert sorted(response.status_code for response in attempts) == [200, 429]
    assert _item(await _upload(client, client_id=client_id, text="retry")) == first

    async with aiosqlite.connect(app.state.settings.sqlite_path, isolation_level=None) as restarted_db:
        restarted_db.row_factory = aiosqlite.Row
        app.state.mobile_inbox = mobile_inbox.MobileInbox(restarted_db, str(inbox.root))
        [(session_hash, count)] = await restarted_db.execute_fetchall(
            "SELECT session_hash, COUNT(*) FROM mobile_inbox_items GROUP BY session_hash",
        )
        assert session_hash and session_hash != client.cookies["taoli1_session"]
        assert count == 120
        assert (await _post_without_remainder(client, uuid4())).status_code == 429
        assert _item(await _post_without_remainder(client, client_id)) == first
        assert not inbox.root.exists()
        assert not app.state.mobile_inbox._client_locks
        app.state.mobile_inbox = inbox

    assert _item(await client.delete(f"{INBOX}/{removed['id']}")) == {"ok": True}
    _item(await _upload(client, text="row removed, new slot"))
    assert (await _upload(client, text="over again")).status_code == 429
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as other:
        await _login(other, counter + 1)
        _item(await _upload(other, text="second session"))
    clock += 3601 * 1_000_000_000
    _item(await _upload(client, text="next hour"))


MULTIPART = {"content-type": "multipart/form-data; boundary=inbox-boundary"}


def _client_id_part(client_id: str | UUID) -> bytes:
    return (f'--inbox-boundary\r\nContent-Disposition: form-data; name="client_id"\r\n\r\n'
            f"{client_id}\r\n--inbox-boundary\r\n").encode("ascii")


async def _post_without_remainder(client, client_id):
    async def body():
        yield _client_id_part(client_id)
        pytest.fail("Server read past the client_id multipart part")

    return await client.post(INBOX, headers=MULTIPART, content=body())


async def test_startup_removes_staging_and_only_old_orphans(signed_in, caplog):
    app, client = signed_in
    stored = _item(await _upload(client, images=(PNG,)))
    root = app.state.mobile_inbox.root
    old_orphan, fresh_orphan = root / str(uuid4()), root / str(uuid4())
    staging, unrelated = root / ".upload-crashed", root / "unrelated"
    for directory in (old_orphan, fresh_orphan, staging, unrelated):
        directory.mkdir()
    old = time.time() - 601
    for directory in (root / stored["id"], old_orphan, unrelated):
        os.utime(directory, (old, old))

    restarted = create_app(settings=app.state.settings, start_background_workers=False)
    with caplog.at_level("INFO", logger=mobile_inbox.__name__):
        async with restarted.router.lifespan_context(restarted):
            assert {entry.name for entry in root.iterdir()} == {stored["id"], fresh_orphan.name, unrelated.name}
    assert "Mobile inbox startup cleanup: removed 2 directories" in caplog.messages


async def test_delete_and_purge_commit_database_before_file_cleanup(signed_in, monkeypatch, caplog):
    app, client = signed_in
    inbox = app.state.mobile_inbox
    deleted = _item(await _upload(client, images=(PNG,)))
    purged = _item(await _upload(client, images=(JPEG,)))
    await inbox.db.execute("UPDATE mobile_inbox_items SET synced_at = 1 WHERE id = ?", (purged["id"],))
    seen = {}

    def fail_removal(item_id):
        with closing(sqlite3.connect(app.state.settings.sqlite_path)) as db:
            seen[str(item_id)] = db.execute(
                "SELECT files_purged_at FROM mobile_inbox_items WHERE id = ?", (str(item_id),),
            ).fetchone()
        raise PermissionError("test failure")

    monkeypatch.setattr(inbox, "_remove_files", fail_removal)
    assert _item(await client.delete(f"{INBOX}/{deleted['id']}")) == {"ok": True}
    assert await inbox.purge(30) == 1
    assert seen[deleted["id"]] is None
    assert seen[purged["id"]][0] > 0
    assert (inbox.root / deleted["id"]).exists() and (inbox.root / purged["id"]).exists()
    assert (await client.get(f"{INBOX}/{purged['id']}/images/1")).status_code == 410
    assert sum("Could not remove mobile inbox files" in message for message in caplog.messages) == 2


async def test_disappearing_image_is_gone_before_response_starts(signed_in, monkeypatch):
    app, client = signed_in
    inbox = app.state.mobile_inbox
    item = _item(await _upload(client, images=(PNG,)))
    image = inbox.image

    async def resolve_then_remove(item_id, number):
        path, media_type = await image(item_id, number)
        path.unlink()
        return path, media_type

    monkeypatch.setattr(inbox, "image", resolve_then_remove)
    response = await client.get(f"{INBOX}/{item['id']}/images/1")
    assert response.status_code == 410
    assert response.json() == {"detail": "收件箱图片已清理"}


def test_nginx_inbox_only_allows_large_unbuffered_requests():
    config = (Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    location = re.search(r"location = /api/mobile/inbox\s*\{([^}]+)\}", config)
    assert location is not None and location.start() < config.index("location /api/")
    for directive in ("client_max_body_size 150m;", "proxy_request_buffering off;", "proxy_read_timeout 300s;"):
        assert directive in location[1]
        assert config.count(directive) == 1


async def _cli(app, *args):
    settings = app.state.settings
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.tools.mobile_inbox", *args,
        env={**os.environ, "DATABASE_URL": settings.database_url,
             "MOBILE_INBOX_DIR": settings.mobile_inbox_dir},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await process.communicate()
    assert len(err.splitlines()) == 1, err
    return process.returncode, out, json.loads(err)


async def test_cli_export_status_and_purge_lifecycle(signed_in, monkeypatch):
    app, client = signed_in
    monkeypatch.setattr(mobile_inbox, "time_ns", lambda: 1_796_000_000_000_000_000)
    old = _item(await _upload(client, text=" 原文\n第二行 ", note="保留备注", images=(PNG,), source_app="app.share"))
    new = _item(await _upload(client, text="new sync", images=(JPEG,)))
    pending = _item(await _upload(client, text="unsynced", images=(WEBP,)))
    ids = old["id"], new["id"], pending["id"]

    code, out, err = await _cli(app, "export", "--unsynced")
    assert (code, err) == (0, {"ok": True, "exported": 3, "skipped_missing": 0})
    with tarfile.open(fileobj=BytesIO(out), mode="r:gz") as bundle:
        assert bundle.getnames()[0] == "manifest.json"
        assert json.load(bundle.extractfile("manifest.json")) == list(ids)
        folders = [f"20261130-0853-{item_id[:8]}" for item_id in ids]
        assert set(bundle.getnames()) == {"manifest.json", *folders,
            *(f"{folder}/note.md" for folder in folders),
            *(f"{folder}/1.{ext}" for folder, ext in zip(folders, ("png", "jpg", "webp")))}
        assert bundle.getmember(folders[0]).isdir()
        assert bundle.extractfile(f"{folders[0]}/1.png").read() == PNG
        note = bundle.extractfile(f"{folders[0]}/note.md").read().decode("utf-8")
        for expected in ("时间（北京时间）：2026-11-30 08:53:20+08:00", "来源 App：app.share",
                         "保留备注", " 原文\n第二行 ", ids[0]):
            assert expected in note
    assert all(item["synced_at"] is None for item in _item(await client.get(INBOX)))

    assert await _cli(app, "mark-synced", *ids[:2]) == (0, b"", {"ok": True, "marked": 2})
    assert await _cli(app, "set-status", ids[0], "researched", "--result", "已核实") == (
        0, b"", {"ok": True, "id": ids[0], "status": "researched"},
    )
    code, out, err = await _cli(app, "export", "--unsynced")
    assert (code, err) == (0, {"ok": True, "exported": 1, "skipped_missing": 0})
    with tarfile.open(fileobj=BytesIO(out), mode="r:gz") as bundle:
        assert json.load(bundle.extractfile("manifest.json")) == [ids[2]]

    synced = _item(await client.get(INBOX))
    assert synced[0]["synced_at"] is None and synced[1]["synced_at"] is not None
    assert synced[2]["status"] == "researched" and synced[2]["result"] == "已核实"
    await app.state.mobile_inbox.db.execute(
        "UPDATE mobile_inbox_items SET synced_at = synced_at - ? WHERE id = ?",
        (31 * 86400 * 1_000_000, ids[0]),
    )
    assert await _cli(app, "purge", "--days", "30") == (0, b"", {"ok": True, "purged": 1})
    assert await _cli(app, "purge") == (0, b"", {"ok": True, "purged": 0})
    assert not (app.state.mobile_inbox.root / ids[0]).exists()
    for item_id, status in zip(ids, (410, 200, 200)):
        assert (await client.get(f"{INBOX}/{item_id}/images/1")).status_code == status
    purged = _item(await client.get(INBOX))[-1]
    assert purged["id"] == ids[0] and purged["image_count"] == 1 and purged["result"] == "已核实"
    assert "files_purged_at" not in purged


async def test_cli_export_manifest_first_and_skips_incomplete_items(signed_in):
    app, client = signed_in
    missing = _item(await _upload(client, images=(PNG, JPEG)))
    kept = _item(await _upload(client, images=(WEBP,)))
    (app.state.mobile_inbox.root / missing["id"] / "2.jpg").unlink()
    code, out, err = await _cli(app, "export", "--unsynced")
    assert (code, err) == (0, {"ok": True, "exported": 1, "skipped_missing": 1})
    with tarfile.open(fileobj=BytesIO(out), mode="r:gz") as bundle:
        names = bundle.getnames()
        assert names[0] == "manifest.json"
        assert json.load(bundle.extractfile("manifest.json")) == [kept["id"]]
        assert len(names) == 4 and all(kept["id"][:8] in name for name in names[1:])


@pytest.mark.parametrize("days", ["0", "-1", "3651", "999999999"])
async def test_cli_rejects_purge_days_out_of_bounds(signed_in, days):
    app, _ = signed_in
    assert await _cli(app, "purge", "--days", days) == (
        2, b"", {"ok": False, "error": "清理天数必须在 1 至 3650 之间"},
    )


async def test_cli_accepts_purge_boundaries_and_keeps_result_limit(signed_in):
    app, client = signed_in
    for days in ("1", "3650"):
        assert await _cli(app, "purge", "--days", days) == (0, b"", {"ok": True, "purged": 0})
    item_id = _item(await _upload(client, text="research"))["id"]
    assert await _cli(app, "set-status", item_id, "researched", "--result", "结" * 2000) == (
        0, b"", {"ok": True, "id": item_id, "status": "researched"},
    )
    code, out, err = await _cli(app, "set-status", item_id, "researched", "--result", "结" * 2001)
    assert (code, out, err["ok"]) == (2, b"", False)
