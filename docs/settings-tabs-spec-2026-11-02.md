# 参数与告警页改版 + 合并告警历史（2026-11-02）

## 目标
1. 参数与告警页（`SettingsPage`）改成标签页，最常用的告警规则不再压在最底部。
2. 告警历史并入该页，作为一个标签，导航里去掉独立的“告警历史”入口。
3. 去掉“正差价正费率实盘实验”面板的前端部分。后端接口由另一个分支处理，这里只删前端面板、相关 state、API 调用和类型；类型如果别处也引用，就保留。

数据逻辑、接口调用、各表单字段 name 和保存行为不变（实盘实验除外）。

## 允许修改的文件
- `frontend/src/pages/SettingsPage.tsx`
- `frontend/src/pages/AlertHistoryPage.tsx`：可改成导出一个可嵌入的组件
- `frontend/src/components/AppShell.tsx`：只改“告警历史”导航项及其路由兼容
- `frontend/src/api/*`、`frontend/src/types*`：只删实盘实验专用的前端调用和类型
- `frontend/src/styles.css`：只改设置页、告警历史页专用的规则，新增规则放在附近；不要在文件末尾追加，也不要改其他页面的规则
- `frontend/tests/SettingsPage.test.tsx`、`frontend/tests/AlertHistoryPage.test.tsx`、`frontend/tests/AppShell.test.tsx`：DOM 变化时调整定位方式；删除实盘实验专属的测试；其余断言不得删除或放宽

不要 git push，只在当前分支提交。

## 标签页结构
用 antd `Tabs`，所有面板必须 `forceRender`（或用等效方式保持挂载），保证切换标签时各 Form 的未保存修改和校验状态不丢。

1. **告警规则**（默认标签）：告警规则编辑 + 告警规则表 + 告警内容模板。
2. **风险参数**：原“风险参数”面板，内部 3 个主题（多交易所溢价告警、信号策略、币名映射）用小节分隔，保持一个表单和一个保存按钮。
3. **Astro**：Astro 自动化 + Astro 卡片默认参数。
4. **访问与服务**：面板访问 + 服务控制。
5. **告警历史**：嵌入原 `AlertHistoryPage` 的内容（测试告警按钮、刷新、表格）。
   - 状态列 sent/failed 等英文值显示为中文（已发送、失败等，按实际取值映射，未知值原样显示）。
   - 刷新按钮加上文字“刷新”。
   - 只在切到这个标签时才请求告警历史数据，不要随设置页一起加载。

其他要求：
- 当前标签存 localStorage，键 `settings-active-tab`，读写都包 try/catch。
- 每个可保存的面板，把保存按钮放在该面板标题栏右侧，可以保留底部按钮。
- 英文：“Signal strategy（信号策略）” → “信号策略”；“信号策略备注 / 后续自定义规则 (Signal strategy notes)” → “信号策略备注 / 后续自定义规则”。

## 导航与兼容
- `AppShell` 默认导航去掉 `history`。
- 用户保存过的导航顺序（localStorage `taoli1:navigation-order.v1`）里如果有 `history`，读取时忽略，不报错。
- 通过 URL 或其他代码跳到 `history` 页面时，改为打开设置页并选中“告警历史”标签。先 grep 所有 `"history"` 的页面跳转来源，逐一处理。

## 验收
在 `frontend` 目录运行：
1. `npx tsc -b`，不能有错误；
2. `npm test`（vitest run），全部通过，贴出汇总行；
3. `npx vite build`，构建成功。

报告需包括：标签页的挂载方式、`history` 的兼容处理、删除了哪些实盘实验前端代码，以及每个测试改动的原因。
