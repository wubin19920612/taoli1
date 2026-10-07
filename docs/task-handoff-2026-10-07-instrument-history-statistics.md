# 标的查询历史行情统计交接

## 目标与范围

- 模块：标的查询 / 精确原始市场行情统计。
- 跨市场差价表增加可排序的「24h 最大价差」列；精确行情市场增加「1h 涨跌幅」「24h 涨跌幅」。
- 不修改交易执行、Astro 建卡、自动风控、资金费率或数据库结构。

## 分支与基线

- 分支：`codex/frontend-localization-polish`。
- 本地开始基线：`dbb52a0cf97a025e1086e22adb5eb0421a0b6f2a`。
- 开始时生产 HEAD / 两份镜像：`fe88e38a7e1c465974e5ecd633f3274258688ba7`，服务器跟踪文件干净，前后端 healthy。
- 功能提交、镜像工作流、备份和部署验收将在部署后补充；本节不能作为已部署证明。

## 已实现功能与入口

- `backend/app/services/instrument_spreads.py`：提取稳定 `instrument_market_id`；保持原价差 ID 不变，身份包含交易所、现货/永续、DEX、原始市场、价格倍率和数量乘数。
- `backend/app/models/instrument.py`：精确行情候选返回 `market_id`；新增历史涨跌幅、24h 价差统计和独立响应模型。
- `backend/app/services/pair_spread_query.py`：公开 `fetch_market_klines`，复用既有交易所分页；优先使用原始市场，不使用规范 ticker 替代。Hyperliquid 直接用原始 coin / `@index` 请求分钟K线，保留 DEX，失败不替用别的现货或永续市场；Lighter/RH Lighter 使用各自独立端点。
- `backend/app/services/instrument_statistics.py`：按原始市场异步抓取已完成的分钟K线，对齐统计；2 分钟缓存、128 个市场 LRU、4 个并发、单次市场请求含排队最多 45 秒，同一市场并发请求共用抓取，关闭时取消任务并关闭客户端。
- `backend/app/api/routes_instruments.py`：新增只读 `GET /api/instruments/{symbol}/statistics`，支持 `dex` 参数；普通标的查询不等待历史请求。
- `backend/app/main.py`：注册统计服务并纳入生命周期关闭。
- `frontend/src/api/client.ts`、`frontend/src/api/types.ts`：独立历史 API 和类型。
- `frontend/src/pages/InstrumentLookupPage.tsx`：历史统计独立加载、自动刷新时每 2 分钟更新；按市场 ID 和方向 ID 关联，切换标的后忽略旧响应，缺失显示 `-` 而非零。
- `frontend/src/styles.css`：行情指标标签不逐字换行，数字列允许适应窄列；表格横向滚动局限于表内。

## 重要口径与边界

- 1h / 24h 涨跌幅 = `(最近已完成分钟收盘价 / 1h或24h前同一分钟收盘价 - 1) × 100`；必须存在对应基准，不用第一条样本冒充完整周期。最近K线过期不计算涨跌幅，tooltip 说明截至时间和来源。
- 24h 最大价差按当前行买卖方向，使用双方**同一分钟**收盘价，先乘各市场价格倍率，再计算 `2 × (卖价 - 买价) / (卖价 + 买价) × 100`，与既有百分比价差的对称分母保持一致。
- 这是历史分钟收盘价价差估算，不是历史可成交 Bid/Ask 峰值，不能代表扣除手续费、滑点或资金费率后的收益；标题、`≈` 和 tooltip 明示预估。
- 不将两市场不同时间的高低价相减；不取绝对值，负最大值保留负号。双向分别统计，实时最优方向翻转仍能关联缓存结果。
- 滚动窗口为最近 24h，排除未完成K线和窗口外点；1440 个对齐分钟点才标记「完整24h」，否则显示「部分样本」和点数，tooltip 展示覆盖区间及峰值时间。
- 某交易所失败不影响其它市场；无公开历史、无对齐数据或新上市不足周期返回空值 / 明确错误，不回填零，不混用相似 ticker 或其他 DEX。
- 现有当前买 Ask / 卖 Bid、双边 24h 成交额、资金费率周期、实际盘口和交易动作保持不变；数量乘数只参与身份隔离，不误当价格倍率。

## 本地验证

- 后端四文件回归：`test_instrument_statistics.py`、`test_instrument_lookup.py`、`test_instrument_spreads.py`、`test_pair_spread_query.py`：82 passed（227.12s）。最后补充原始 Hyperliquid 现货 coin 请求及缓存任务回收后，统计服务整文件复测：9 passed（3.51s），与 82 有重叠，不累加为独立测试数量。
- 前端 `InstrumentLookupPage` 整文件：34 passed（20.29s），包括涨跌幅 / 最大值展示、历史延迟不挡实时数据、失败不转零；既有 Astro、DEX、交易诊断、筛选和导航回归通过。
- `npm run build`：TypeScript 和 Vite 生产构建通过，最后含 CSS 变更构建 5.53s。
- 新服务 / 新测试完整 Ruff 检查通过；修改的既有后端文件定向 `--select F,E9` 检查通过；`git diff --check` 通过。
- 1440px / 390px 浏览器本地验证：使用只读生产 FLOCK 市场身份夹具，历史统计为明确的 mock；10 个行情市场、45 条方向统计，最大值和两周期涨跌幅可见，无页面横向溢出 / pageerror。该检查不冒充真实线上历史统计。
- 既有 Ant Design 属性废弃与 React act 警告保留，未扩展修复无关模块。

## 未跟踪产物与下一步

- 开始时已有 `.worktrees/`、多组 `backend/.pytest*`、其它任务交接文件、`output/` 截图 / 脚本 / 研究资料，全部保留，不暂存其它任务文件。
- 本任务日志、市场夹具、浏览器脚本和截图保留于 `output/instrument-statistics-*20261007*`、`output/verify-instrument-statistics-20261007.mjs`；不提交测试产物。
- 推送功能提交后等待两份 GitHub Actions 固定 SHA 镜像成功；通过生产脚本校验 SQLite 备份、`git pull --ff-only`、拉取镜像、Compose 启动，禁止在 2 GiB 服务器本地构建或删除卷。
- 上线验收必须包含前后端 health、真实统计接口、FLOCK 桌面 / 手机显示以及另一标的 / Hyperliquid 原始市场验证。部署后在本文件追加提交 SHA、工作流、备份文件 / 校验和、部署版本与结果。
- 若继续做逐秒 Bid/Ask 历史真实峰值记录，应另开行情采样模块任务，明确采样频率、保留期与存储 / 服务器资源预算；下一模块也请新建任务并提供本交接文档。
