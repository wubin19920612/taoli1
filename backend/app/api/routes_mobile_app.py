import logging
from pathlib import Path
from re import fullmatch
from time import monotonic
from typing import BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from app.core.security import require_session
from app.models.mobile import MobileAppManifest, MobileAppRelease
from app.services.mobile_app_release import CHUNK_SIZE, open_release

router = APIRouter(prefix="/app", dependencies=[Depends(require_session)])
logger = logging.getLogger(__name__)
MEDIA_TYPE = "application/vnd.android.package-archive"
START_INTERVAL = 30


class _DownloadLimits:
    def __init__(self):
        self.active: set[str] = set()
        self.started: dict[str, float] = {}
        self.cleanup_at = 0.0

    def check(self, session: str) -> float:
        now = monotonic()
        cutoff = now - START_INTERVAL
        if now >= self.cleanup_at:
            self.started = {key: start for key, start in self.started.items() if start > cutoff}
            self.cleanup_at = now + START_INTERVAL
        if session in self.active or self.started.get(session, cutoff) > cutoff:
            raise HTTPException(429, "下载太频繁，请稍后再试")
        return now

    def start(self, session: str) -> None:
        self.started[session] = self.check(session)
        self.active.add(session)


def _available_release(directory: str) -> tuple[MobileAppManifest, BinaryIO]:
    try:
        release = open_release(Path(directory))
    except Exception:  # noqa: BLE001 - never expose filesystem or manifest details.
        logger.warning("无法读取手机更新版本")
        raise HTTPException(503, "更新服务暂不可用") from None
    if release is None:
        raise HTTPException(404, "暂无可用更新")
    return release


def _apk_headers(manifest: MobileAppManifest) -> dict[str, str]:
    filename = f"astro-mobile-{manifest.version_name}.apk"
    if fullmatch(r"[A-Za-z0-9._-]+", filename):
        disposition = f"attachment; filename={filename}"
    else:
        disposition = f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
    return {
        "Cache-Control": "no-store",
        "Content-Length": str(manifest.size),
        "ETag": f'"{manifest.sha256}"',
        "Content-Disposition": disposition,
    }


def _apk_chunks(apk: BinaryIO):
    try:
        while chunk := apk.read(CHUNK_SIZE):
            yield chunk
    except OSError:
        # Headers are already sent. Abort the stream so a partial APK is not treated as complete.
        raise RuntimeError("更新服务暂不可用") from None


class _ApkDownload(Response):
    def __init__(self, directory: str, limits: _DownloadLimits, session: str):
        super().__init__()
        self.directory = directory
        self.limits = limits
        self.session = session

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.limits.check(self.session)
        manifest, apk = await run_in_threadpool(_available_release, self.directory)
        with apk:
            # Admission is checked again because another request may have opened the same APK.
            self.limits.start(self.session)
            try:
                response = StreamingResponse(
                    _apk_chunks(apk), media_type=MEDIA_TYPE, headers=_apk_headers(manifest),
                )
                await response(scope, receive, send)
            finally:
                self.limits.active.remove(self.session)


@router.get("/latest", response_model=MobileAppRelease)
def latest_app(request: Request, response: Response) -> MobileAppRelease:
    manifest, apk = _available_release(request.app.state.settings.mobile_app_dir)
    apk.close()
    response.headers["Cache-Control"] = "no-store"
    return manifest


@router.get("/apk", response_class=StreamingResponse, responses={200: {"content": {MEDIA_TYPE: {}}}})
async def download_apk(request: Request) -> Response:
    state = request.app.state
    if not hasattr(state, "mobile_app_download_limits"):
        state.mobile_app_download_limits = _DownloadLimits()
    return _ApkDownload(
        state.settings.mobile_app_dir, state.mobile_app_download_limits,
        request.state.auth_session_hash,
    )
