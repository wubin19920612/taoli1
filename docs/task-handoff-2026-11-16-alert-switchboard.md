# 告警总开关面板交接

## 目标与范围

按 `docs/alert-switchboard-spec-2026-11-16.md` 在设置页标签上方集中展示风险类开关、普通规则汇总、公告开关和手机价格提醒汇总。仅本地修改；用户要求不提交、不运行 WSL。本轮未暂存、提交、推送或部署，也未操作线上服务。

分支：`codex/alert-switchboard`。基线：`35f41118e1d02052ccea5d4e1f70a28665c4a3d0`。开始时工作区干净。

## 实现入口与业务规则

- `frontend/src/components/AlertSwitchboard.tsx`：面板、各组独立错误处理、保存失败提示及回滚；组件少于 300 行。
- `frontend/src/components/AlertSwitchboard.css`：弹性布局和文本折行，适配窄屏。
- `frontend/src/pages/SettingsPage.tsx`：只增加组件导入及挂载，传入原有风险表单、规则列表及 `updateRiskSettings`。
- `frontend/tests/AlertSwitchboard.test.tsx`：组件行为测试。
- `frontend/tests/SettingsPage.test.tsx`：保留现有测试，补充公告接口数据及面板与风险表单联动、失败回滚集成测试。

风险开关直接观察、修改页面的 `riskForm`，上下两处共用同一份表单状态。每次保存先 GET 最新风险配置，再仅替换点击字段调用原有保存 API；成功和失败均只更新该表单字段，保留其他未保存输入。沿用客户端现有的大价差屏蔽列表保护，不改变大价差业务逻辑。

公告配置独立加载；失败保留整张面板，公告组显示“加载失败”、禁用本组开关并提供重试。公告保存同样先 GET 最新值，只替换一个字段，其余字段原样回写；失败恢复先前开关状态。同组保存期间禁止重复提交。

普通规则和手机价格提醒只展示“已启用 N / 共 M 条”，不提供批量开关。普通规则汇总跟随页面 `rules`，手机提醒通过既有规则 API 在面板挂载时读取。风险类跳到“风险参数”，两个规则汇总跳到“告警规则”，公告跳到 `?page=announcements`。

## 验证

全部命令使用 Windows PowerShell 与本机 Node.js。本机 Vitest 默认最小 worker 数大于规格中的最大值 2，因此执行测试时仅设置进程环境变量：

```powershell
$env:VITEST_MIN_FORKS = '1'
$env:VITEST_MIN_THREADS = '1'
cd frontend
npx vitest run --maxWorkers=2 tests/AlertSwitchboard.test.tsx tests/SettingsPage.test.tsx
npx tsc --noEmit
```

- 组件测试：13 passed。
- 两文件最终联合回归：`Test Files 2 passed (2)`；`Tests 41 passed (41)`，退出码 0（组件 13 项、SettingsPage 28 项）。
- `npx tsc --noEmit`：通过，退出码 0。
- `git diff --check`：通过。

测试输出包含 React `act(...)` 和 Ant Design `There may be circular references` 警告，无失败测试或未处理错误。

## 线上状态、边界与后续

未部署，未验证生产接口或实际浏览器视口。手机提醒汇总为面板挂载时的快照；在同一页面新增或删除手机提醒后，重新加载页面可刷新此汇总。GET 后 PUT 沿用现有接口，不提供跨浏览器并发更新的原子比较交换保证。

新增未跟踪文件均属本任务：面板 TSX、CSS、组件测试和本交接文档；没有既有未跟踪产物。工作区修改保留给用户审阅。后续发布或其他模块请另开任务，先核对本交接与最新 Git 状态。
