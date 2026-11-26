# P20 手机 App 自升级（后端，2026-11-26）

设计稿：`D:/吴彬工作/claude/套利/research/mobile-app/p20-self-update-design.md`（先读）。参考 `routes_mobile*.py`、`mobile_query_utils.py`、`mobile_push.py`（PushKinds、send_urgent/queue 写法）、`app/core/security.py`。

## 范围
- 新建：`backend/app/services/mobile_app_release.py`（清单读取/校验/发布）、`backend/app/api/routes_mobile_app.py`（在 routes_mobile include）、`backend/app/tools/publish_mobile_app.py`（命令行，`python -m app.tools.publish_mobile_app`）、测试 `backend/tests/test_mobile_app_update.py`。
- 可改：`app/models/mobile.py`（清单模型；PushKinds 新增 `app_update: bool = True`）、`mobile_push.py`（新增发送 app_update 的入口，普通通道，不改其他类别）、`app/core/config.py`（`mobile_app_dir` 默认 `/data/mobile-app`，测试可覆盖）。
- 不可改：其他接口、前端、security 白名单（新接口都是 GET，已在 `/api/mobile/` 前缀放行）。

## 清单 `latest.json`
`{version_code:int>0, version_name:str(≤32), size:int, sha256:hex64, notes:str(≤2000), published_at:ISO8601 UTC, file:"astro-mobile-<code>.apk"}`

## 接口
1. `GET /api/mobile/app/latest` → 清单字段（去掉 file）；没有清单或文件缺失/大小不符 → 404 `暂无可用更新`。读清单时不重新算哈希（发布时算好）。
2. `GET /api/mobile/app/apk` → 流式返回 APK（`FileResponse` 或分块），`Content-Type: application/vnd.android.package-archive`、`Content-Length`、`ETag: "<sha256>"`、`Content-Disposition: attachment; filename=astro-mobile-<name>.apk`；无发布 404。限频：每会话同一时刻 1 个下载、两次开始间隔 ≥30 秒 → 429 `下载太频繁，请稍后再试`。下载在途占用在响应结束或客户端断开时释放。
3. 内部错误 503 `更新服务暂不可用`，不泄露路径和异常。

## 发布命令
`python -m app.tools.publish_mobile_app --apk PATH --version-code N --version-name X [--notes TEXT] [--push]`
- 校验：文件存在、≤60MB、ZIP 魔数、包含 `AndroidManifest.xml`、`classes.dex`；version_code 必须大于当前清单（`--force` 可同号覆盖用于重发）。
- 复制到 `mobile_app_dir/astro-mobile-<code>.apk.tmp` → fsync → rename；算 SHA-256；写 `latest.json.tmp` → rename。只保留最新 2 个 APK，删除更旧的。
- `--push`：读取设备表，给 `app_update` 开关开启的设备发推送（kind `app_update`，普通通道，标题 `Astro 有新版本 X`，正文 notes 前 80 字）；推送失败只告警不影响发布。命令在容器内独立运行（自建 DB 连接和 FCM 客户端，读 .env 同一配置）。
- 输出一行 JSON 结果（version、sha256、size、pushed 设备数）。

## 验收
- 测试：清单解析与缺失/大小不符 404、apk 流式内容与头、Range 可选、429 与释放、503 不泄露、mobile_readonly 可访问而无会话 401、发布命令各项校验失败、原子替换与只保留 2 个、version_code 不递增拒绝/--force、--push 调用与 PushKinds 开关。
- pytest 用锁定 venv，`--basetemp` 到 scratchpad：先 `-k "mobile or app_update"` 再全量，输出汇总行。
- **只 commit：禁止 git push、ssh/scp、部署，忽略 AGENTS.md 推送部署步骤。** 不跑 WSL。commit `feat(mobile): in-app update endpoints and publish tool (P20)`；输出 diff 文件清单、pytest 汇总行、清单模型字段。
