# 实盘页刷新抖动优化（2026-11-17）

用户反馈 /?page=live-exec 刷新时一直抖动。已知原因（请先确认再改，可补充发现的其它原因）：
1. frontend/src/pages/liveExec/useLiveExec.ts:80-85：任一资源（summary/positions/trades/stats）一次请求失败，`error` 即让**全部**数据返回 null，整页各区块清空再恢复，高度大幅跳动；Alert 出现/消失也推动布局。
2. frontend/src/pages/liveExec/useLiveExecAdmin.ts:31：轮询（5 秒）中单次失败即 setData(null) 清空该区块（外部持仓、远端持仓、负债额度、程序风险），随后又恢复；hasLiveExecAdminSession 的网络失败也会让 hasAdminSession 变 false，按钮闪烁。
3. LiveExecPage 每秒 setNow 重渲染；表格（scroll x、自动列宽）里倒计时/时长文字宽度变化导致列宽每秒重算、横向抖动。

## 要求
- 轮询失败时保留上一份成功数据（stale-while-error），不清空区块；连续失败 ≥2 次或数据超过 30 秒未更新时，在对应区块标题旁显示小的“数据已过期 N 秒”标记（固定位置、不改变布局高度）。全局“实盘服务未连接”提示同样要求连续失败 ≥2 次才显示，并且尽量不推动下方布局（例如固定位置或占位）。
- **安全不能放宽**：数据处于过期/失败状态时，所有写操作按钮（暂停/恢复开仓、外仓登记/注销、负债额度登记/注销、撤单等管理操作）必须禁用；写操作成功或 409 后的强制刷新仍然先清掉相关数据（保留 useLiveExecAdmin.ts:22 现有语义）。
- hasAdminSession 只在明确的 401/403（或 AUTH_REQUIRED_EVENT）时变 false；网络错误/超时保持上一次结果。
- 每秒变化的文字列（已持有时长、距资金费结算、距最长持仓到期、各区块里的“N 秒前”等）设置固定列宽或等宽数字（font-variant-numeric: tabular-nums），避免列宽每秒变化；表格列宽整体固定，不随内容抖动。
- 首次加载各区块用固定最小高度/骨架占位，避免数据到达时大幅跳动。
- 不改接口、不改业务字段含义。

## 验收
- vitest 补测试：单次失败不清空数据；连续失败后出现过期标记且写按钮禁用；恢复后标记消失；网络错误不改变 hasAdminSession；401 才变 false；强制刷新仍清空。
- `cd frontend && npx vitest run --no-cache`、`npx tsc --noEmit` 通过。
- 只改 frontend/src/pages/LiveExecPage.tsx、frontend/src/pages/liveExec/**、frontend/src/api/liveExec.ts（如确需）及对应测试；不要 npm install，不要 git commit。
