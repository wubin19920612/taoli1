# FS 公开借币数据展示与 Astro 查询核验

## 目标、范围与基线

用户要求将“借币 / 额度 / 库存”主表位置用公开 API 数据填充，并自行核实 Astro 是否提供账户额度和库存查询。仍属于负费率 FS 观察池模块，覆盖 Bybit、Binance、OKX、Bitget、Gate。保留既有信号达标自动建立停用 Astro FS 卡片与去重，不借币、不下单、不变更账户权限或杠杆、不发送外部通知。

开始时分支 `codex/frontend-localization-polish`，本地与 origin 均 `fca56cc1f3365840347f1964231818a6eabaa6d9`；线上 Git 和双镜像均 `79eef76143702c395afbe41db228316e977ec9ca`，tracked clean、双容器 healthy、health=ok。前一交接见 [五交易所观察池](task-handoff-2026-10-22-fs-multi-exchange.md)。

## 已实现

主表标题改为“借币 / 公开参考 / 账户”，桌面、手机和展开详情使用同一借币展示组件：

| 平台 | 主表补充内容 | 口径 |
| --- | --- | --- |
| Bybit | 普通档 No VIP 公开参考上限 | `maxBorrowingAmount`，基础币数量 |
| OKX | 基础额度，未乘等级系数 | `basic.quota`；定制币种按 `configCcyList` 转到 `config` 中 Lv1 / 普通策略，绝不回退到失效的 basic 额度 |
| Bitget | 全仓产品支持、最小借币量 | `isCrossBorrowable`、`userMinBorrow`；交易金额上限不是借币上限 |
| Gate | 逐仓产品支持、最小借币量 | `base_min_borrow_amount`，不能取 USDT 的 `quote_min_borrow_amount` |
| Binance | 市场杠杆交易支持 | `exchangeInfo.isMarginTradingAllowed`，不能升格为基础币可借支持 |

没有公开上限的接口明确显示“该公开接口未提供上限”。账户额度/库存独立显示“未接入查询”、具体查询错误或实际返回数值；零库存显示 0，不与缺失合并。极小借币量使用有效数字格式，`0.00000001` 不会显示成 0。所有借币数量带基础币单位。

公开来源、适用范围与采样时间进入 `FsCandidate.public_borrow`，状态区分 available / not_listed / query_failed。保留旧 `public_vip_limit` 的 Bybit 语义，旧历史可无新字段读取；无需数据库迁移。公开资料超过原60秒时窗、时间异常或整个候选过期时标记过期；历史值不冒充当前容量。

不把参考上限、最小借币量或 Binance 的市场杠杆标记写入 `account_max_loan` / `available_inventory`，不修改账户成本晋级和身份、利润、有效期门槛。Binance 产品查询复用原20秒规格缓存，避免重复请求。没有添加 Astro 轮询或私有查询。

关键入口：`backend/app/models/fs_monitor.py`、`backend/app/services/fs_public.py`、`backend/app/services/fs_monitor.py`、`frontend/src/pages/FsMonitorPage.tsx`、`frontend/src/api/fsTypes.ts`；新测试 `backend/tests/test_fs_borrow_reference.py`。

## Astro 已自行核实的结论

2026-10-22 12:14、12:21 UTC 使用生产现有 SDK 配置，实际只读调用 list 均成功，返回114张卡片，列出了全部返回字段，没有借币额度/库存字段。读取当前 Astro 页面和实际主 JS（546662字节，SHA256 `58a4f9769595d7cc0a09242a340a4f59e1222b6eba26091da59dbf5333ee08b8`），静态调用列表中未发现借贷查询端点。没有调用页面里的配置、开发者代码、交易、重启、资金转移等接口。

同时直接读取最新官方仓库原文，SDK 文档最新变更提交为 `f6e281f3de8983918958007d8638da7c7c372ae3`。文档公开的是卡片管理、消息发送及收益查询三个入口，没有借币额度、库存和账户借币利率接口。[固定版本 SDK 文档](https://github.com/astro-btc/Astro/blob/f6e281f3de8983918958007d8638da7c7c372ae3/SDK-API.md)。搜索引擎缓存仍只列两个入口，因此本轮以仓库原文为准。

结论限定为：**当前已核验的 Astro 对外 SDK 不提供所需借贷查询，现有卡片列表不能代替账户额度/库存**。这不否定 Astro 内部 FS 执行可以借贷，也不宣称所有未公开内部接口绝对不存在。不得猜测读写端点、借用其他交易所凭据或调用开发者代码绕过接口边界。证据 `output/fs-borrow-astro-capabilities-20261022{,b}.json` 仅保存字段名称、静态路由和哈希，不保存凭据或卡片配置值。

## 公开来源与实际验证

- [Bybit 公开档位数据](https://bybit-exchange.github.io/docs/v5/spot-margin-uta/vip-margin)：本地真实读取 BMT 上限262000枚、MEW上限2240000枚（仅采样时点）。
- [OKX 公开利率和额度](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-interest-rate-and-loan-quota)：MEW基础额度14000000、ZIL1200000、LPT5000；日利率仍除24换算小时。
- [Bitget 公开杠杆产品](https://www.bitget.com/docs/catalog/classic-margin-common/classic-margin-common)：ZIL等 `userMinBorrow=0.00000001`，没有账户最大可借量。
- [Gate 逐仓产品](https://www.gate.com/docs/developers/apiv4/en/isolated-margin/)：例如 BMT 最小50、ZIL135，都是基础币数量。
- [Binance 公开市场规格](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-endpoints#exchange-information)：BMTUSDT杠杆市场标记true，没有据此放行借币信号。

新适配器真实调用五家成功，结果在 `output/fs-borrow-provider-probe-20261022.json`。这些是公开数据和 Astro 只读列表验证，**真实账户额度、库存、利率和手续费仍未实测**。原有 Binance/Bitget/Gate 私有利率、身份未核验等阻断不变。

## 测试与交付

后端 FS公开数据/监测/多平台/Astro桥接、Astro客户端/planner、账户连接回归117项通过（140.60秒）；前端 FS 14项 + AppShell12项共26项通过，类型检查/Vite生产构建通过。Ruff与差异空白检查通过；保留原有React act告警。日志为 `output/fs-borrow-{backend,frontend,build}-20261022a.log`。新增覆盖定制额度优先级、空值/零值/NaN、最小借币量单位、Binance产品标记隔离、公开查询失败、旧历史兼容、公开大额度不能使未配置/权限不足/零库存账户通过，以及前端过期与错误显示。最终部署结果见后续补记。

## 工作区保护与后续

两份既有 PairMonitor WIP 未改动，SHA256保持 `11379f2cb0881b274a998a4a4534a698c4dd94b0080de3de691f4dbf27440d38` 和 `0376b098c2274e60666915709d9d1183decac45781a861faac452aacb00500ab`。既有未跟踪文件、数据库/WAL/SHM和日志全部保留；本轮 output/fs-borrow-* 为未提交探测和验证证据。

下一步若需真实账户容量，必须有交易所只读账户查询，或 Astro 后续正式提供借贷查询接口后再适配；本轮未选择账户、未读取/导出 Astro 交易所密钥。首次自然达标的真实 FS 建卡回读仍沿用上一交接的待验证项。其他模块使用新任务。
