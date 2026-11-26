import asyncio
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zipfile import ZipFile

import httpx
import pytest
from auth_helpers import TEST_TOTP_SECRET
from pydantic import ValidationError

from app.api import routes_mobile_app
from app.core.config import Settings
from app.db.auth import AuthStore, session_hash
from app.db.repositories import MobilePushRepository
from app.main import create_app
from app.models.mobile import MAX_MOBILE_APK_SIZE, MobileAppManifest, PushKinds
from app.services.fcm import SendResult
from app.services.mobile_app_release import MobileAppPublishError, open_release, publish_release
from app.tools import publish_mobile_app

ROOT = "/api/mobile/app"
TOKEN = "a" * 64
OTHER_TOKEN = "b" * 64
KINDS = dict.fromkeys(("near_open", "near_close", "fill", "leg_alert", "big_spread", "stale"), False)


def make_apk(path, contents=b"apk", *, entries=("AndroidManifest.xml", "classes.dex")):
    with ZipFile(path, "w") as apk:
        for entry in entries:
            apk.writestr(entry, contents)
    return path


def release(directory, source, code=18, **kwargs):
    return publish_release(directory, source, version_code=code, version_name=f"0.{code}.0", **kwargs)


async def add_session(store, token, *, expires_in=3600):
    now = time.time()
    await store.db.execute(
        "INSERT INTO auth_sessions "
        "(id_hash, created_at, expires_at, last_seen_at, ip, user_agent, scope) "
        "VALUES (?, ?, ?, ?, '127.0.0.1', 'Test', 'mobile_readonly')",
        (session_hash(token), now, now + expires_in, now),
    )
    await store.db.commit()


@pytest.fixture
async def update_api(tmp_path):
    directory = tmp_path / "releases"
    app = create_app(settings=Settings(
        environment="production", dashboard_password="fixture",
        dashboard_totp_secret=TEST_TOTP_SECRET, dashboard_api_token="c" * 64,
        mobile_app_dir=str(directory),
    ), start_background_workers=False)
    store = await AuthStore.open(":memory:")
    app.state.auth_store = store
    try:
        await add_session(store, TOKEN)
        await add_session(store, OTHER_TOKEN)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test",
        ) as client:
            yield app, client, directory
    finally:
        await store.close()


def cookie(token=TOKEN):
    return {"cookie": "taoli1_session=" + token}


async def test_mobile_update_session_manifest_and_download(update_api, tmp_path):
    _, client, directory = update_api
    source = make_apk(tmp_path / "input.apk", b"x" * (300 * 1024))
    manifest = release(directory, source, notes="更新说明")
    for endpoint in ("/latest", "/apk"):
        assert (await client.get(ROOT + endpoint)).status_code == 401
        assert (await client.get(ROOT + endpoint, headers={"x-api-token": "c" * 64})).status_code == 401

    latest = await client.get(ROOT + "/latest", headers=cookie())
    assert latest.status_code == 200
    assert latest.json() == manifest.model_dump(mode="json", exclude={"file"})
    assert latest.json()["published_at"].endswith("Z")
    assert latest.headers["cache-control"] == "no-store"
    apk = await client.get(ROOT + "/apk", headers=cookie())
    assert apk.status_code == 200
    assert apk.content == source.read_bytes()
    assert apk.headers["content-type"] == "application/vnd.android.package-archive"
    assert apk.headers["content-length"] == str(len(apk.content))
    assert apk.headers["etag"] == f'"{sha256(apk.content).hexdigest()}"'
    assert apk.headers["content-disposition"] == "attachment; filename=astro-mobile-0.18.0.apk"


@pytest.mark.parametrize("state", ["no-manifest", "no-apk", "wrong-size"])
async def test_mobile_update_missing_release_returns_404(update_api, tmp_path, state):
    _, client, directory = update_api
    if state != "no-manifest":
        manifest = release(directory, make_apk(tmp_path / "input.apk"))
        apk = directory / manifest.file
        if state == "no-apk":
            apk.unlink()
        else:
            apk.write_bytes(b"truncated")
    for endpoint in ("/latest", "/apk"):
        response = await client.get(ROOT + endpoint, headers=cookie())
        assert response.status_code == 404
        assert response.json() == {"detail": "暂无可用更新"}


@pytest.mark.parametrize("broken", ["malformed", "io-error"])
async def test_mobile_update_errors_are_redacted(update_api, monkeypatch, broken):
    _, client, directory = update_api
    directory.mkdir()
    if broken == "malformed":
        (directory / "latest.json").write_text("private content")
    else:
        def fail(_):
            raise PermissionError("private path", str(directory))

        monkeypatch.setattr(routes_mobile_app, "open_release", fail)
    for endpoint in ("/latest", "/apk"):
        response = await client.get(ROOT + endpoint, headers=cookie())
        assert response.status_code == 503
        assert response.json() == {"detail": "更新服务暂不可用"}


@pytest.mark.parametrize("field,value", [
    ("version_code", 0), ("version_code", "18"), ("version_name", "x" * 33),
    ("size", -1), ("sha256", "bad"), ("notes", "x" * 2001),
    ("published_at", "2026-11-26T12:00:00+09:00"), ("published_at", "2026-11-26T12:00:00"),
    ("published_at", 1800000000), ("file", "../astro-mobile-18.apk"),
])
def test_mobile_update_manifest_rejects_invalid_fields(tmp_path, field, value):
    valid = release(tmp_path / "store", make_apk(tmp_path / "input.apk"))
    data = valid.model_dump(mode="json") | {field: value}
    with pytest.raises(ValidationError):
        MobileAppManifest.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("end", ["complete", "disconnect", "send-error"])
async def test_mobile_update_limits_follow_asgi_response_lifetime(update_api, tmp_path, monkeypatch, end):
    app, client, directory = update_api
    source = make_apk(tmp_path / "input.apk", b"x" * (300 * 1024))
    release(directory, source)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(routes_mobile_app, "monotonic", lambda: clock.now)
    started = asyncio.Event()
    finish = asyncio.Event()
    disconnect = asyncio.Event()
    events = []
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4" if end == "send-error" else "2.3"},
        "http_version": "1.1", "scheme": "https", "method": "GET", "path": ROOT + "/apk",
        "query_string": b"", "headers": [(b"cookie", cookie()["cookie"].encode())],
        "server": ("test", 443), "client": ("127.0.0.1", 1234),
    }

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        events.append(message)
        if message["type"] == "http.response.body" and not started.is_set():
            started.set()
            await finish.wait()
            if end == "send-error":
                raise OSError("client disconnected")

    stream = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        assert events[0]["status"] == 200
        immediate = await client.get(ROOT + "/apk", headers=cookie())
        assert immediate.status_code == 429
        assert immediate.json() == {"detail": "下载太频繁，请稍后再试"}
        assert (await client.get(ROOT + "/apk", headers=cookie(OTHER_TOKEN))).status_code == 200
        clock.now = 130
        assert (await client.get(ROOT + "/apk", headers=cookie())).status_code == 429
        if end == "disconnect":
            disconnect.set()
        else:
            finish.set()
        if end == "send-error":
            from starlette.requests import ClientDisconnect

            with pytest.raises(ClientDisconnect):
                await asyncio.wait_for(stream, timeout=5)
        else:
            await asyncio.wait_for(stream, timeout=5)
        if end == "complete":
            assert b"".join(event.get("body", b"") for event in events) == source.read_bytes()
            assert events[-1]["more_body"] is False
        assert (await client.get(ROOT + "/apk", headers=cookie())).status_code == 200
        clock.now += 29.99
        assert (await client.get(ROOT + "/apk", headers=cookie())).status_code == 429
        clock.now = 160
        assert (await client.get(ROOT + "/apk", headers=cookie())).status_code == 200
    finally:
        stream.cancel()
        await asyncio.gather(stream, return_exceptions=True)


def cli_args(source, code=18, *options):
    return ["--apk", str(source), "--version-code", str(code), "--version-name", f"0.{code}.0", *options]


@pytest.mark.parametrize("state", ["missing", "oversize", "magic", "bad-zip", "manifest", "dex", "code",
                                   "version-name", "notes"])
def test_mobile_update_cli_rejects_invalid_inputs(tmp_path, monkeypatch, capsys, state):
    directory = tmp_path / "releases"
    monkeypatch.setattr(publish_mobile_app, "get_settings", lambda: Settings(mobile_app_dir=str(directory)))
    source = tmp_path / "input.apk"
    if state == "oversize":
        with source.open("wb") as apk:
            apk.truncate(MAX_MOBILE_APK_SIZE + 1)
    elif state == "magic":
        source.write_bytes(b"not an APK")
    elif state == "bad-zip":
        source.write_bytes(b"PK\x03\x04invalid archive")
    elif state == "manifest":
        make_apk(source, entries=("classes.dex",))
    elif state == "dex":
        make_apk(source, entries=("AndroidManifest.xml",))
    elif state != "missing":
        make_apk(source)
    args = cli_args(source, 0 if state == "code" else 18)
    if state == "version-name":
        args[-1] = "x" * 33
    if state == "notes":
        args.extend(("--notes", "x" * 2001))
    assert publish_mobile_app.main(args) == 1
    assert not capsys.readouterr().out
    assert not (directory / "latest.json").exists()
    assert not list(directory.glob("*.apk*"))


def test_mobile_update_publish_keeps_two_versions_and_rejects_downgrade(tmp_path):
    directory = tmp_path / "releases"
    source = make_apk(tmp_path / "input.apk")
    unrelated = tmp_path / "releases" / "unrelated.apk"
    first = release(directory, source, 18)
    unrelated.write_bytes(b"keep")
    second = release(directory, source, 20)
    with pytest.raises(MobileAppPublishError, match="必须递增"):
        release(directory, source, 20)
    with pytest.raises(MobileAppPublishError, match="必须递增"):
        release(directory, source, 19, force=True)

    source = make_apk(source, b"new")
    forced = release(directory, source, 20, force=True)
    assert forced.sha256 != second.sha256
    assert (directory / forced.file).read_bytes() == source.read_bytes()
    third = release(directory, source, 21)
    assert not (directory / first.file).exists()
    assert {path.name for path in directory.glob("*.apk")} == {forced.file, third.file, unrelated.name}
    assert unrelated.read_bytes() == b"keep"
    assert not list(directory.glob("*.tmp"))


@pytest.mark.parametrize("force,missing", [(False, False), (True, False), (True, True)])
def test_mobile_update_failed_manifest_rename_preserves_live_version(tmp_path, monkeypatch, force, missing):
    directory = tmp_path / "releases"
    source = make_apk(tmp_path / "input.apk")
    first = release(directory, source)
    old = source.read_bytes()
    if missing:
        (directory / first.file).unlink()
    make_apk(source, b"new")
    replace = Path.replace

    def fail_manifest(path, destination):
        if path.name == "latest.json.tmp":
            assert (directory / f"astro-mobile-{18 if force else 19}.apk").read_bytes() == source.read_bytes()
            assert json.loads((directory / "latest.json").read_bytes())["sha256"] == first.sha256
            raise OSError("manifest rename failed")
        return replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_manifest)
    with pytest.raises(OSError, match="manifest rename failed"):
        release(directory, source, 18 if force else 19, force=force)
    opened = open_release(directory)
    if missing:
        assert opened is None
        assert not (directory / first.file).exists()
    else:
        manifest, apk = opened
        with apk:
            assert manifest == first
            assert apk.read() == old
    assert not list(directory.glob("*.tmp"))


def test_mobile_update_recovers_interrupted_same_code_publish(tmp_path, monkeypatch):
    directory = tmp_path / "releases"
    source = make_apk(tmp_path / "input.apk")
    first = release(directory, source)
    old = source.read_bytes()
    make_apk(source, b"new")
    replace = Path.replace

    class Interrupted(BaseException):
        pass

    def interrupt(path, destination):
        if path.name == "latest.json.tmp":
            raise Interrupted
        return replace(path, destination)

    with monkeypatch.context() as context:
        context.setattr(Path, "replace", interrupt)
        with pytest.raises(Interrupted):
            release(directory, source, force=True)
    manifest, apk = open_release(directory)
    with apk:
        assert manifest == first and apk.read() == old
    release(directory, source, 19)
    assert (directory / first.file).read_bytes() == old
    assert not list(directory.glob("*.tmp"))


def test_mobile_update_concurrent_publish_rechecks_version_under_lock(tmp_path):
    directory = tmp_path / "releases"
    source = make_apk(tmp_path / "input.apk")
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(release, directory, source) for _ in range(2)]
        outcomes = [job.exception(timeout=15) for job in jobs]
    assert outcomes.count(None) == 1
    assert sum(isinstance(error, MobileAppPublishError) for error in outcomes) == 1


def test_mobile_update_module_outputs_json_and_reads_release_dir_env(tmp_path):
    directory = tmp_path / "releases"
    source = make_apk(tmp_path / "input.apk")
    result = subprocess.run(
        [sys.executable, "-m", "app.tools.publish_mobile_app", *cli_args(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=os.environ | {"MOBILE_APP_DIR": str(directory)},
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=20,
    )
    assert json.loads(result.stdout) == {
        "version": "0.18.0", "sha256": sha256(source.read_bytes()).hexdigest(),
        "size": source.stat().st_size, "pushed": 0,
    }
    assert len(result.stdout.splitlines()) == 1
    assert (directory / "latest.json").is_file()


def test_mobile_update_cli_push_failure_does_not_undo_release(tmp_path, monkeypatch, capsys, caplog):
    directory = tmp_path / "releases"
    source = make_apk(tmp_path / "input.apk")
    monkeypatch.setattr(publish_mobile_app, "get_settings", lambda: Settings(mobile_app_dir=str(directory)))
    push = AsyncMock(side_effect=RuntimeError("private FCM details"))
    monkeypatch.setattr(publish_mobile_app, "_push_update", push)
    assert publish_mobile_app.main(cli_args(source, 18, "--push", "--notes", "new")) == 0
    push.assert_awaited_once()
    assert push.await_args.args[1].notes == "new"
    assert json.loads(capsys.readouterr().out)["pushed"] == 0
    assert (directory / "latest.json").exists()
    assert "发送推送失败" in caplog.text and "private FCM details" not in caplog.text


async def test_mobile_update_standalone_push_respects_preferences_and_counts_success(tmp_path, monkeypatch, caplog):
    database = tmp_path / "radar.db"
    auth = await AuthStore.open(str(database))
    assert PushKinds(**KINDS).app_update is True
    assert PushKinds(**KINDS, app_update=False).app_update is False
    try:
        await add_session(auth, TOKEN)
        await add_session(auth, OTHER_TOKEN, expires_in=-1)
        repo = MobilePushRepository(auth.db)
        for device, kinds, token in (
            ("legacy", KINDS, TOKEN), ("enabled", {"app_update": True}, TOKEN),
            ("off", {"app_update": False}, TOKEN), ("failed", {"app_update": True}, TOKEN),
            ("expired", KINDS, OTHER_TOKEN),
        ):
            await repo.register(device, session_hash(token), kinds, 0.1)
    finally:
        await auth.close()

    async def send(token, message):
        return SendResult(token != "failed", "send status")

    fcm = SimpleNamespace(enabled=True, send=AsyncMock(side_effect=send), aclose=AsyncMock())
    monkeypatch.setattr(publish_mobile_app, "FcmClient", lambda _: fcm)
    manifest = release(tmp_path / "releases", make_apk(tmp_path / "input.apk"), notes="新" * 90)
    settings = Settings(database_url=f"sqlite:///{database}")
    assert await publish_mobile_app._push_update(settings, manifest) == 2
    assert {call.args[0] for call in fcm.send.await_args_list} == {"legacy", "enabled", "failed"}
    for call in fcm.send.await_args_list:
        _, message = call.args
        assert (message.kind, message.channel, message.title, message.body) == (
            "app_update", "normal", "Astro 有新版本 0.18.0", "新" * 80,
        )
    fcm.aclose.assert_awaited_once()
    assert "1 台设备发送失败" in caplog.text
