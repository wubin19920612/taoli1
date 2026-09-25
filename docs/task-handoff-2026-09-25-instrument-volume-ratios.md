# 交接：标的查询合约成交额比值

日期：2026-09-25（Asia/Shanghai）

## 目标与范围

- 模块：标的查询。新增“合约 / 流通市值”和“合约 / 现货”两个指标，口径分别为已覆盖永续市场 24h 成交额除以 CoinGecko 流通市值、已覆盖永续市场 24h 成交额除以已覆盖现货市场 24h 成交额。
- 分支：`codex/frontend-localization-polish`；开始基线 `f3f1f96da0e401a122b4a909b7986d0008b98cbc`。功能提交 `e62f8cafb7f0adb5f27d7644a1b33c139cb72494`，生产核验后的别名修正提交 `c946aa4b3680bbde64c710948ae905a6215742aa`，均已推送。

## 实现与入口

- `backend/app/services/instrument_market_cap.py`：CoinGecko 精确 ticker 搜索和 `/coins/markets` 流通市值查询。搜索缓存 6 小时，市值缓存 5 分钟，缓存上限 256 个条目；同名币种多于一个时不自动选择。
- `backend/app/api/routes_instruments.py`、`backend/app/models/instrument.py`、`backend/app/main.py`：独立 `GET /api/instrument-market-cap/{base}` 接口、响应模型和服务生命周期；`coin_id` 必须属于该 ticker 的精确候选。
- `frontend/src/pages/InstrumentLookupPage.tsx`、`frontend/src/api/{client,types}.ts`、`frontend/src/styles.css`：摘要区显示两项比值，下方显示合约/现货成交额覆盖数、市值资产名与时间。多个 CoinGecko 候选时可选和改选；市值独立于 10 秒行情刷新，页面每 5 分钟更新一次。
- `backend/tests/test_instrument_market_cap.py`、`frontend/tests/InstrumentLookupPage.test.tsx`：覆盖唯一/同名候选、非法选择、来源失败、缓存、过期市场、别名市场、页面刷新及选择交互。

## 业务规则

- 分子/分母使用 `/api/instruments/{symbol}` 的精确市场 `volume_24h_usdt`，按 `(exchange, market_type, raw_symbol, multiplier, dex)` 区分与去重；只计未过期、有限、非负的成交额。缺少可用分母或分母为零时显示 `-`，不补造数据。
- 原始标的被重映射到其他规范标的的市场不计入成交额，仍保留在原有精确市场表格中。例如生产数据中的 Bitget `RZETAUSDT` 被映射到 `ZETAUSDT`，但两者价格差距很大；ZETA 现货比值排除该别名行，并显示“1 个别名未计”。
- 摘要展示的是本系统已覆盖市场的成交额合计，不代表全球总量。页面标明计入/总市场数及是否含预估值。市值为 CoinGecko 的 USD 值，市场成交额为 USDT 值，合约/市值比按近似平价显示 `≈`；它不是持仓量 OI 或可成交深度。
- CoinGecko 返回同 ticker 多个资产时必须人工选择；搜索无匹配、来源失败或市值缺失时不显示合约/市值比。市值与行情各自按独立时间和错误状态处理。

## 验证

- 后端：`python -m pytest -p no:cacheprovider tests/test_instrument_market_cap.py tests/test_instrument_lookup.py -q`，11/11 通过；新增后端文件 `ruff check` 通过。
- 前端：`npm test -- InstrumentLookupPage.test.tsx --run --silent --reporter=dot`，30/30 通过；`npm run build` 含 TypeScript 检查通过；`git diff --check` 通过。
- 本地 Playwright 用固定数据检查 1440、390、320 px，三个宽度均无文档横向溢出、脚本错误，指标和来源完整显示。未跟踪截图为 `output/instrument-ratios-local-*.png`。

## 线上状态

- GitHub Actions 镜像构建：功能提交 run `36099802417`、修正提交 run `36100470644` 均 `completed/success`，生产机可读取对应 backend/frontend 两份镜像 manifest。
- 首次发布前备份 `backups/radar-before-e62f8cafb7f0-20260925T054654Z.db`，大小 601,034,752 字节，`integrity_check=ok`，容器和主机 SHA-256 一致：`70d82e5ee49ef883d5b31182c2d111183ab0f2b7663740f9f4b8976c9a530751`。
- 修正版本发布前备份 `backups/radar-before-c946aa4b3680-20260925T055617Z.db`，大小 600,178,688 字节，`integrity_check=ok`，容器和主机 SHA-256 一致：`321597b60ed5fa189e2299ec25f6d581695e1a2b42624a08aae8af6ea36f5995`。脚本按既有保留策略各清理一份到期的部署备份，没有删除数据库卷。
- 服务器先后用 `deploy/linux-update.sh` 的备份、`git pull --ff-only`、预构建镜像拉取和 `docker compose up -d --no-build --wait` 更新。最终生产仓库及前后端运行镜像均为 `c946aa4b3680bbde64c710948ae905a6215742aa`，容器 healthy；前后端 `/api/health` 均 `status=ok`。
- 生产接口 `/api/instrument-market-cap/BTC` 返回 Bitcoin 流通市值，`/api/instruments/BTCUSDT` 返回 15 个精确市场。经严格主机密钥校验的 SSH 隧道实测生产页面：BTC 在 1440/390 px 显示约 `0.0244x` 与 `21.9x`，合约成交额 9/9 市场、现货成交额 4/6 市场；ZETA 在 390 px 显示约 `0.183x` 与 `8.63x`，现货成交额 4/5 市场，1 个别名未计。三次浏览器检查均无横向溢出或脚本错误；未跟踪截图为 `output/instrument-ratios-production-*.png`。数值随市场刷新变化。

## 已知限制与后续

- Binance 和 Aster 的 BTC/ETH 现货快照目前没有 24h 成交额，合约/现货比仅按有数据的现货市场计算。CoinGecko 可能限流或缺失个别币种市值，页面会显示不可用；多候选的人工选择不会跨会话保存。
- 既有规范标的映射仍把 Bitget `RZETAUSDT` 放在 ZETA 的精确市场列表中，因而旧的价格区间等汇总仍可能受它影响。本任务只约束新增成交额比值，若要修正别名配置和其他汇总，需在单独模块任务中核实其真实资产身份与受影响调用链。
- 原有 `.worktrees/`、`backend/.pytest_module_prune_20260925/`、其他模块文档、`output/` 与 `script/` 未跟踪内容均未暂存或清理。本任务新增的本地/生产截图及 Playwright 验证脚本也保留在 `output/`，不提交。服务器登录方法已记录在 `docs/linux-deployment.md` 的 Windows Operator Access 小节。
