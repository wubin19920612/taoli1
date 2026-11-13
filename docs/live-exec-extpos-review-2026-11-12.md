codex
**可部署：是（本次增量安全复审结论，有 3 项非阻断 P2）。无 P0/P1。**

- **P2：快照失效后，确认按钮仍启用。** [LiveExternalPositions.tsx:118](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/frontend/src/pages/liveExec/LiveExternalPositions.tsx:118)。打开弹窗后快照过期或缺失，“确认提交”仍可点击。实测提交函数会拦截、没有写请求；应同时禁用按钮。

- **P2：方向变化后仍可沿用旧确认。** [LiveExternalPositions.tsx:41](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/frontend/src/pages/liveExec/LiveExternalPositions.tsx:41)。实测同合约由多仓变为空仓后，弹窗仍显示“多”且可以提交。登记覆盖双向，因此不会串合约，但应在方向变化后要求重新确认。

- **P2：第十四轮风险口径展示尚未同步。** [LiveExecPage.tsx:82](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/frontend/src/pages/LiveExecPage.tsx:82)。既有页面仍将全账户跌幅与 `max_equity_drop_usd` 比较并标红；上游 `679791a` 已将该阈值改为程序损益口径。新增 `income` 字段读取兼容，但风险展示应区分账户观测值与程序停机依据。

新增写接口沿用会话鉴权、CSRF、字段白名单、乐观并发和审计，409 正文原样返回，未发现旁路。原始六字段无归一化或截断，未登记持仓高亮有效；未发现其他页面回归或测试放宽。

本次验证：后端 **384 项**、前端 **131 项**通过，TypeScript 无输出检查及 `diff --check` 通过。全程未修改文件、未提交，最终工作区干净。
tokens used
154,519
**可部署：是（本次增量安全复审结论，有 3 项非阻断 P2）。无 P0/P1。**

- **P2：快照失效后，确认按钮仍启用。** [LiveExternalPositions.tsx:118](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/frontend/src/pages/liveExec/LiveExternalPositions.tsx:118)。打开弹窗后快照过期或缺失，“确认提交”仍可点击。实测提交函数会拦截、没有写请求；应同时禁用按钮。

- **P2：方向变化后仍可沿用旧确认。** [LiveExternalPositions.tsx:41](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/frontend/src/pages/liveExec/LiveExternalPositions.tsx:41)。实测同合约由多仓变为空仓后，弹窗仍显示“多”且可以提交。登记覆盖双向，因此不会串合约，但应在方向变化后要求重新确认。

- **P2：第十四轮风险口径展示尚未同步。** [LiveExecPage.tsx:82](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/frontend/src/pages/LiveExecPage.tsx:82)。既有页面仍将全账户跌幅与 `max_equity_drop_usd` 比较并标红；上游 `679791a` 已将该阈值改为程序损益口径。新增 `income` 字段读取兼容，但风险展示应区分账户观测值与程序停机依据。

新增写接口沿用会话鉴权、CSRF、字段白名单、乐观并发和审计，409 正文原样返回，未发现旁路。原始六字段无归一化或截断，未登记持仓高亮有效；未发现其他页面回归或测试放宽。

本次验证：后端 **384 项**、前端 **131 项**通过，TypeScript 无输出检查及 `diff --check` 通过。全程未修改文件、未提交，最终工作区干净。
