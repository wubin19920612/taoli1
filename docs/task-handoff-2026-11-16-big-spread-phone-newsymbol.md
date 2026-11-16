# 大价差电话新标的窗口交接

## 目标、范围与基线

- 按 `docs/big-spread-phone-newsymbol-spec-2026-11-16.md` 实现大价差夜间电话的新标的限制。
- 分支：`codex/phone-new-symbol`；基线：`b20bd3f5b07615f2e67352a13b54737625a9e147`。
- 用户明确要求仅本地实现与验证，不提交、不运行 WSL；本次不暂存、不推送、不部署。
- 初始工作区干净；其他告警逻辑及 `.github` 不在修改范围内。

## 已实现与关键入口

- `backend/app/models/settings.py`：`RiskSettings.big_spread_phone_new_symbol_days` 默认 7，允许整数 0–90；0 跳过历史查询，保留旧电话行为。
- `backend/app/main.py`：`_process_alert_match()` 将当前事件传入 `_maybe_send_big_spread_phone()`，在读取每晚电话名额之前查询 `alert_events`。
- 历史条件为 `rule_id = big_spread`、同一 `symbol`、`status IN (sent, muted)`，时间在当前时刻前 N 天至当前时刻内，按 `id` 排除本次事件。窗口下界包含在内。
- 命中历史或历史查询异常均跳过电话，不写电话 delivery、不消耗名额，文字与卡片原有流程继续。查询异常仅记录一行异常类型名 warning，不输出异常详情或 traceback。
- `frontend/src/api/types.ts`、`frontend/src/pages/SettingsPage.tsx`：新增字段、旧数据默认值及“新标的窗口(天)”输入，校验整数 0–90，并随风险设置保存。
- `frontend/src/components/AlertSwitchboard.tsx`：夜间电话状态追加“仅新标的(N天)”，0 时省略；缺省按 7 天显示。

## 测试

- `backend/tests/test_big_spread_phone.py`：覆盖设置缺省和边界、sent/muted 历史、1/7/90 天及窗口下界、窗口外与无历史、无关规则/币种/状态、未来记录、0 天绕过查询、查询异常与日志隐私、本次事件自身排除、不占电话名额，以及真实 SQLite 文字事件跨夜阻止电话。
- `frontend/tests/SettingsPage.test.tsx`：扩充原有断言，覆盖默认 7 天、保存 14 天、非法 91 天与合法 0 天。
- `frontend/tests/AlertSwitchboard.test.tsx`：新增缺省、7 天、14 天及 0 天文案测试。
- Windows 原生后端命令：`python -m pytest tests/test_big_spread_phone.py tests/test_big_spread_alert.py tests/test_big_spread_throttle.py -q`，结果：`188 passed in 13.87s`。
- Windows 原生前端命令：`npx tsc --noEmit`，结果：退出码 0，无错误输出。
- Windows 原生前端命令：`npx vitest run --maxWorkers=1 --minWorkers=1 tests/AlertSwitchboard.test.tsx tests/SettingsPage.test.tsx`，结果：`Test Files  2 passed (2)`、`Tests  45 passed (45)`、`Duration  162.59s`，退出码 0。
- `git diff --check` 通过；现有测试断言未放宽。

## 线上状态、已知问题与工作区

- 线上未操作，未做数据库备份、构建、部署或真实电话拨打；本次结果是本地自动化验收。
- 当前没有新增已知功能问题；历史判定按规格直接匹配事件的 `symbol`，不改写 DEX 或原始市场信息。
- Vitest 输出 React `act` 和 `There may be circular references` warning，全部测试通过；本次未扩展处理这些测试环境 warning。
- 使用 `npm ci --offline --no-audit --no-fund` 在本工作树安装前端依赖；`frontend/node_modules/` 为忽略产物，锁文件未改。
- 新增未跟踪交接文档为本文件；其他改动为后端入口/设置/电话测试、前端类型/设置页/开关面板及两份对应测试。

## 后续

本模块在本地验收后停止。其他模块请另建任务；如后续要求提交或部署，先复核当前 diff 和工作区归属，再按项目 Git 与部署清单完成交付。
