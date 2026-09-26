# 浮窗账户持仓与账户连接交接

日期：2026-09-22

## 目标与范围

本任务只处理“浮窗账户持仓展示与屏蔽”模块，并补齐真实账户数据源配置：

- Hyperliquid 使用公开地址直接查询持仓，明确区分 `main` 和 builder-deployed DEX。
- Binance、OKX、Bybit、Gate、Bitget 使用单独的只读 API 查询现货余额和永续持仓。
- 同一交易所可以保存多个账户连接；不复用或推导 Astro 卡片数量。
- 浮窗继续按稳定持仓身份展示、屏蔽和恢复，并默认隐藏名义价值小于 1 USDT 的持仓。

没有扩展 Aster、HTX、KuCoin、MEXC、Lighter 等尚未实现并验证私有账户接口的交易所。

## 分支与基线

- 分支：`codex/frontend-localization-polish`
- 开始基线：`55a081a`（浮窗账户持仓交付后的文档提交）
- 开发期间分支吸收了同分支已推送的交易可用性改版、交付文档及状态指标改版，最终代码基线为 `5ec8ed7`。
- 功能提交和生产功能镜像：`c1a8c18`（`feat: add direct account position connections`）。

## 已完成功能

### 账户连接与安全

- 新增 SQLite `account_connections` 表和账户连接 CRUD、草稿测试、已保存连接测试接口。
- API Key、Secret、Passphrase 和 Hyperliquid 地址使用 Fernet 加密后保存；主密钥来自 `ACCOUNT_CREDENTIALS_MASTER_KEY`，不写入数据库或仓库。
- 所有账户连接接口同时要求服务端配置非空 `DASHBOARD_PASSWORD`，并校验 `X-Dashboard-Password`。
- 列表和测试响应只返回掩码提示，不返回明文或密文凭据；日志只记录交易所、范围和异常类型。
- 编辑时交易所不可变，空的凭据字段保留原值；同一交易所可以保存多个独立账户。

### 真实账户提供器

- CEX 通过 CCXT 分别建立现货和永续读取范围；单个范围失败不会阻止同账户另一范围或其他账户。
- 永续保留交易所原始市场 ID、方向、合约张数、实际合约乘数、开仓均价、标记价、名义价值、未实现盈亏、收益率、杠杆和交易所时间。
- 双向持仓的 long/short 使用不同身份；不同账户、交易所、原始市场和 DEX 不会合并。
- 现货使用实际非零余额，结合公开现货行情估值；未知估值保持可见且名义价值为空。
- Hyperliquid 使用官方 Info API 的 `clearinghouseState` 和 `metaAndAssetCtxs`；每个连接固定查询明确的 DEX。
- Hyperliquid 的 USDC 金额以及非 USDT 稳定币金额按 1:1 换算时明确标记为估算。
- 动态提供器按连接更新时间缓存；更新、禁用或删除后自动关闭旧客户端并重新加载。
- 旧的环境变量 Gate 永续提供器继续兼容；存在已保存的同范围连接时不重复查询旧提供器。

### 配置页面与浮窗

- 新增“账户连接”导航和紧凑配置页面，支持添加、编辑、启停、测试和删除连接。
- OKX/Bitget 动态显示 Passphrase；Hyperliquid 显示公开地址和 DEX；列表只显示凭据掩码。
- 桌面表格保留固定操作列；390px 窄屏取消粘滞并在表格内部滚动，页面本身无横向溢出。
- 浮窗账户状态现在显示现货/永续范围和 HL DEX，且按唯一账户 ID 统计账户数。
- 现有持仓屏蔽、恢复、持久化、同 ticker 不同身份和 `<1 USDT` 默认隐藏逻辑保持不变。
- 标记价、名义价值、未实现盈亏和收益率若为估算，会在字段名或价格口径中明确标注。

## 关键代码入口

- 数据模型：`backend/app/models/account_connection.py`、`backend/app/models/account_position.py`
- 加密和连接管理：`backend/app/services/account_connections.py`
- CCXT/Hyperliquid 提供器：`backend/app/services/account_position_providers.py`
- 局部失败和过期缓存：`backend/app/services/account_positions.py`
- API：`backend/app/api/routes_account_connections.py`、`backend/app/api/routes_account_positions.py`
- 数据库：`backend/app/db/schema.py`、`backend/app/db/repositories.py`
- 前端页面：`frontend/src/pages/AccountConnectionsPage.tsx`
- 浮窗：`frontend/src/components/FloatingWatchPanel.tsx`

## 重要业务规则

- 账户连接必须使用只读凭据，不应授予交易或提现权限；系统不会通过这些接口下单、平仓或修改交易所仓位。
- 浮窗屏蔽只影响浮窗，不影响账户采集、Astro、告警或其他页面。
- “已核验无持仓”“尚未配置”“权限不足”“接口失败”和“显示旧快照”是不同状态，不能互相替代。
- 持仓身份至少包含账户、交易所、现货/永续、原始市场、方向和具体 HL DEX。
- `<1 USDT` 只在浮窗默认隐藏；恰好 1 USDT 和名义价值未知的持仓保持可见。
- 公开行情估值、稳定币 1:1 换算和由名义价值反推的价格必须标记为估算。

## 测试结果

- 后端账户连接、提供器、身份、局部失败、配置和退出清理专项：`22 passed`。
- 后端全量：功能主体完成后为 `756 passed`，11 个既有 datetime 弃用警告；最终退出清理补丁已由上述专项覆盖。
- 前端账户连接、浮窗和导航专项：`36 passed`。
- 前端全量最终复跑：`174 passed, 1 failed`；唯一失败是既有 Settings 测试仍查找旧文案“实盘灰度”。
- 前端 TypeScript/Vite 生产构建：通过。
- Python `compileall`：通过。
- Playwright 视觉检查：1280px 和 390px 页面完成；390px 页面级横向溢出为 0，折叠菜单按钮和标题不相交。
- `git diff --check`：提交前通过。

## 线上状态

- 数据库在线备份：`backups/radar-20260922T043320Z-pre-floating-account-positions.db`，`599015424` 字节，SHA-256 `08fbac98684a4d94070ce86ddd58aac16d0d19d9543d8ab054050588bb7dfd8d`，`PRAGMA quick_check=ok`。
- 修改生产 `.env` 前另存 `.env.backup-floating-account-positions-20260922T043522Z`；生产已配置非空 `DASHBOARD_PASSWORD` 和新生成的 Fernet 主密钥，二者均未写入仓库或本文档。
- 服务器已执行 `git pull --ff-only origin codex/frontend-localization-polish`，并使用 `docker compose build --pull`、`docker compose up -d --remove-orphans` 重建；没有执行 `down -v`。
- 功能镜像版本为 `c1a8c18`；前后端容器均为 `healthy`，`/api/health` 返回 `status=ok`，8 个行情交易所均为 `healthy`、`exchange_errors={}`。
- 鉴权后的 `/api/account-connections` 返回 HTTP 200、`storage_ready=true`；`/api/account-positions` 返回 HTTP 200。
- 使用明确标记的 Hyperliquid 零地址临时连接验证官方接口：测试读取成功并得到已核验空仓；后端重启后连接仍存在，随后删除，生产连接数恢复为 0。
- 使用临时完整持仓身份验证浮窗屏蔽和恢复接口，写入与移除均返回 HTTP 200，清理后屏蔽列表数量恢复原值。
- 用户真实账户尚未在新页面填写，因此当前 `position_count=0` 只能解释为“尚未配置真实账户”，不能解释为“真实账户没有持仓”。

## 已知问题与残余风险

- 不同交易所账户模式和权限设置可能导致部分范围返回权限不足；页面和浮窗会保留其他成功范围并明确显示失败范围。
- 现货资产如果没有可用 USDT/USDC 公开市场，仍展示真实余额，但估值、名义价值和盈亏保持缺失。
- 交易所返回的更新时间缺失时使用本次查询时间；接口失败后只显示最后成功快照并标记过期。
- 前端全量测试仍有上述既有 Settings 旧文案失败，不属于本模块。

## 未跟踪文件

本任务不提交、删除或覆盖 `output/` 下的历史截图和研究产物，也不处理 `script/dexe_bybit_bitget_chain.py`。本任务本地视觉截图为：

- `output/account-connections-local-desktop.png`
- `output/account-connections-local-mobile.png`

## 下一步建议

- 部署后由用户在“账户连接”页面逐个填写 Hyperliquid 公开地址和 CEX 只读 API，并先执行“测试读取”。
- 每个实际账户验证浮窗数量、原始市场、方向、乘数、DEX 和估算标识，再验证屏蔽、恢复与后端重启持久化。
- 后续新增其他交易所账户接口时应单独建任务，只有在真实接口和身份规则完成测试后才能加入支持列表。
