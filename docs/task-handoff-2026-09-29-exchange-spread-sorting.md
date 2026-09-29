# 交易所价差筛选与排序交接

## 目标、范围与基线

- 在现有「实时机会」页面选择交易所，展示买入或卖出任一侧属于该所的跨标的价差，并按指标升降序排列。
- 分支：`codex/frontend-localization-polish`；开始基线：`679c758bd9cb785c396c08a701d21ff9aa388969`。
- 不更改行情采集、机会生成、交易执行及通知配置。沿用当前机会快照和全局屏蔽、风险、类型过滤；不是交易所全部市场的覆盖承诺。

## 实现与入口

- `backend/app/api/routes_opportunities.py`：`GET /api/opportunities` 增加白名单 `sort_by` 和 `sort_order=asc|desc`；先过滤、全量排序、最后 limit。缺失值在两个方向都置后，同值以完整路由 ID 稳定排序；不修改原快照。不传排序参数时保持旧 API 行为。
- `frontend/src/components/TopFilters.tsx`：交易所可搜索/清空，补全独立的 RH Lighter；增加排序指标和方向。
- `frontend/src/pages/DashboardPage.tsx`、`components/OpportunityTable.tsx`：默认开仓价差降序；表头名称/价差排序与后端同步，避免前端对截断结果再次排序。资金费率排序时额外显示对应的精确排序值。
- `frontend/src/state/useRadarStore.ts`：条件变更立即发起新请求、清除旧条件行，过期请求不能覆盖新结果；自动刷新失败仍沿用原有错误提示。
- 类型契约位于 `frontend/src/api/types.ts`。

## 指标口径

- 排序支持：标的、买/卖交易所、开仓/平仓价差、扣费后价差、买/卖方 24h USDT 成交额、当前/预测每小时净资金费率。
- 资金费率按各自周期归一化，买多卖空，正值表示净收入；预测值不补成当前值，未知显示 `-`。原有资金费率详情保持双方周期展示。
- 价差保留正负号，按数值而非绝对值排序。成交额未知不当作零。
- 保留原始合约、市场类型、DEX、价格/数量倍率与现有价差查询跳转身份。
- 前端默认取排序后前 120 条，可调整或清空条数；排序是当前过滤后快照范围，不仅当前显示页。

## 验证

- 后端机会相关专项：42 passed；调整参数类型注解后新增排序/筛选专项再次 29 passed。
- 前端 DashboardPage、OpportunityTable、useRadarStore 共 30 项通过（表格测试选择器修正后 11 项专项复测通过）。
- `npm run build`（包含 TypeScript）通过。
- Playwright 本地生产构建：1440px/390px 交易所选择和资金费率排序可用，无页面异常及页面横向溢出。使用模拟行情，不代表线上行情验收。
- `git diff --check` 通过。Ruff 在未修改的 `list_markets` 参数已有 B008 告警；本次新增参数使用 Annotated，未引入同类告警。

## 线上状态

部署前只读确认：生产分支同上，版本 `081b1aa096907823eb741aafbd9737b0a8e07822`，tracked 工作区干净，两容器 healthy，约 20 GiB 空闲空间。

功能提交、镜像构建、备份、部署与线上验收证据将在完成后追加。生产必须使用 `deploy/linux-update.sh` 和 GHCR 预构建镜像，不在 2 GiB 主机编译。

## 工作区与后续

- 已有 `.worktrees/`、pytest 临时目录、`output/**`、其他交接文档和 `script/dexe_bybit_bitget_chain.py` 均保留，不归入本次提交。
- 本次浏览器脚本和截图在 `output/verify-exchange-spread-local.mjs`、`output/exchange-spread-local-{1440,390}.png`，不提交。
- 后续独立模块应新建任务；若需要交易所所有原始市场覆盖、CSV 导出或多级联合排序，应另行明确范围。
