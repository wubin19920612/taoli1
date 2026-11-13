# 大价差提醒按路线屏蔽交接

## 目标与范围

- 模块：价差与资金费率告警，仅增加大价差提醒的路线屏蔽。
- 已完整阅读 `docs/big-spread-mute-spec-2026-11-13.md`。
- 工作树：`.worktrees/big-spread-alert`；分支 `codex/big-spread-alert`；基线 `1f12c69`。
- 按用户要求只提交本地 Git，提交信息 `feat: 大价差提醒按路线屏蔽`。不 push、不部署、不运行 WSL。

## 已完成与代码入口

- `backend/app/models/settings.py`：`BigSpreadMuteRoute`、`BigSpreadMute`、创建请求及 `RiskSettings.big_spread_alert_mutes`。标的大写并去除 `-/_`，保留其他前缀；交易所去首尾空白并小写。默认永久、双向、空备注，创建时间为 UTC。标的必须精确，交易所支持完整 `*` 通配。最多 500 条有效屏蔽；读取和保存时清除过期项。
- `backend/app/services/big_spread_alert.py`：只在快速通道资格判断中检查路线、到期与双向匹配；普通规则与全局黑名单流程保持独立。屏蔽期间连续命中清零，到期后重新累计。
- `backend/app/services/alert_reject.py`：保留原有拒绝详情、原始标的/DEX 及限频规则；追加 `rule_id=big_spread`、`reasons=[big_spread_muted]`，普通规则命中也不丢失屏蔽诊断。
- `backend/app/main.py`：异步诊断结束、建卡处理开始前，以及实际发送前，重新读取屏蔽状态；等待期间新增屏蔽会生成 muted 事件并抑制发送。
- `backend/app/db/repositories.py`：通过现有 risk 设置 JSON 存储，无数据库迁移。读取清理、风险设置保存、路线增删共用连接级 `asyncio.Lock`；同一数据库连接上的多个仓储实例也共用锁。普通风险保存未显式传屏蔽字段时保留当前列表。
- `backend/app/api/routes_alerts.py`：GET/POST/DELETE `/api/alerts/big-spread-mutes`，均继承现有会话鉴权。返回完整有效屏蔽列表。POST 同一路线覆盖原项，支持 `duration_hours` 或 `until`；两者冲突、已过期、非正时长、溢出、空身份、部分通配及超限返回 422。DELETE 只按规范化后的 symbol+buy+sell 精确删除存储键，不按双向或通配语义扩大删除范围。
- `frontend/src/api/types.ts`、`client.ts`：增加类型和列表/增删 API；普通风险参数 PUT 主动省略屏蔽列表，避免旧表单覆盖刚新增的屏蔽。
- `frontend/src/components/BigSpreadMuteDialog.tsx`：共用弹窗，提供 1 天、7 天、永久、备注及默认开启的双向选项；手动新增可选择任意交易所。独立表单名避免与设置页字段 ID 冲突；关闭销毁，失败保留弹窗供重试。
- `frontend/src/components/BigSpreadMutesPanel.tsx`、`pages/SettingsPage.tsx`：在大价差提醒区域展示标的、方向、到期（UTC+8）、备注，支持新增、删除及刷新。
- `frontend/src/pages/AlertHistoryPage.tsx`：仅大价差行显示“屏蔽此路线”。从现有固定消息路线行提取交易所，弹窗保留含原始市场、DEX、倍率的路线描述；无法识别路线时禁用按钮并提示手动新增。
- `backend/tests/test_alert_big_spread_mutes.py`、`frontend/tests/AlertHistoryPage.test.tsx`、`SettingsPage.test.tsx`：补充资格、到期、通配、双向、普通规则独立、拒绝日志、并发、持久化、500 条、会话鉴权、校验、等待期间新增屏蔽、弹窗与列表增删等回归。

## 业务边界

1. 屏蔽粒度严格按任务书的标的和买卖交易所；不新增市场类型或 DEX 字段，因此覆盖该交易所对应标的的各市场和 DEX。界面明确展示此范围，原始市场信息仍保留在消息与拒绝日志。
2. 默认双向；关闭“双向”后仅匹配存储的买入/卖出顺序。匹配前标的按相同规则规范化，不追加 USDT，也不剥离 DEX 前缀。
3. 无时区的日期按 UTC 处理，带时区的日期转换成 UTC；到期边界为 `until <= now`。
4. 同键 POST 更新有效期、备注等，不消耗额外配额。到期项先清理再应用 500 条上限。
5. 显式在 risk PUT 里提供屏蔽列表表示完整替换；当前前端始终使用专用路线增删接口，普通 risk PUT 不发送该字段。
6. 并发锁遵循当前应用的单进程、共用 SQLite 连接模式；未新增多进程/多连接事务方案。

## 实际验证

均使用 Windows 原生 PowerShell，前端复用已有 node_modules 目录链接，没有安装依赖或修改其目标。

后端在 `backend/` 运行，PowerShell 显式展开任务书的 `tests/test_alert*.py`：

```powershell
$alertTests = @(Get-ChildItem -LiteralPath tests -Filter 'test_alert*.py' -File | Sort-Object Name | ForEach-Object { 'tests/' + $_.Name })
pytest tests/test_big_spread_alert.py @alertTests -q -p no:cacheprovider --basetemp ../.tmp-bsmute
# 196 passed in 34.43s
```

最终版本复验使用独立 basetemp，避免复用旧目录引起 Windows 权限问题：

```powershell
pytest tests/test_big_spread_alert.py @alertTests -q -p no:cacheprovider --basetemp ../.tmp-bsmute-final
# 196 passed in 35.76s

pytest tests/test_api.py tests/test_repositories.py tests/test_route_permissions.py -q -p no:cacheprovider --basetemp ../.tmp-bsmute-api
# 144 passed, 2 warnings in 67.37s (0:01:07)
```

补充测试的两条警告分别为旧 `datetime.utcnow()` 弃用提示，以及 `test_settings_repository_defaults` 收集到的 aiosqlite 线程 `Event loop is closed`。未影响断言，未在本任务中追查来源或修改相关清理逻辑，未声称已证明属于基线问题。

前端在 `frontend/`：

```powershell
npx.cmd tsc --noEmit
# exit 0
npx.cmd vitest run --no-cache
# Test Files  44 passed (44)
# Tests       685 passed (685)
# Duration    189.94s
```

局部验证：新增后端测试首轮 `35 passed in 12.66s`（随后补充 2 个等待期间屏蔽用例）；历史页 `12 passed (12)`；设置页新增用例通过，定向命令使用 `-t` 时其余 23 项未执行，最终全量已覆盖这些项目。首次前端相关测试有 5 个失败，暴露弹窗关闭后 DOM 保留与表单 ID 重复，均已修复后复验。前端全量仍输出 React act 和现有组件弃用警告，未产生测试失败。

`git diff --check` 和修改后端文件的 Ruff `E9,F63,F7,F82,F401` 检查通过。本地日志保留于 `.tmp-bsmute-*.log`，不提交。

## 线上状态、未做项及下一步

- 未 push、部署、备份线上数据库或进行线上验证；不声称生产已生效。未运行 WSL，未发送真实通知或写入真实 Astro 卡片。
- 本次未运行后端整个 tests 目录或真实浏览器人工验收；验收使用相关后端回归、前端类型检查和 Vitest 全量。
- 历史入口复用现有消息中的固定路线行，没有为旧事件增加结构化路线字段或做数据库迁移；缺失路线的记录通过设置页手动屏蔽。
- 测试开始前已有 `.tmp-bigspread*`、`.tmp-bs2/` 和两份未跟踪任务书，均保留且不纳入提交。已有 `.tmp-bigspread-related/` 存在权限警告，未修改权限或清理。
- 本任务新增 `.tmp-bsmute*` 目录和日志属于本地验证产物，保留、不提交。未改动 node_modules 链接。
- 后续发布或其他模块请新建任务，引用本交接，先审阅本地提交，再按新授权决定推送、备份和部署。
