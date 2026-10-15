# 机会列表差价类型与市场类型标识优化交接

## 目标与范围

本任务只优化标的查询页“跨市场差价”列表中的差价类型和市场类型标识，使 `FF / SF` 以及“现货 / 合约”无需依赖隐含知识即可辨认。

本任务没有修改价差计算、排序规则、筛选值、市场身份、买卖方向或 Astro 路由，也没有扩展到交易可用性、账户连接、浮窗或其他功能模块。

## 分支与版本

- 分支：`codex/frontend-localization-polish`
- 开始基线：`85d9735 docs: record trade availability delivery`
- 功能提交：`f848dcc780cce7766a41b0ada32cfbafa1d89e37 style: clarify spread market type labels`
- 功能提交已推送到 `origin/codex/frontend-localization-polish`。

## 已完成功能

差价类型使用完整方向标签和固定颜色：

| 类型 | 页面标签 | 颜色 |
| --- | --- | --- |
| `FF` | `FF` / `合约 → 合约` | 蓝色 |
| `SF` | `SF` / `现货 → 合约` | 绿色 |
| `SS` | `SS` / `现货 → 现货` | 黄色 |
| 反向 `SF` | `反向 SF` / `合约 → 现货` | 红色 |

买入市场和卖出市场中的市场类型改为高对比徽标：

- `spot` 显示绿色“现货”。
- `future` 显示蓝色“永续合约”。

“屏蔽类型”筛选项同步显示完整含义：

- `FF 合约-合约`
- `SF 现货-合约`
- `SS 现货-现货`
- `反向 SF 合约-现货`

## 代码入口

- `frontend/src/pages/InstrumentLookupPage.tsx`
  - 新增统一的市场类型徽标和差价类型展示组件。
  - 买卖市场列复用市场类型徽标。
  - 差价类型列和筛选项展示完整方向。
- `frontend/src/styles.css`
  - 增加 `FF / SF / SS / 反向 SF` 四种语义色。
  - 增加现货与永续合约徽标样式。
  - 固定标签内部排版，避免手机端压缩或文字截断。
- `frontend/tests/InstrumentLookupPage.test.tsx`
  - 覆盖完整类型文案、筛选行为、买卖市场徽标和四种样式类。

## 本地验证

- `frontend/tests/InstrumentLookupPage.test.tsx`：`20 passed`。
- TypeScript 检查和 Vite 生产构建：通过。
- 从 Git index 生成独立临时提交和独立 worktree 复核，确认功能提交不依赖共享工作区中其他任务的未提交改动：专项 `20 passed`，生产构建通过。
- `git diff --cached --check`：通过。
- Playwright 桌面 `1440px` 与手机 `390px`：类型标签完整、标签自身无水平或垂直截断，页面与栏目均无横向溢出。
- 本地截图为未跟踪的 `output/spread-type-labels-local-desktop.png` 和 `output/spread-type-labels-local-mobile.png`，不提交仓库。

## 生产备份与部署

- 部署前在线备份：`backups/radar-20260922T101630Z.db`
- 大小：`599420928` 字节。
- SHA-256：`c55027d3bec757757a3459411db963578a61e07d5da6b2c8763f72d9580765f4`
- 源库、容器内在线副本和主机备份的 `PRAGMA quick_check` 均为 `ok`。
- 校验完成后已删除容器内临时副本 `/data/codex-radar-20260922T101630Z.db`；线上 `/data/radar.db` 未替换或删除。
- 服务器使用 `git pull --ff-only origin codex/frontend-localization-polish` 更新到功能提交 `f848dcc780cce7766a41b0ada32cfbafa1d89e37`。
- 执行 `docker compose build --pull`、补充构建前端镜像并执行 `docker compose up -d --remove-orphans`；没有执行 `docker compose down -v`。
- 前端和后端容器均为 `healthy`，前端根页面、`/api/health` 和 `/api/instruments/BTCUSDT` 均返回 HTTP 200。
- `/api/health` 返回 `status=ok`；Binance、OKX、Bybit、Gate、Bitget、Aster、Hyperliquid、Lighter 八家采集器均为 `healthy`。

## 生产视觉验证

通过 SSH 隧道直接检查部署后的生产前端：

- BTC 返回 `14` 个市场和 `91` 组差价，实际包含 `FF / SF / SS` 数据。
- 默认可见列表同时显示 `FF 合约 → 合约` 和 `SF 现货 → 合约`。
- 买卖市场同时显示绿色“现货”和蓝色“永续合约”徽标。
- `1440px`：`document clientWidth/scrollWidth = 1440/1440`，栏目为 `1190/1190`。
- `390px`：`document clientWidth/scrollWidth = 390/390`，栏目为 `372/372`。
- 两个视口的所有差价类型及市场类型标签均为 `clientWidth >= scrollWidth`、`clientHeight >= scrollHeight`，没有文字截断或重叠。
- `FF` 与 `SF`、现货与永续合约的文字色、背景色和边框色均不同。
- 生产截图为未跟踪的 `output/spread-type-labels-production-desktop.png` 和 `output/spread-type-labels-production-mobile.png`，不提交仓库。

## 工作区保护

共享工作区仍有后端行情与接口、账户连接、浮窗、机会表、价差页及相关测试的其他任务修改，以及大量 `output/**` 验证产物。这些内容均未回滚、删除、覆盖或纳入本任务功能提交。

服务器既有 `.env` 备份和 `CACHED` 未跟踪运维文件保持不动。未执行 `git reset --hard`、`git checkout -- <file>`、force push 或 `docker compose down -v`。

## 已知行为与残余风险

- 手机端没有页面级横向溢出；跨市场差价数据表保留既有的表内横向浏览，以访问买入市场之后的其余列。本任务只保证固定在首列的类型标签完整清晰，没有重构整张差价表。
- 默认屏蔽 `SS` 和反向 `SF`，因此生产默认列表主要看到 `FF / SF`；完整标签和样式已由专项测试覆盖，用户取消相应屏蔽后即可查看。
- 市场价格和差价数量会随实时采集变化，不影响类型映射。

## 下一步建议

本模块已经完成。后续若要重新设计手机端整张跨市场差价表，应新建独立任务，单独评估列折叠、详情展开和操作入口布局，不要在本交接任务中扩大范围。
