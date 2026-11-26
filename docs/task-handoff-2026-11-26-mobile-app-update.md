# P20 手机 App 自升级后端交接

分支为 `codex/mobile-p1`，基线为 `5302f94`。本次实现发布工具、手机会话下载接口和更新推送，验收范围见 [P20 任务书](mobile-p20-app-update-2026-11-26.md)。

## App 接口

| 方法与路径 | 接入约定 |
| --- | --- |
| `GET /api/mobile/app/latest` | 返回 `version_code`、`version_name`、`size`、`sha256`、`notes` 和 `published_at`。发布清单中的 `file` 仅供服务端定位 APK。 |
| `GET /api/mobile/app/apk` | 返回当前 APK，媒体类型为 `application/vnd.android.package-archive`，附带字节数、带引号的 SHA-256 ETag 和下载文件名。每会话最多一个在途下载，开始间隔至少 30 秒。下载完成或断开后释放在途名额。 |

- 两条路由使用现有 Cookie 会话校验，允许 `mobile_readonly`。没有可用版本时返回 404，达到下载限额时返回 429，清单损坏与文件读取错误在开始响应前统一返回 503。
- 后端按已发布清单查验文件大小。App 下载完成后应校验 SHA-256、包名、版本和签名。两次 GET 之间可能切换发布版本，收到的 ETag 或 APK 摘要与上次清单不符时，应重新查询版本。断点续传留待后续接入。
- 入口在 `backend/app/api/routes_mobile_app.py`，响应与清单模型在 `backend/app/models/mobile.py`。下载限额保存在当前 API 进程内，适用于现有单 worker 配置。

## 发布与推送

在后端运行环境用下列命令发布已构建的 APK。目录由 `MOBILE_APP_DIR` 配置，容器默认使用 `/data/mobile-app`。

```bash
python -m app.tools.publish_mobile_app --apk /path/to/app.apk --version-code 18 --version-name 0.18.0 --notes "更新说明" --push
```

- `backend/app/services/mobile_app_release.py` 校验 APK 的 ZIP 头、两个必备条目和 60 MiB 上限，计算 SHA-256 后发布，保留当前和上一版。允许用 `--force` 同号重发。发布与读取使用同目录锁协调，同号重发通过旧 APK 的硬链接保留恢复能力。普通 I/O 失败时清单维持上次发布状态，发布中断后可在下一次发布时自动恢复。
- `backend/app/tools/publish_mobile_app.py` 读取后端配置，成功时向 stdout 输出包含 `version`（版本名）、`sha256`、`size`、`pushed` 的一行 JSON。带 `--push` 时自行连接配置中的 SQLite 与 FCM。推送失败只记录告警，APK 和清单继续可用。
- `backend/app/services/mobile_push.py` 通过普通通知通道发送 `kind=app_update`。标题为“Astro 有新版本 X”，正文取说明的前 80 个字符。缺少开关的旧设备默认开启，注册时可保存 `app_update=false`。

## 验收与下一任务

- 在 `backend` 内使用任务指定的锁定环境。定向回归 `-k "mobile or app_update"` 使用 `pt-p20/run2`，结果为 `305 passed, 4404 deselected, 1 warning in 83.66s (0:01:23)`。
- 全量回归使用 `pt-p20/run3`，结果为 `4698 passed, 11 deselected, 13 warnings in 363.91s (0:06:03)`。默认排除带 network 或 benchmark 标记的用例，警告为已有依赖弃用提示。
- 交付边界为本地提交。Android 的启动检查、系统安装确认及真实 FCM 送达留待 App 集成任务验收。
- 原有 P12、P13、P14、P15、P16 五份未跟踪任务书保留原位。下一任务可据本文件接入更新页、首页提示与安装流程。
