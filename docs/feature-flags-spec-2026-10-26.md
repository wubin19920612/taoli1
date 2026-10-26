# 功能裁剪开关 DISABLED_FEATURES：任务书（Claude，2026-10-26，用户已批准）

## 1 目标

新增环境变量 `DISABLED_FEATURES`（逗号分隔，默认为空）。列在里面的功能在**启动时不构造服务、不启动后台任务**，接口返回“已停用”，前端菜单隐藏。**默认为空时行为与现在完全一致**。用来在生产上实测裁掉这些功能能省多少内存，并且可以随时恢复（删掉这个变量再重启即可）。

## 2 功能键（固定白名单）

| 键 | 菜单 | 后端（需要 Codex 盘点补全） |
|---|---|---|
| `squeeze` | 挤仓结构观察 | squeeze-monitor / squeeze-route-monitor / squeeze-paper-monitor，`routes_squeeze_arbitrage`，相关 repo 与独立数据库连接 |
| `negative_basis` | 负基差埋伏 | negative-basis-monitor，`routes_negative_basis_monitor` |
| `oil_news` | 原油新闻 | oil-news-loop / oil-news-translation-loop，`routes_oil_news` |
| `opportunity_radar` | 机会雷达 | opportunity-radar-alert-loop，`routes_opportunity_radar` |
| `symbol_spread` | 跨所价差（前端 key `symbol-spread`） | 对应接口及 `_REALTIME_SYMBOL_SPREAD_CACHE` 的写入入口 |
| `fat_finger` | 乌龙回测 | 对应接口 |
| `gate_twap` | Gate 定时减仓 | `routes_gate_twap`、GateTwapClient/JobManager |
| `second_level_sampling` | 1s 采样 | `routes_second_level_sampling`、SecondLevelSampler（含启动时的 `initialize()`） |
| `funding_research` | 资金研究 | `routes_funding_research`、funding-research-loop（与现有 `FUNDING_RESEARCH_ENABLED` 取“与”） |

键不在白名单里时，配置加载直接报错（fail fast，防止拼写错误悄悄失效）。

## 3 设计要求

1. **先盘点依赖，写进交接**：每个功能的服务、后台任务、路由、repo/数据库、定时器；以及**谁引用了它**（其他服务、告警、`/api/health/detail`、memory_probe 的 `cache_census` 登记、前端其他页面）。被核心功能依赖的部分不能直接不构造：要么保留轻量占位，要么在交接里说明并**停下来报告**，不要自作主张改核心逻辑。
2. 停用时：不构造服务对象、不启动后台任务、不打开该功能独享的数据库连接；`app.state.<x>` 设为 None（或保持不存在）且所有读取方能安全处理；路由仍然挂载，但统一返回 HTTP 404 `{"detail": "feature_disabled"}`（用一个公共依赖或中间件实现，不要逐个接口复制粘贴）。
3. 新增 `GET /api/features` → `{"disabled": [...]}`（无需鉴权、无敏感信息）。前端 `AppShell.tsx` 按这个结果隐藏对应菜单项；直接访问已停用页面时显示“该功能已停用”。接口请求失败时前端按“全部启用”处理。
4. 配置放 `config.py`（`disabled_features: frozenset[str]`），只在启动时读取一次。
5. 不删除任何功能代码、数据或表结构；不改这些功能启用时的行为。

## 4 文件范围

- 允许修改：`backend/app/core/config.py`、`backend/app/main.py`、`backend/app/api/` 下与上述功能相关的路由文件（只加停用依赖）、新建 `backend/app/api/routes_features.py` 和一个公共依赖模块（例如 `backend/app/core/features.py`）、`frontend/src/components/AppShell.tsx`、`frontend/src/api/client.ts`、`frontend/src/api/types.ts`、必要的前端页面守卫；新建测试 `backend/tests/test_feature_flags.py`、前端测试；新建交接 `docs/task-handoff-2026-10-26-feature-flags.md`。
- 禁止修改：各功能的业务逻辑、FS 相关文件（`fs_*.py`）、`cache_census.py`、部署脚本、compose、CI；`frontend/src/pages/PairMonitorPage.tsx` 及其测试。

## 5 测试

1. 默认（空）：所有路由和后台任务与现在一致（对照现有测试全部通过）。
2. 每个键单独停用：对应后台任务未启动（检查 supervisor 任务名）、服务未构造、路由返回 404 feature_disabled、其他功能正常。
3. 全部 9 个一起停用：`create_app` 正常启动，`/api/health`、`/api/health/live`、`/ready` 正常，memory_probe/census 开启时不报错（目标记为 missing）。
4. 未知键 → 配置报错；大小写和空格容错（`" Squeeze , oil_news "` 可以识别）。
5. 前端：`/api/features` 返回的项被隐藏；接口失败时显示全部菜单。
6. 后端全量 `python -m pytest -q --basetemp=<worktree 内目录>`，前端 `npm test` 和 `npm run build`。

## 6 上线验收（Claude，用户已批准设置 .env）

- 先部署代码（默认为空，确认行为不变）→ 先备份 `.env`，再设置 `DISABLED_FEATURES=squeeze,negative_basis,oil_news,opportunity_radar,symbol_spread,fat_finger,gate_twap,second_level_sampling,funding_research` → 重建后端 → 探针采 1 小时，与 2026-10-26 17:34–18:35 的数据对比（rss_anon 底部、尖峰、增长斜率）。
