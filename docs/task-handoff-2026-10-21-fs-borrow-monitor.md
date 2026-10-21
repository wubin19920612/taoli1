# Bybit 负费率 FS 观察池交接

## 目标与范围

第一版实现 Bybit 同平台、普通 1:1 USDT 线性永续多头 + 借币现货空头的持续观察。发现负费率后核验借贷支持、账户可借量、库存、成本。只监测，不实际借款/下单，不接 Astro，不发送外部消息。后续适配 Binance、其他平台及跨平台路线，应新建任务。

方案见 [设计文档](fs-borrow-monitor-design.md)。基线 `750f0fa`，分支 `codex/frontend-localization-polish`；开始时线上功能版为 `cf92fc9`。

## 已实现与入口

- 页面入口 `?page=fs-monitor`，菜单“负费率 FS 观察”；桌面表格、手机卡片、展开成本与来源明细、配置、历史快照及站内事件。
- `backend/app/services/fs_bybit.py`：固定 Bybit 主机，公开与签名 GET 白名单、分页目录、实时规格、订单簿、公开/账户利率、最大可借量、可用库存、账户手续费。异常只输出静态分类，不泄露凭据。
- `backend/app/services/fs_economics.py`：统一基础币数量、双腿步长最小公倍数、交易限制、四侧 VWAP；下一结算/指定持有期/24h 预估，基差恶化压力情景。
- `backend/app/services/fs_monitor.py`：限流、单飞、50秒扫描上限、配置/账户变化失效、已有精确资产身份核验、结果新鲜度、状态变化事件。
- `backend/app/services/fs_repository.py`：独立连接到 `radar.db`，新增 `fs_settings`、`fs_snapshots`、`fs_events`；快照288份、事件500条有界留存，纳入现有主库备份。
- `backend/app/api/routes_fs_monitor.py`：report/settings/refresh/history/events，所有接口要求非空仪表盘密码。
- `frontend/src/pages/FsMonitorPage.tsx`、CSS、`frontend/src/api/fsTypes.ts` 与 client；AppShell 仅新增菜单与懒加载页面。

## 业务边界

- 默认开启公开扫描，每轮深查15条（最多30），目标1000 USDT，8小时持有期。优先查公开支持借贷的现货对，按实时每小时负费率排序；这是有限候选，不保证展示全部负费率市场。
- 默认不选择账户。用户可在 FS 配置里选择“账户连接”中已有、启用现货及永续读取的 Bybit 账户。API 权限不足/账户现货杠杆关闭时显示未知或阻断，不修改交易所开关。
- 档位上限不等于库存；可用库存又受平台、用户和档位限制。目标借币数额外预留一小时利息扣除空间；容量不证明永续开仓保证金足够。
- 只接受匹配的原始市场、base、USDT quote/settle、Trading LinearPerpetual；非1倍率不自动合并。Bybit 旧采集器 contract_size_multiplier 为空时依赖已核验单位 + 官方线性规格，显式非1仍拒绝。
- 使用已核验资产绑定和最新双腿盘口运行 pair validation；没有绑定仍可看到估算，但不能成为“成本通过”。首版不新增人工资产映射。
- 手续费采用配置假设时只能观察；选中账户并查到真实费率、额度/库存、身份、深度、成交额及压力净收益均通过后，才标“成本通过（预估）”。不是交易授权。
- 资金费按实际未来结算时点计数；退出恰好等于结算时点时保守不计。下一结算情景包含两分钟退出余量。利息向上取整小时为保守预算，不是交易所实际账单。
- 四侧盘口已含买卖差与吃单冲击；不假定基差归零，不重复扣盘口滑点。资金费、利率、标记价和退出盘口保持当前值只是估算假设。
- 盘口最多有效30秒、其他输入60秒，并受身份复核有效期（最多15秒）约束；过期结果降级。页面5秒轮询不触发交易所请求。账户删除/禁用/修改、配置改变或扫描失败，不保留旧的通过状态。
- 快照不是实际成交或收益台账，尚未实现逐笔真实资金费对账/回测。历史借贷库存不可从历史费率倒推。

## 验证

- 后端专项涵盖离散结算、同数量成本、步长、缺失字段、零库存、权限不足、身份冲突、过期与异常盘口、GET 白名单/签名、秘密脱敏、有界留存、API认证、账户整轮扫描及在途配置变更。
- 前端 FS 页面与 AppShell 16项通过；类型检查和 Vite 生产构建通过。
- 真实公开接口整轮扫描成功；本地无账户/无身份目录时准确显示未知，生成三种情景。没有用真实账户借币请求验证执行能力。
- 桌面1440px与手机390px浏览器检查通过：配置保存、候选与成本展开，无整页横向溢出，无页面脚本错误。证据 `output/fs-local-browser-20261021.json` 与 `output/fs-local-*-20261021.png`。
- 首轮测试修正了测试初始化参数及 Decimal 断言；菜单测试随新增导航更新。首轮浏览器等待样本超时，重测成功，失败日志未清除。
- 后端专项及账户/原资金费率回归共49项通过；最后补充身份有效期上限后，27项非API专项复测通过。Ruff检查通过。日志 `output/fs-backend-tests-20261021.log`、`output/fs-frontend-tests-final-20261021.json`、`output/fs-build-20261021.log`。

## 线上状态

- 2026-10-21 已发布功能提交 `86047e204b5502180d9d9d8242c902f477bdccdd`，已推送当前分支。[GitHub Actions 39533044886](https://github.com/wubin19920612/taoli1/actions/runs/39533044886) 的 backend/frontend 双镜像成功。服务器 `git pull --ff-only` 到该版本，使用同 SHA 预构建镜像启动，未在生产机构建。
- 部署前主库备份：`backups/radar-before-86047e204b55-20261021T022931Z.db`，657842176字节，SHA-256 `cd894fa1f01b131acac6eb16b274bf5bb0e48f4cbb1db14c61a7ccf482caac75`。
- 路线库备份：`backups/squeeze-route-before-86047e204b55-20261021T022931Z.db`，241664字节，SHA-256 `6e27207773a30780bff96f153f8656df0af9ebfdd83d640b72d1eb5a78166e9a`。两库 SQLite `integrity_check=ok`，容器内外哈希一致。既有保留脚本执行后各留10份备份。部署日志 `output/fs-deployment-20261021.log`。
- 前后端容器 healthy，线上 Git 工作区 tracked clean。启动约6分钟后 backend 553.2MiB/768MiB、frontend 4.938MiB/96MiB；这是短期采样，不是长时间负载证明。
- `/api/health` 为 `ok`、12192个市场、交易所错误为空。无密码访问 FS report 返回401；认证后 report/settings/history/events 均可读，refresh 返回202并完成新扫描。
- 2026-10-21 10:37:10 北京时间完成的一轮：84个负费率市场、15个候选（12 discovered、3 blocked），历史2份、事件27条，整轮 error=null。较早样本过期后15条均降级 stale；重新刷新恢复对应观察状态。记录见 `output/fs-production-api-20261021.json`、`output/fs-production-fresh-20261021.json`。
- 线上候选中14条 identity unverified、1条 conflict。MEWUSDT 双腿在既有市场目录中均是 identity/unit pending，不能将此解释为已验证的可交易路线；首版没有新增人工绑定。未选择账户，账户额度、库存及真实手续费仍未实测，因此没有 cost_pass，也没有执行任何借款/交易。
- 通过 SSH 隧道读取真实部署页面，1440px/390px 候选、详情展开均正常，无页面错误或整页横向溢出。证据 `output/fs-production-browser-20261021.json`、`output/fs-production-*-20261021.png`；未修改线上配置。
- 日志抽样发现其他模块 `trade_availability.py`、`announcements.py`、`oil_news.py` 存在 RuntimeError/HTTPStatusError 堆栈；本次检查未发现 FS 路由500，FS report error=null。其他模块错误未扩展修复，也未清理相关日志，需其所属任务另查。
- 本次最终交接与 `docs/next-task.md` 另作仅文档提交；线上运行镜像版本仍为上述功能 SHA。

## 工作区与下一步

- 两份原有 WIP `frontend/src/pages/PairMonitorPage.tsx`、`frontend/tests/PairMonitorPage.test.tsx` 不属于本模块，必须保留并排除提交。
- 保留所有既有 `.worktrees/`、pytest 临时目录、交接草稿、output DB/WAL/SHM/日志/截图。新增 `output/fs-*`、本地预览服务脚本和浏览器脚本只是证据，不提交。
- 本地预览端口8016/5186，使用独立 `output/fs-preview-20261021.db`；不要与线上数据库混用。
- 后续先由用户选定具备所需查询权限的 Bybit 账户，验证账户端点适用性，并通过现有身份流程补齐目标标的绑定；账户只读权限能否覆盖全部端点须以实际查询为准，不为监测主动增加交易权限。再考虑 Binance 适配、跨平台精确身份与保证金、实际结算对账。外部通知和真实交易需要独立范围说明。
