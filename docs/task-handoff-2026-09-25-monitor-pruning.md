# 交接：退役分钟价差信号与新币极速

日期：2026-09-25（Asia/Shanghai）。模块：监控功能裁剪。

## 目标与基线

- 用户要求删除“1 分钟价差信号”和“新币速递”；现有菜单中后者名为“新币极速”，本任务按该独立监控模块处理。
- 验收目标：两页从菜单和旧链接消失，专属接口、后台采样、公告预热、飞书通知和 Astro 特殊建卡路径停止运行；其他行情、告警及交易所公告继续可用。
- 分支 `codex/frontend-localization-polish`；实现前 HEAD 为 `17ccab1bb74bd31f032be378d21fda52d7f58484`。本次提交及部署版本见下方线上状态。

## 已完成与关键入口

- `frontend/src/components/AppShell.tsx` 删除两个页面键、懒加载入口和菜单项。旧的 `?page=minute-signals`、`?page=new-listing` 回到默认“实时机会”；旧菜单排序中无效的键会被过滤。
- 删除两个页面、专属 API 类型与客户端函数、专属 CSS 和对应页面测试；`frontend/src/pages/SettingsPage.tsx` 删除新币 Astro 专属参数表单。
- `backend/app/main.py` 不再注册两个路由、不再启动新币极速后台任务或公告预热，也不再按近期上币公告给常规机会打 `NEW_LISTING` 标签。`backend/app/services/announcements.py` 去掉预热回调，保留公告采集和独立上/下币提醒。
- 删除两套专属服务、路由、模型和配置。常规 Astro 自动建卡恢复使用普通卡片参数，不因历史 `NEW_LISTING` 标签强制开仓。通用价差告警和 Live Pilot 继续按自身规则运行。
- `backend/app/db/schema.py` 不再为新数据库创建四张 `new_listing_*` 表。已有数据库的表和行不执行 DROP 或 DELETE，便于备份和历史核查；旧模块 API 已不可访问。

## 验证

- 后端相关回归：`python -m pytest tests/test_api.py tests/test_alert_loop.py tests/test_astro_alerts.py tests/test_repositories.py tests/test_announcements.py -q`，194 passed。
- 后端全量：`python -m pytest -q --basetemp=.pytest_module_prune_20260925`，750 passed，只有既有的 Pydantic 弃用提示和 pytest 缓存权限提示。
- 前端：`npm test -- AppShell SettingsPage --silent`，27 passed；`npm run build` 通过 TypeScript 检查和 Vite 生产构建；`git diff --check` 通过。
- 本地无采集服务使用内存数据库。浏览器实测菜单为 19 项，不含两个旧入口；两条旧页面链接均显示“实时机会”。截图位于 Codex 可视化目录 `monitor-pruning-local.png`，未提交仓库。
- 本地 `http://127.0.0.1:3000/` 和前端代理 `/api/health` 返回 200；旧 `/api/minute-signals/scan`、`/api/new-listing-monitor/status`、`/api/settings/astro-new-listing-card` 返回 404；保留的 `/api/settings/announcements` 返回 200。
- GitHub Actions run `36083544346` 对提交 `8e0e29f9caff786a1a2790b0a1a547aaa986beaa` 的 backend、frontend 构建均为 `completed/success`，整个 run 为 `completed/success`。

## 线上状态

- 功能提交 `8e0e29f9caff786a1a2790b0a1a547aaa986beaa` 已推送到 `origin/codex/frontend-localization-polish`。服务器经 `deploy/linux-update.sh` 备份后执行 `git pull --ff-only`，仓库已快进到该提交。
- 部署前备份：`backups/radar-before-8e0e29f9caff-20260925T015417Z.db`（服务器 `~/wubin/taoli1` 下），`PRAGMA integrity_check=ok`，容器与主机 SHA-256 一致；复核文件 SHA-256 为 `1053a421bee07a204cf0096a963c6ed2a290668062a9fbefb2c32fd9e890fcf5`。
- 后端和前端均运行 `ghcr.io/wubin19920612/taoli1-{backend,frontend}:sha-8e0e29f9caff786a1a2790b0a1a547aaa986beaa`，Docker Compose 两容器均为 `healthy`。服务器后端和前端代理 `/api/health` 均返回 200，前端根页面返回 200。
- 生产前端代理下，`/api/minute-signals/scan`、`/api/new-listing-monitor/status` 和 `/api/settings/astro-new-listing-card` 均返回 404；`/api/settings/announcements` 与 `/api/announcements?limit=1` 均返回 200。
- Playwright 打开生产页面：菜单共 19 项，不含“1 分钟价差信号”“新币极速/新币速递”，保留“交易所公告”；旧 `?page=minute-signals` 和 `?page=new-listing` 均选中“实时机会”。公告页面已加载记录，截图位于 Codex 可视化目录 `monitor-pruning-production.png`。
- 部署脚本的既有自动备份保留规则保留最近三份，并清理一份较早的自动备份 `radar-before-6926681fb8a4-20260924T051046Z.db`；本次备份仍在服务器上。

## 工作区与后续

- 任务开始时已有未跟踪 `.worktrees/`、`output/`、`script/dexe_bybit_bitget_chain.py` 和 `docs/task-handoff-2026-09-24-spread-alerts-aster-production.md`；均未纳入本任务或改动。
- 本轮全量 pytest 生成 `backend/.pytest_module_prune_20260925/`。该目录仅含本轮测试夹具，但 Windows ACL 拒绝删除，仍为未跟踪文件；不要误加到提交。
- 下一步建议：此模块已完成。已有数据库中的 `new_listing_*` 历史表和记录仍保留；若以后需要物理清理，应单独设计备份和迁移。本任务之外的功能请在新任务中继续。
