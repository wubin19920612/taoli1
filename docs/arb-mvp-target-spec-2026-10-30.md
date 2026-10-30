# 建卡三档开关：Astro / arb-mvp 模拟盘 / arb-mvp live（2026-10-30）

## 背景
用户 10-29 决定：taoli1 人工建卡处加三档开关。`Astro` 走原逻辑（一行都不改行为）；`arb-mvp 模拟盘` / `arb-mvp live` 调同机 arb-mvp 服务的签名管理 API 建卡。arb-mvp 是另一个仓库的两腿价差执行系统，只支持 hyperliquid / lighter（模拟盘另有 lighter-rh）、永续-永续。
**真钱边界（硬性）**：taoli1 对 arb-mvp 只能"上架"卡片：只调 `POST /api/pairs` 且 `action: "add"`，payload 固定 `disableOpen: true`。不得调用任何改状态、改配置、删除的接口，任何情况下不得传 `disableOpen: false`。arb-mvp 新卡一律 STOPPED，开始交易由用户在 arb-mvp 界面操作。

## arb-mvp 接口（已上线，不可改）
- 地址：环境变量 `ARB_PAPER_URL`（生产 `http://172.18.0.1:8787`）、`ARB_LIVE_URL`（`:8790`）；密钥 `ARB_PAPER_API_KEY`、`ARB_LIVE_API_KEY`。在 `backend/app/core/config.py` 加 `arb_paper_url / arb_paper_api_key / arb_live_url / arb_live_api_key: str = ""`。URL 或 key 为空 = 该档未配置。密钥不得出现在日志、异常信息、接口返回里。
- 请求：`POST {url}/api/pairs`，body 为紧凑 JSON `{"action":"add","pair":{...}}`（签名用的字节必须就是发送的字节）。头：`content-type: application/json`、`x-timestamp`（毫秒）、`x-nonce`（`secrets.token_hex(12)`）、`x-sign` = 小写 hex(HMAC-SHA256(key, f"{ts}\n{nonce}\nPOST\n/api/pairs\n" + body))。
- 返回：成功 HTTP 200 `{"code":0,"data":{...,"config":{"name":...}}}`；409 `DUPLICATE_NAME` / `LIMIT_EXCEEDED`、400 配置错误、401 签名错误、429 限频，错误体 `{"code":-1,"message":"..."}`（可能还带 error code 字段，按实际返回宽松解析）。
- 客户端（新文件 `backend/app/services/arb_mvp_client.py`）只提供 `add_pair(pair: dict) -> 结果` 一个写方法。httpx 异步，超时 10 秒，不跟随重定向，**不重试**（add 不是幂等的）。超时/连接错误/无法解析的返回 → 失败，提示"结果未知，请到 arb-mvp 界面确认是否已建卡，不要直接重试"。

## 卡片映射（新文件 `backend/app/services/arb_mvp_planner.py`）
输入：经过人工复核的 `Opportunity`（与 Astro 档相同的 `_review_manual_opportunity` 结果）、目标档、生效的尺寸参数。输出与 Astro 预览同结构的 `AstroPairPlan`（`pair` = arb-mvp pair，`sdk_payload` = `{"action":"add","pair":pair}`，`card_variant="both"`，`route_variants=[]`），阻断项放 `blockers`，`can_submit = not blockers`。

| arb-mvp 字段 | 取值 |
| --- | --- |
| `name` | 与 Astro 相同的 `_base_name(opportunity.symbol)`；重名由 arb-mvp 返回 409，taoli1 原样提示，不自动改名重试 |
| `type` | `"FF"` |
| `legA` / `legB` | 买入腿（做多）/ 卖出腿（做空）：`{"venue", "symbol", "leverage"}`，HL 非主 DEX 时加 `"dex"` |
| `openSpread` / `closeSpread` | 与 Astro 完全相同的算法得到的百分比 ÷ 100，6 位小数字符串（即 Astro 非 FR 卡的 openPosition / closePosition） |
| `maxPositionUsd` | 本次填写的 `max_trade_usdt`；未填时用 arb-mvp 档默认值（初始 100） |
| `minOrderUsd` / `maxOrderUsd` | 本次填写的 `min_notional` / `max_notional`；未填时用已保存的 Astro 设置 |
| `disableOpen` / `disableClose` / `maxSlippage` | 固定 `true` / `false` / `"0.002"` |

- 交易所：taoli1 `hyperliquid`→`hyperliquid`，`lighter`→`lighter`，`rh-lighter`→`lighter-rh`。live 只允许 hyperliquid、lighter；模拟盘允许三者。其余交易所 → 阻断（"arb-mvp <档> 不支持 <交易所>"），**不得**静默改走 Astro。
- 两腿都必须是永续（FUTURE）；FR（两腿基础币不同）、SF/FS → 阻断。
- symbol：取 raw symbol，去掉 `"<dex>:"` 前缀；`dex` 只对 hyperliquid 且前缀存在时传（主 DEX 不传）。缺 raw symbol → 阻断。**先核对** taoli1 的 Lighter raw symbol 是否就是 Lighter 市场符号（如 `ETH`、`XAU`，arb-mvp 用 Lighter `orderBookDetails` 的 `symbol`）；如果是 market index 或带后缀，停下报告，不要自己猜映射。
- 尺寸校验（阻断）：`minOrderUsd >= 10`（HL 最小名义额）、`0 < minOrderUsd <= maxOrderUsd <= maxPositionUsd`、杠杆为 1–100 的整数。
- 身份核验：与 Astro 人工建卡相同（`identity_enforced` 且核验不通过 → 阻断）。**不要**套用 Astro 的路由限制（"Lighter 仅支持与已知 GC 交易所…配对"只属于 Astro）。
- 阈值：在 `AstroPairPlanner` 里把开/清阈值计算抽成一个公开方法供两边共用；Astro `plan()` 的输出必须逐字节不变（现有测试保护，不得改断言）。

## 后端接口
- `AstroCardCreateRequest` 加 `target: Literal["astro","arb_paper","arb_live"] = "astro"`。两个预览接口（`/preview/{id}`、`/instrument/preview`）加查询参数 `target`（默认 astro）。
- `target == "astro"`：现有代码路径完全不变。
- arb 档建卡：复核 → 映射（有阻断返回 `status="skipped"`, `action="market_validation"` 或 `"arb_mvp_mapping"`）→ 深度检查只追加提示（复用 `_validate_order_book_before_create`）→ `add_pair`。不调用 Astro service、不改 `service.card_settings`、不保存 Astro 设置。返回 `AstroAlertActionResult`：`action="arb_mvp_paper_add"` / `"arb_mvp_live_add"`，成功 `status="created"`、`pair_name`、`pair_type="FF"`；失败 `status="failed"` + 中文原因。未配置的档 → `enabled=false, status="disabled"`。
- `save_as_default` 在 arb 档只保存 arb-mvp 档默认最大持仓（新设置项，存在现有 settings 表/仓库里，读不到时默认 100），不碰 Astro 设置。
- 新接口 `GET /api/arb-mvp/status` → `{"paper":{"configured":bool},"live":{"configured":bool},"default_max_position_usd":float}`（不返回 URL 和 key；受 S0 全局鉴权保护，不进公开白名单）。
- 日志：每次 arb 建卡记一行（目标档、卡名、结果、耗时），不记 body 里的 key/签名头。

## 前端（`DashboardPage.tsx`、`InstrumentLookupPage.tsx`、`api/client.ts`、`api/types.ts`）
- 建卡弹窗顶部加 `Radio.Group`「建卡目标」：Astro / arb-mvp 模拟盘 / arb-mvp live；未配置的档禁用（读 `/api/arb-mvp/status`）。每次打开弹窗默认 Astro，不记忆。
- 切换目标后用该 target 重新请求预览，显示该档的阻断项和 pair。
- arb 档：隐藏「建卡路线」和开仓开关；`max_trade_usdt` 标签改"最大持仓 (USD)"，预填 arb-mvp 默认值（切回 Astro 恢复 Astro 值）；固定提示"arb-mvp 卡片建好后是 STOPPED、禁止开仓，需要在 arb-mvp 界面手动开始交易"。
- live 档提交前弹确认框："将在 arb-mvp live（主网）上架卡片 <名称>，不会下单"。
- 提交时 payload 带 `target`；结果按现有方式展示（成功/失败消息 + warnings）。

## 测试（必须）
- 映射：HL 主 DEX、HL `xyz:GOLD`（带 dex）、lighter、模拟盘 rh-lighter→lighter-rh、live rh-lighter 阻断、bitget 阻断、SF/FR 阻断、minOrderUsd<10 阻断、阈值 = Astro 值 ÷ 100、`disableOpen` 恒为 true（即使请求 `open_enabled=true`）。
- 客户端：固定 ts/nonce 的签名向量（测试里用 hmac 独立重算）、签名字节=发送字节、409/400/401/429 映射为中文失败、超时只请求 1 次且提示结果未知、日志（caplog）里没有 key 和 x-sign。
- 路由：target 缺省时走原 Astro 路径（原测试不改）；arb 档不调用 Astro service；未配置 → disabled；预览 target 生效；status 接口不含 key。
- 前端：切换目标重新预览、arb 档隐藏路线、payload 带 target、live 确认框、未配置禁用。
- 现有测试一律不放宽。

## 范围
可改：`backend/app/core/config.py`、`backend/app/models/astro.py`（或新建 `models/arb_mvp.py`）、`backend/app/services/astro_planner.py`（只抽阈值方法）、新建 `arb_mvp_client.py`、`arb_mvp_planner.py`、`backend/app/api/routes_astro.py`、新路由文件（并在 main.py 注册）、settings 仓库（只加新设置项）、上面列出的前端文件、相关测试、`.env.example`（如有，只加四个变量名，值留空）、本任务书。
**不可改**：`astro_client.py`（另一个分支 S3a 在改）、`astro_alerts.py`、`fs_astro.py`、`security.py`、部署脚本、CI。
commit 信息 `feat: arb-mvp paper/live target for manual card creation`。不要 push，不要部署。

## 测试环境（必须）
后端全量用锁定依赖：在 `backend` 目录下 `C:\Users\wubin\AppData\Local\Temp\claude\D-------claude---\7f276a81-4df2-4527-bc65-b410f9d557b6\scratchpad\venv-lock\Scripts\python.exe -m pytest -q -p no:cacheprovider`。前端在 `frontend` 目录 `npm test` 和 `npx tsc -b`。日志里保留命令和汇总行。不要运行 WSL，不要访问生产服务器、真实 Astro、真实 arb-mvp 或交易所。
遇到本任务书没覆盖、需要改不可改文件、或映射事实与本书不符时：停下，把问题写在本文件末尾「待 PM 决定」段，不要自行扩大范围。
