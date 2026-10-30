# 交接：新币极速监控总开关

日期：2026-09-21

## 目标与范围

本任务只处理“新币极速”模块的总开关。目标是在保留已有监控标的和单项启用状态的前提下，一次性停止定时采样、公告预热、启动时公告扫描、新币机会标记和 Astro 自动建卡，并阻止手动“立即采样”。

验收目标：

- 总开关持久化，页面刷新、后端重启或容器重建后不会自动恢复为开启。
- 总开关关闭时不再创建新的公告预热监控或新币 Astro 卡片。
- 已有监控标的继续保留，重新开启总开关后可以恢复使用。
- 页面明确显示关闭状态，并禁用“立即采样”。
- 生产环境部署后将总开关实际设置为关闭。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 开始基线：`ddfc5f477de7d9a6a93f297de28760de5bb4436e`
- 功能提交：`e27f6a1b71f285b48cc746a6913d5ffd7d4dddc4`
- 功能提交说明：`feat: add new listing monitor master switch`

## 已完成功能

- 新增持久化设置 `NewListingMonitorSettings(enabled: bool = True)`，存储在 `app_settings` 的 `new_listing_monitor` 键中。
- 新增设置读取和更新接口；更新接口沿用 Dashboard 密码认证。
- 后台采样每轮开始前读取总开关，关闭时不采集任何标的。
- 状态接口新增 `enabled`；关闭时 `active_watch_count` 固定为 `0`，但保留总监控数和单项启用数。
- 手动采样在总开关关闭时返回 HTTP `409` 和 `新币极速总开关已关闭`。
- 公告实时预热、启动时近期公告扫描、近期新币机会标记和专用 Astro 建卡都受总开关约束。
- “新币极速监控”页标题区新增立即持久化的“总开关”。
- 关闭后页面显示警告，状态显示“总开关已关闭”，列表和详情中的“立即采样”按钮均禁用。
- 页面初次加载设置状态前不再短暂显示为开启。

## API 与代码入口

接口：

```text
GET /api/new-listing-monitor/settings
PUT /api/new-listing-monitor/settings  {"enabled": false}
GET /api/new-listing-monitor/status
POST /api/new-listing-monitor/watchlist/{item_id}/collect
```

后端入口：

- `backend/app/models/new_listing.py`
- `backend/app/db/repositories.py`
- `backend/app/services/new_listing_monitor.py`
- `backend/app/api/routes_new_listing_monitor.py`
- `backend/app/main.py`
- `backend/tests/test_new_listing_monitor.py`

前端入口：

- `frontend/src/api/types.ts`
- `frontend/src/api/client.ts`
- `frontend/src/pages/NewListingMonitorPage.tsx`
- `frontend/tests/NewListingMonitorPage.test.tsx`

## 重要业务规则

- 新增设置默认值为 `enabled=true`，保证未保存过该设置的旧环境升级后行为兼容；生产环境已按用户要求显式保存为 `false`。
- 总开关与单项 `watch.enabled` 相互独立。关闭总开关不会删除监控行，也不会改写任何单项启用状态。
- 总开关关闭后，后台协程仍保持存活并定期读取设置，但不会执行采样。页面以总开关状态为准，不把协程存活误显示为“运行中”。
- 如果总开关在一轮采样已经开始后才关闭，该轮中已经发出的网络请求可能完成；后续采样轮次会停止。当前生产状态已确认活跃标的为 `0`。
- Astro 通用配置没有被修改；这里只阻止新币极速模块触发的新币专用建卡。

## 验证结果

- 新币监控和公告专项后端测试：`66 passed`。
- 告警循环和 API 回归测试：`105 passed`，仅有一个既有 Pydantic 弃用警告。
- 最终新币监控专项测试：`21 passed`。
- 新增前端页面测试：`1 passed`。
- 前端生产构建：通过。
- Python `compileall`：通过。
- `git diff --check`：通过，仅显示工作区 LF/CRLF 提示。
- Ruff 未运行：当前环境没有 Ruff 可执行文件或 Python 模块。
- 桌面截图：`output/new-listing-master-switch-desktop.png`。
- 390px 手机截图：`output/new-listing-master-switch-mobile.png`。
- 生产桌面截图：`output/new-listing-master-switch-production.png`。
- 桌面和手机页面均确认警告、状态、开关和操作按钮无重叠。

前端全量测试结果为 `155 passed, 1 failed`。唯一失败可独立复现，且与本任务无关：

```text
SettingsPage > loads and saves Live Pilot settings
Unable to find an element with the text: 实盘灰度
```

当前 `SettingsPage.tsx` 已不再渲染该旧标题，本任务没有修改 Settings 页面或其测试。

## 生产备份与部署

- 部署前数据库备份：`backups/radar-before-new-listing-master-switch-20260921T064632Z.db`
- 大小：`718966784` 字节
- SHA-256：`ebc8cf41326f7d58d2e5a6e923a42429aa55e1cd1a46d100aabbe5d4b4607591`
- SQLite 校验：`PRAGMA quick_check = ok`
- 服务器从 `ddfc5f4` 使用 `git pull --ff-only` 快进到 `e27f6a1`。
- 使用 `sudo docker compose up -d --build` 重建并启动服务；没有执行 `docker compose down -v`。
- 部署构建中的前端 TypeScript 和 Vite 生产构建通过。
- 后端和前端容器最终均为 `healthy`。
- 前端页面入口 `/?page=new-listing` 返回 HTTP `200`，部署资源包含总开关关闭提示。
- 前端 `/api` 反向代理读取设置返回 `{"enabled":false}`。
- `/api/health` 返回 `status=ok`。

## 线上实际状态

2026-09-21 部署后已通过认证接口把生产总开关从 `true` 设置为 `false`，复核结果：

```text
settings.enabled=false
status.enabled=false
watch_count=291
enabled_watch_count=288
active_watch_count=0
manual collect=409 新币极速总开关已关闭
```

这说明 291 条已有配置全部保留，288 条单项启用状态也没有被总开关改写，但当前没有任何标的处于实际活跃监控状态。

## 已知限制与残余风险

- 总开关不会取消关闭瞬间已经在途的单次交易所请求；它保证后续采样轮次、预热和自动建卡停止。
- 设置存储依赖现有 SQLite `app_settings` 表和数据库卷；若人为替换或恢复旧数据库，需要重新检查总开关值。
- 前端全量测试仍有一个 Settings 页旧文案失败；应在独立任务中修复。
- Ruff 工具未安装，已由专项测试、回归测试、编译和生产构建覆盖本次验证。

## 工作区保护项

本任务提交时，本地工作区同时存在另一项“全交易所交易可用性”任务的修改。本任务只按代码块暂存 `backend/app/main.py`、`frontend/src/api/client.ts` 和 `frontend/src/api/types.ts` 中的新币总开关内容，没有提交、删除或回滚以下并行修改：

- `backend/app/db/schema.py`
- `backend/app/main.py` 中未暂存的交易可用性代码
- `backend/app/api/routes_trade_availability.py`
- `backend/app/models/trade_availability.py`
- `backend/app/services/trade_availability.py`
- `backend/tests/test_trade_availability.py`
- `frontend/src/api/client.ts` 和 `frontend/src/api/types.ts` 中未暂存的交易可用性代码
- `frontend/src/pages/InstrumentLookupPage.tsx`
- `frontend/src/styles.css`
- `frontend/tests/InstrumentLookupPage.test.tsx`

既有 `output/` 截图、探测产物和 `script/dexe_bybit_bitget_chain.py` 也保持未提交。服务器上的 `.env.backup-codex-20260911-1425`、`.env.backup-poll-8-20260911` 和 `CACHED` 未被修改。

## 下一步建议

- 保持生产总开关关闭；需要恢复新币极速时，只在页面重新打开总开关，不需要重建 291 条监控配置。
- 若后续需要“关闭时立即取消所有在途请求”，应另开新币监控任务评估取消语义、数据库写入边界和交易所客户端的可中断性。
- Settings 页旧文案测试和“全交易所交易可用性”功能应分别在各自任务中完成，不扩展本模块范围。
