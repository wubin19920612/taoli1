"""Publish and open a consistent manifest and APK from the mobile release directory."""

import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from errno import ELOOP
from hashlib import sha256
from pathlib import Path
from stat import FILE_ATTRIBUTE_REPARSE_POINT, S_ISDIR, S_ISREG
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile

from app.models.mobile import MAX_MOBILE_APK_SIZE, MobileAppManifest

logger = logging.getLogger(__name__)
LATEST = "latest.json"
STAGED_MANIFEST = "latest.json.tmp"
PREVIOUS_APK = ".previous.apk"
APK_NAME = re.compile(r"astro-mobile-([1-9][0-9]*)\.apk")
CHUNK_SIZE = 256 * 1024
MAX_MANIFEST_SIZE = 64 * 1024
UNSAFE_FILE = "手机更新文件必须是普通文件，且不能使用符号链接或越出发布目录"


class MobileAppPublishError(ValueError):
    """An operator-correctable error with a safe command-line message."""


def _is_reparse_point(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _release_directory(directory: Path) -> Path:
    info = directory.lstat()
    if not S_ISDIR(info.st_mode) or _is_reparse_point(info):
        raise MobileAppPublishError("手机更新发布目录必须是实际目录，不能使用符号链接")
    return directory.resolve(strict=True)


def _file_info(path: Path, directory: Path | None) -> os.stat_result:
    info = path.lstat()
    if not S_ISREG(info.st_mode) or _is_reparse_point(info):
        raise MobileAppPublishError(UNSAFE_FILE)
    if directory is not None and not path.resolve(strict=True).is_relative_to(directory):
        raise MobileAppPublishError(UNSAFE_FILE)
    return info


def _optional_file_info(path: Path, directory: Path | None) -> os.stat_result | None:
    try:
        return _file_info(path, directory)
    except FileNotFoundError:
        return None


def _no_follow_open(path: str, flags: int) -> int:
    # A substituted FIFO must not block the worker before the descriptor is checked.
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        return os.open(path, flags)
    except OSError as exc:
        if exc.errno == ELOOP:
            raise MobileAppPublishError(UNSAFE_FILE) from None
        raise


def _open_regular_file(path: Path, directory: Path | None = None, *, create_lock: bool = False) -> BinaryIO:
    if create_lock:
        _optional_file_info(path, directory)
    else:
        _file_info(path, directory)
    stream = open(path, "a+b" if create_lock else "rb", opener=_no_follow_open)
    try:
        if not os.path.samestat(os.fstat(stream.fileno()), _file_info(path, directory)):
            raise MobileAppPublishError(UNSAFE_FILE)
    except BaseException:
        stream.close()
        raise
    return stream


@contextmanager
def _release_lock(directory: Path, *, writing: bool = False) -> Iterator[None]:
    # The HTTP server and the standalone publisher run in separate processes.
    with _open_regular_file(directory / ".release.lock", directory, create_lock=True) as lock:
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
        with _open_regular_file(directory / LATEST, directory) as manifest:
            if os.fstat(manifest.fileno()).st_size <= MAX_MANIFEST_SIZE:
                data = manifest.read(MAX_MANIFEST_SIZE + 1)
                if len(data) <= MAX_MANIFEST_SIZE:
                    return MobileAppManifest.model_validate_json(data)
    except FileNotFoundError:
        return None
    logger.warning("手机更新清单 latest.json 超过 64 KiB")
    raise MobileAppPublishError("手机更新清单超过 64 KiB")


def open_release(directory: Path) -> tuple[MobileAppManifest, BinaryIO] | None:
    try:
        directory = _release_directory(directory)
        with _release_lock(directory):
            manifest = _read_manifest(directory)
            if manifest is None:
                return None
            path = directory / manifest.file
            backup = directory / PREVIOUS_APK
            # During an interrupted same-code publish, the old manifest is still live.
            if (_optional_file_info(directory / STAGED_MANIFEST, directory) is not None
                    and _optional_file_info(backup, directory) is not None):
                _optional_file_info(path, directory)
                path = backup
            apk = _open_regular_file(path, directory)
    except (FileNotFoundError, MobileAppPublishError):
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
        apk = _open_regular_file(source)
    except FileNotFoundError:
        raise MobileAppPublishError("APK 文件不存在或不是普通文件") from None
    with apk:
        info = os.fstat(apk.fileno())
        if info.st_size > MAX_MOBILE_APK_SIZE:
            raise MobileAppPublishError("APK 文件超过 60 MB")
        header = apk.read(4)
        if header != b"PK\x03\x04":
            raise MobileAppPublishError("APK 文件缺少 ZIP 文件头")
        size = len(header)
        with destination.open("x+b") as staged:
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
                with ZipFile(staged) as archive:
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
    backup_info = _optional_file_info(backup, directory)
    if backup_info is None:
        return
    if current is not None and _optional_file_info(directory / STAGED_MANIFEST, directory) is not None:
        previous_path = directory / current.file
        if backup_info.st_size == 0:
            # The empty marker represents a release without a usable APK.
            previous_path.unlink(missing_ok=True)
        else:
            backup.replace(previous_path)
    # On POSIX, rename is a no-op when both names already refer to the same inode.
    backup.unlink(missing_ok=True)


def _prune(directory: Path, current_code: int, *, former_code: int | None = None) -> None:
    try:
        packages = []
        for path in directory.glob("astro-mobile-*.apk", case_sensitive=True):
            info = path.lstat()
            if S_ISDIR(info.st_mode):
                continue
            match = APK_NAME.fullmatch(path.name)
            code = int(match[1]) if match and S_ISREG(info.st_mode) and not _is_reparse_point(info) else 0
            packages.append((code, path))
        codes = [code for code, _ in packages if code]
        if former_code is None:
            # At startup, keep the live APK even if interrupted releases have higher codes.
            keep = set(sorted(codes, reverse=True)[:2])
        else:
            # After committing, keep the newest predecessor at or below the former live code.
            previous_code = max((code for code in codes if code < current_code and code <= former_code), default=0)
            keep = {previous_code} if previous_code else set()
        if current_code:
            keep.add(current_code)
        for code, path in packages:
            if code not in keep:
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

    try:
        directory = _release_directory(directory)
    except FileNotFoundError:
        directory.mkdir(parents=True, exist_ok=True)
        directory = _release_directory(directory)
    with _release_lock(directory, writing=True):
        current = _read_manifest(directory)
        former_code = current.version_code if current else 0
        filename = f"astro-mobile-{version_code}.apk"
        apk = directory / filename
        staged_apk = apk.with_suffix(".apk.tmp")
        staged_manifest = directory / STAGED_MANIFEST
        backup = directory / PREVIOUS_APK
        for path in (apk, staged_apk, staged_manifest):
            _optional_file_info(path, directory)
        if current is not None and current.file != filename:
            _optional_file_info(directory / current.file, directory)
        resolved_source = source.resolve()
        if resolved_source.parent == directory and (
            resolved_source.match("*.apk.tmp") or resolved_source.name in {STAGED_MANIFEST, PREVIOUS_APK}
        ):
            raise MobileAppPublishError("输入 APK 不能使用发布暂存文件")

        _recover(directory, current)
        for path in directory.glob("*.apk.tmp"):
            _discard(path)
        _discard(staged_manifest)
        _prune(directory, former_code)

        overwriting = version_code == former_code
        if version_code < former_code or (overwriting and not force):
            raise MobileAppPublishError("version_code 必须递增，同号重发须指定 --force")
        apk_replaced = False
        try:
            size, digest = _stage_apk(source, staged_apk)
            manifest = MobileAppManifest(
                version_code=version_code, version_name=version_name, size=size, sha256=digest,
                notes=notes, published_at=datetime.now(UTC), file=filename,
            )
            with staged_manifest.open("xb") as staged:
                staged.write(manifest.model_dump_json().encode("utf-8"))
                staged.flush()
                os.fsync(staged.fileno())

            if overwriting:
                # A hard link preserves the old bytes across the APK rename without a second copy.
                try:
                    os.link(apk, backup, follow_symlinks=False)
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
        _prune(directory, version_code, former_code=former_code)
        return manifest
