"""Publish an APK to the mobile update service and optionally notify devices."""

import argparse
import asyncio
import json
import logging
from collections.abc import Sequence
from pathlib import Path

import aiosqlite

from app.core.config import Settings, get_settings
from app.db.repositories import MobilePushRepository
from app.models.mobile import MobileAppManifest
from app.services.fcm import FcmClient
from app.services.mobile_app_release import MobileAppPublishError, publish_release
from app.services.mobile_push import MobilePushService

logger = logging.getLogger(__name__)


async def _push_update(settings: Settings, release: MobileAppManifest) -> int:
    fcm = FcmClient(settings.fcm_service_account_json_b64)
    try:
        if not fcm.enabled:
            logger.warning("跳过手机更新推送，FCM 未启用")
            return 0
        database = Path(settings.sqlite_path).resolve().as_uri() + "?mode=rw"
        async with aiosqlite.connect(database, uri=True) as db:
            db.row_factory = aiosqlite.Row
            push = MobilePushService(MobilePushRepository(db), fcm, load_summary=None)
            return await push.send_app_update(release.version_name, release.notes)
    finally:
        await fcm.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--version-code", type=int, required=True)
    parser.add_argument("--version-name", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--force", action="store_true", help="允许同版本号重发")
    parser.add_argument("--push", action="store_true", help="向开启更新提醒的手机发送通知")
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
        release = publish_release(
            Path(settings.mobile_app_dir), args.apk, version_code=args.version_code,
            version_name=args.version_name, notes=args.notes, force=args.force,
        )
    except MobileAppPublishError as exc:
        logger.error("%s", exc)
        return 1
    except Exception:  # noqa: BLE001 - keep local paths and settings out of diagnostics.
        logger.error("手机更新发布失败，无法读取或写入发布文件")
        return 1

    pushed = 0
    if args.push:
        try:
            pushed = asyncio.run(_push_update(settings, release))
        except Exception:  # noqa: BLE001 - a notification failure cannot undo a release.
            logger.warning("手机更新已发布，发送推送失败")
    print(json.dumps({
        "version": release.version_name,
        "sha256": release.sha256,
        "size": release.size,
        "pushed": pushed,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
