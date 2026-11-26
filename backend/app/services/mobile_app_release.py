"""Publish and open a consistent manifest and APK from the mobile release directory."""

import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from stat import S_ISREG
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile

from app.models.mobile import MAX_MOBILE_APK_SIZE, MobileAppManifest

logger = logging.getLogger(__name__)
LATEST = "latest.json"
STAGED_MANIFEST = "latest.json.tmp"
PREVIOUS_APK = ".previous.apk"
APK_NAME = re.compile(r"astro-mobile-([1-9][0-9]*)\.apk")
CHUNK_SIZE = 256 * 1024


class MobileAppPublishError(ValueError):
    """An operator-correctable error with a safe command-line message."""


@contextmanager
def _release_lock(directory: Path, *, writing: bool = False) -> Iterator[None]:
    # The HTTP server and the standalone publisher run in separate processes.
    with (directory / ".release.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX if writing else fcntl.LOCK_SH)
        yield


def _read_manifest(directory: Path) -> MobileAppManifest | None:
    try:
        data = (directory / LATEST).read_bytes()
    except FileNotFoundError:
        return None
    return MobileAppManifest.model_validate_json(data)


def open_release(directory: Path) -> tuple[MobileAppManifest, BinaryIO] | None:
    try:
        with _release_lock(directory):
            manifest = _read_manifest(directory)
            if manifest is None:
                return None
            path = directory / manifest.file
            backup = directory / PREVIOUS_APK
            # During an interrupted same-code publish, the old manifest is still live.
            if (directory / STAGED_MANIFEST).exists() and backup.exists():
                path = backup
            if not S_ISREG(path.stat().st_mode):
                return None
            apk = path.open("rb")
    except FileNotFoundError:
        return None

    try:
        if os.fstat(apk.fileno()).st_size == manifest.size:
            return manifest, apk
    except BaseException:
        apk.close()
        raise
    apk.close()
    return None


def _stage_apk(source: Path, destination: Path) -> tuple[int, str]:
    digest = sha256()
    size = 0
    try:
        if not S_ISREG(source.stat().st_mode):
            raise MobileAppPublishError("APK 文件必须是普通文件")
        apk = source.open("rb")
    except (FileNotFoundError, IsADirectoryError):
        raise MobileAppPublishError("APK 文件不存在或不是普通文件") from None
    with apk:
        info = os.fstat(apk.fileno())
        if not S_ISREG(info.st_mode):
            raise MobileAppPublishError("APK 文件必须是普通文件")
        if info.st_size > MAX_MOBILE_APK_SIZE:
            raise MobileAppPublishError("APK 文件超过 60 MB")
        header = apk.read(4)
        if header != b"PK\x03\x04":
            raise MobileAppPublishError("APK 文件缺少 ZIP 文件头")
        size = len(header)
        with destination.open("wb") as staged:
            staged.write(header)
            digest.update(header)
            while chunk := apk.read(CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_MOBILE_APK_SIZE:
                    raise MobileAppPublishError("APK 文件超过 60 MB")
                staged.write(chunk)
                digest.update(chunk)
            staged.flush()
            os.fsync(staged.fileno())

    try:
        with ZipFile(destination) as archive:
            archive.getinfo("AndroidManifest.xml")
            archive.getinfo("classes.dex")
    except (BadZipFile, KeyError, ValueError):
        raise MobileAppPublishError("APK 压缩包无效或缺少 AndroidManifest.xml / classes.dex") from None
    return size, digest.hexdigest()


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("未能清理手机更新发布文件")


def _recover(directory: Path, current: MobileAppManifest | None) -> None:
    backup = directory / PREVIOUS_APK
    if not backup.exists():
        return
    if current is not None and (directory / STAGED_MANIFEST).exists():
        previous_path = directory / current.file
        if backup.stat().st_size == 0:
            # The empty marker represents a release without a usable APK.
            previous_path.unlink(missing_ok=True)
        else:
            backup.replace(previous_path)
    # On POSIX, rename is a no-op when both names already refer to the same inode.
    backup.unlink(missing_ok=True)


def _prune(directory: Path, current_code: int, former_code: int) -> None:
    try:
        packages = []
        for path in directory.iterdir():
            match = APK_NAME.fullmatch(path.name)
            if match and path.is_file():
                packages.append((int(match[1]), path))
        keep_previous = max(
            (code for code, _ in packages if code < current_code and code <= former_code),
            default=0,
        )
        for code, path in packages:
            if code not in (current_code, keep_previous):
                _discard(path)
    except OSError:
        logger.warning("未能清理旧的手机更新版本")


def publish_release(
    directory: Path,
    source: Path,
    *,
    version_code: int,
    version_name: str,
    notes: str = "",
    force: bool = False,
) -> MobileAppManifest:
    if version_code <= 0 or not 1 <= len(version_name) <= 32 or len(notes) > 2000:
        raise MobileAppPublishError("版本号、版本名或更新说明无效")

    directory.mkdir(parents=True, exist_ok=True)
    with _release_lock(directory, writing=True):
        current = _read_manifest(directory)
        _recover(directory, current)
        former_code = current.version_code if current else 0
        overwriting = version_code == former_code
        if version_code < former_code or (overwriting and not force):
            raise MobileAppPublishError("version_code 必须递增，同号重发须指定 --force")

        filename = f"astro-mobile-{version_code}.apk"
        apk = directory / filename
        staged_apk = apk.with_suffix(".apk.tmp")
        staged_manifest = directory / STAGED_MANIFEST
        backup = directory / PREVIOUS_APK
        if source.resolve() in {staged_apk.resolve(), staged_manifest.resolve(), backup.resolve()}:
            raise MobileAppPublishError("输入 APK 不能使用发布暂存文件")
        apk_replaced = False
        try:
            size, digest = _stage_apk(source, staged_apk)
            manifest = MobileAppManifest(
                version_code=version_code, version_name=version_name, size=size, sha256=digest,
                notes=notes, published_at=datetime.now(UTC), file=filename,
            )
            with staged_manifest.open("wb") as staged:
                staged.write(manifest.model_dump_json().encode("utf-8"))
                staged.flush()
                os.fsync(staged.fileno())

            if overwriting:
                # A hard link preserves the old bytes across the APK rename without a second copy.
                try:
                    os.link(apk, backup)
                except FileNotFoundError:
                    backup.touch(exist_ok=False)
            staged_apk.replace(apk)
            apk_replaced = True
            staged_manifest.replace(directory / LATEST)
        except OSError:
            _recover(directory, current)
            if apk_replaced and not overwriting:
                _discard(apk)
            raise
        finally:
            _discard(staged_apk)
            if not backup.exists():
                _discard(staged_manifest)

        _discard(backup)
        _prune(directory, version_code, former_code)
        return manifest
