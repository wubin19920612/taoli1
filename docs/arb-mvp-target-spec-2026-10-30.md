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

## 实现记录

2026-10-30，工作树 `taoli1/.worktrees/arb-mvp-target`，分支 `codex/arb-mvp-target`，基线 `c0fad64b376a60232735c2def160b77ad497ef6c`。首次实现开始时工作区干净；续做时保留并接续本任务全部既有改动。下方首次验证和待决定段保留历史证据；PM 决定后的修复及最终三项验收见文末，均已通过。本任务仅创建指定消息的本地提交，不 push、不部署。

### 修改文件与实现范围

- `backend/app/core/config.py`、`.env.example`：四个 arb 配置项；示例值均为空。
- `backend/app/models/astro.py`：建卡目标类型与缺省 `astro`。
- `backend/app/services/astro_planner.py`：仅抽取公开阈值方法 `position_thresholds()`，保留原计算、舍入、说明和告警内容，原测试断言未修改。
- 新增 `backend/app/services/arb_mvp_planner.py`：FF、交易所、raw symbol/HL DEX、身份及尺寸校验，输出 arb pair。
- 新增 `backend/app/services/arb_mvp_client.py`：唯一写方法 `add_pair()`，紧凑 JSON 字节签名并原字节发送；10 秒超时，不重定向、不重试；未知结果提示人工核对。planner 和客户端均强制 `disableOpen=true`，不提供状态、修改或删除调用。
- `backend/app/api/routes_astro.py`：两种预览和两种人工建卡入口按 target 分流；arb 分支复用人工复核、仅提示深度风险、记录目标/卡名/结果/耗时，不调用 Astro 提交服务，不写 Astro 设置。
- 新增 `backend/app/api/routes_arb_mvp.py`、修改 `backend/app/main.py`：状态查询通过现有全局鉴权注册，仅返回配置布尔值及默认最大持仓。
- `backend/app/db/repositories.py`：新增独立设置键 `arb_mvp_default_max_position_usd`，初始/读取失败回退 100；与 Astro 默认设置隔离。
- `frontend/src/api/client.ts`、`frontend/src/api/types.ts`：目标类型、状态接口、预览 target 查询参数及建卡 payload。
- `frontend/src/pages/InstrumentLookupPage.tsx`、`frontend/src/pages/DashboardPage.tsx`：三档选择、每次打开默认 Astro、未配置禁用、切档重取预览、过滤过期预览响应、arb 隐藏路线/开仓开关、最大持仓默认值及切回恢复、STOPPED 提示、live 上架确认、结果及 warnings 展示。
- 新增 `backend/tests/test_arb_mvp.py`、`backend/tests/test_arb_mvp_routes.py`；在 `frontend/tests/InstrumentLookupPage.test.tsx`、`frontend/tests/DashboardPage.test.tsx` 添加测试。原有测试断言未修改。
- 本任务书追加实现记录及待 PM 决定事项。未修改任务书列出的禁止文件、部署脚本或 CI。

### Lighter raw symbol 核对

本次仅核对本地源码和离线测试，没有访问交易所。`backend/app/exchanges/lighter.py` 从 `orderBookDetails` 的 `order_book_details` 读取永续市场，构建快照时直接使用 `raw_symbol=str(item["symbol"])`；`market_id` 单独用于 WebSocket 订阅和盘口查询，不作为 raw symbol，也未向永续 raw symbol 拼接 USDT 等后缀。

`backend/tests/test_lighter_adapter.py` 的离线样例覆盖 `ETH`、`ANTHROPIC`、`OPENAI`，以及普通 Lighter / RH-Lighter 同名市场的不同 market ID 和端点隔离。现货样例有 `ETH/USDC`、`ETH/USDG`，但本任务只接受永续，现货在映射时阻断。因此本次永续映射直接传递 `orderBookDetails.symbol`，不猜测 market index 映射。该结论为源码和离线样例核对，不代表已核验线上市场目录。

### 测试命令与汇总

先写测试再实现：新增后端映射/客户端测试首次运行因 `ModuleNotFoundError: No module named 'app.services.arb_mvp_client'` 红灯，汇总 `1 error in 0.51s`；新增路由测试首次运行因缺少 `arb_paper_url` 配置红灯，汇总 `1 warning, 6 errors in 7.30s`。实现后定向测试通过；前端新测试经过 DOM 定位与测试文本编码修正后通过，未改原有业务断言。

后端全量（工作目录 `backend`，指定锁定依赖 venv）：

```powershell
& 'C:/Users/wubin/AppData/Local/Temp/claude/D-------claude---/7f276a81-4df2-4527-bc65-b410f9d557b6/scratchpad/venv-lock/Scripts/python.exe' -m pytest -q -p no:cacheprovider
```

```text
2338 passed, 6 deselected, 13 warnings in 118.30s (0:01:58)
```

6 个 deselected 来自仓库原有 `not network and not benchmark` 配置；未运行真实网络测试。warnings 为 Starlette/httpx 和 datetime.utcnow 弃用提示。

前端新交互定向测试（工作树根目录）：

```powershell
npm --prefix frontend test -- --run tests/DashboardPage.test.tsx tests/InstrumentLookupPage.test.tsx -t arb-mvp --cache=false
```

```text
Test Files  2 passed (2)
     Tests  4 passed | 61 skipped (65)
  Duration  7.86s
```

前端全量（工作目录 `frontend`）：

```powershell
npm test
```

```text
Test Files  1 failed | 27 passed (28)
     Tests  1 failed | 304 passed (305)
  Duration  87.21s
```

唯一失败为原有 `DashboardPage > labels exchange errors as external exchange API link failures`。未经修改原断言，单文件复测：

```powershell
npm test -- --run tests/DashboardPage.test.tsx
```

```text
Test Files  1 failed (1)
     Tests  1 failed | 20 passed (21)
  Duration  12.03s
```

前端类型检查（工作目录 `frontend`）：

```powershell
npx tsc -b
```

结果：退出码 0，无诊断输出。`git diff --check` 通过。

环境备注：`frontend/node_modules` 是指向主工作树依赖目录的 junction。最初沙箱内定向测试遇到 Vitest 缓存 `EPERM`，定向测试使用 `--cache=false`；最终原命令 `npm test` 经工具权限审核运行，缓存错误已消除。没有改依赖、WSL、生产或真实服务状态。前端全量仍有原有 React act/Ant Design 弃用提示。

## 待 PM 决定

**历史停止点（已按下方 PM 决定续做并解除）：前端全量测试未通过，未创建验收提交。**

- 原有 `frontend/tests/DashboardPage.test.tsx` 的健康展示测试，在全量和单文件运行中均失败于 `expect(screen.getByText("交易所外部 API 链路异常")).toBeTruthy()`；新增建卡测试及其他 304 项前端测试通过。
- 初步源码证据：该测试先等待 `交易所链路异常`，这是 Dashboard 初始就渲染的统计标题，随后同步断言依赖异步 `/health` 返回的告警。失败时告警尚未出现。本次没有修改该健康展示逻辑、数据加载逻辑或原断言；尚未进行隔离基线复测，不能断言是基线已有故障。
- 待决定：是否允许在本任务中诊断并修复这一非建卡测试的异步等待。可审阅的候选变更是仅将上述断言等待方式改为 `expect(await screen.findByText("交易所外部 API 链路异常")).toBeTruthy()`，保持文本、存在性以及随后交易所/错误详情断言全部不变；须先确认原因，再跑完整前端验收。或者由独立任务修复并回补基线。
- 按任务书的未覆盖问题停止规则，当前没有执行该候选修改，也没有调整超时、屏蔽测试、放宽断言或更改健康模块。待 PM 决定后继续验收，最终仍只创建一个指定消息的 commit，不 push、不部署。

## PM 决定（2026-10-30）

- PM 在干净基线 96456e2 上单独跑 `tests/DashboardPage.test.tsx`：19/19 通过。所以 `labels exchange errors as external exchange API link failures` 失败是**本次改动造成的回归**，不是原有故障。
- 不允许改这条旧测试（包括把 getByText 改成 findByText）。在本次改动里找根因并修实现。重点怀疑：Dashboard 挂载时新增的 `/api/arb-mvp/status` 请求或 target 相关 effect 改变了 `/health` 等请求的顺序/时序，或者打乱了测试的 fetch mock。可选修法：只在打开建卡弹窗时才请求 status（页面挂载时不请求），或者让新请求不影响现有数据加载。
- 修完后跑前端全量 `npm test`、`npx tsc -b`、后端全量，三项全过后按任务书创建一个 commit（消息不变），不 push。

## PM 决定后的实现与验收记录（2026-10-30）

### Dashboard 回归诊断及修复

- 原工作区完整 Dashboard 文件复现 `1 failed | 20 passed (21)`，失败仍为 `labels exchange errors as external exchange API link failures`。该旧测试正文与 HEAD 逐段比对完全一致；两份前端测试文件的 Git diff 均只新增用例，没有删除或修改原有测试行。
- 当前 `getArbMvpStatus()` 已经只在 `openAstroPreview()` 中调用，挂载时没有 arb status 请求，也没有 target effect 插入健康加载链。不能把 PM 提出的排查假设当成已证实的根因。
- 用临时副本对照 `96456e2` 的 Dashboard 页面及原 19 条测试，整文件 `19 passed (19)`；用同一份原 19 条测试对当前 Dashboard 实现，也为 `19 passed (19)`。此对照只替换页面/测试副本，复用当前依赖和其他模块，并非完整隔离 checkout。临时副本已删除，未纳入提交。
- 结合上述对照，回归在本次新增用例加入后的执行时序下出现，并非健康 API 被新增 status 请求替换。直接失败机制是：`health === null` 时，页面已渲染完成态统计标题“交易所链路异常”和数值 `0`；测试对该标题的等待可在 `/health` 返回前结束，下一条同步告警断言因此失败。单独筛选该用例时，基线页面副本也能暴露同一先后顺序，不能依赖某次运行恰好先完成健康请求。未证实某一个新增 hook 或静态确认框单独导致延迟，故没有围绕这些猜测保留改动。
- 修复落在 `DashboardPage.tsx`：健康数据尚未返回时，异常统计显示“交易所链路状态加载中”和 `-`；收到数据后再显示正式异常统计，与同一份 `health` 派生的告警一起渲染。既避免把未知状态显示为零异常，也消除了旧断言依赖的先后竞态。没有更改请求顺序、增加延时、修改旧断言或放宽测试。
- 新增延迟 `/health` 响应的回归用例：断言待加载时没有零异常完成态，挂载不请求 `/arb-mvp/status`，释放响应后统计值 `1`、告警和交易所详情同时可见。原有 arb 交互测试继续验证打开弹窗后的目标选择。
- 加入新回归用例前，修复后的 Dashboard 单文件为 `21 passed (21)`；加入后在最终全量中为 `22 passed (22)`。

### 最终验收命令与汇总

以下命令均在本工作树内执行。原始日志保留在 `C:/Users/wubin/AppData/Local/Temp/`；没有安装或更换依赖，也没有访问 WSL、生产、真实 Astro、真实 arb-mvp 或交易所。

前端全量（工作目录 `frontend`）：

```powershell
npm test
```

```text
Test Files  28 passed (28)
     Tests  306 passed (306)
  Duration  77.70s
EXIT_CODE=0
```

日志：`arb-mvp-frontend-full-final-20261030.log`。首次沙箱运行也是 `28 passed / 306 passed`，但 Vitest 写 junction 指向的 `node_modules/.vite/vitest/results.json` 时发生 `EPERM`，退出码 1；该次不计为完整验收。经工具权限审核允许本地缓存写入后，原样重跑上述命令，最终退出码 0。首次记录保留于 `arb-mvp-frontend-full-20261030.log`。

类型检查（工作目录 `frontend`）：

```powershell
npx tsc -b
```

```text
无诊断输出
EXIT_CODE=0
```

日志：`arb-mvp-tsc-20261030.log`。

后端全量（工作目录 `backend`，任务书指定的锁定依赖 venv）：

```powershell
& 'C:/Users/wubin/AppData/Local/Temp/claude/D-------claude---/7f276a81-4df2-4527-bc65-b410f9d557b6/scratchpad/venv-lock/Scripts/python.exe' -m pytest -q -p no:cacheprovider
```

```text
2338 passed, 6 deselected, 13 warnings in 135.93s (0:02:15)
EXIT_CODE=0
```

日志：`arb-mvp-backend-full-20261030.log`。6 项 deselected 仍来自仓库既有 `not network and not benchmark` 配置；13 条 warning 为 Starlette/httpx 和 `datetime.utcnow` 弃用提示。前端保留 React act、Ant Design 弃用等 stderr 输出；`useRadarStore` 用例期间还记录了 Ant Design cssinjs 的 `TypeError: Cannot read properties of undefined (reading 'content')`，Vitest 最终没有报告失败用例，退出码为 0。本次未扩展修改该测试环境问题。`git diff --check` 通过。

### 提交范围与交接

- 本次提交覆盖上文已有建卡实现及测试、四个空值示例变量、本次 Dashboard 回归修复和本任务书，共 19 个文件；禁止修改的 Astro 客户端、自动建卡服务、安全模块、部署脚本和 CI 无差异。
- 只创建一个消息为 `feat: arb-mvp paper/live target for manual card creation` 的提交。三项验证完成后再暂存这些明确路径；不 push，不部署，也不进行线上备份或线上验收。
- 本任务书兼作本模块的交接记录：代码入口、业务边界、Lighter raw symbol 本地证据、命令及验收结果均在上文。下一步由 PM 在独立任务中安排审阅、推送或部署；本地验证不代表生产服务配置或真实建卡已验收。
- 临时诊断页面/测试副本已清理；测试日志留在系统临时目录，不提交。未删除既有日志或其他任务产物。

## PM 决定 2（2026-10-30，审 f7230db 后）

1. **必须修**：arb-mvp 的 pair 校验（arb-mvp `src/pairs/schema.ts`，zod strictObject）要求 `maxPositionUsd` / `minOrderUsd` / `maxOrderUsd` / `openSpread` / `closeSpread` / `maxSlippage` 都是**十进制字符串**（不能有指数记法），`leg.leverage` 是整数，`name` 1–40 字符，`openSpread > closeSpread`，不允许多余字段。现在 `arb_mvp_planner.py` 传的是 float，真实 arb-mvp 会每次 400。改成十进制字符串（用 Decimal 格式化，不能出现 `1e-05`、`100.0` 可以写成 `100`）；`name` 超过 40 字符 → 阻断。
2. 加一个契约测试：断言 planner 产出的 pair 键集合恰好是 `{name,type,legA,legB,openSpread,closeSpread,disableOpen,disableClose,maxPositionUsd,minOrderUsd,maxOrderUsd,maxSlippage}`，各字段类型与上面一致，leg 键只允许 `{venue,dex,symbol,leverage}`。参考合法样例：`{"name":"ETH-HL-LIGHTER-LIVE1","type":"FF","legA":{"venue":"hyperliquid","symbol":"ETH","leverage":3},"legB":{"venue":"lighter","symbol":"ETH","leverage":3},"openSpread":"0.0015","closeSpread":"0.0003","disableOpen":true,"disableClose":false,"maxPositionUsd":"100","minOrderUsd":"15","maxOrderUsd":"25","maxSlippage":"0.002"}`。
3. 400 / 409 时在中文前缀后带上 arb-mvp 返回的 `message`（arb-mvp 是用户自己的系统，返回的是校验原因，例如 `Invalid config: legA.venue ...`），截断到 300 字符，去掉换行之外的控制字符；其他状态码保持现状。客户端测试相应更新（新加断言，不删旧的安全断言：key 和 x-sign 不出现在日志和返回里）。
4. 修完跑前端全量、`npx tsc -b`、后端全量，三项全过后新建一个 commit `fix: send arb-mvp sizes as decimal strings and surface rejection reasons`，不 push。

## PM 决定 2 的实现与验收记录（2026-10-30）

### 范围与实现

- 本轮基线 `f7230db`，工作树 `taoli1/.worktrees/arb-mvp-target`，分支 `codex/arb-mvp-target`。开始时仅本任务书的「PM 决定 2」未提交；完整保留该段。本轮仅修改两个 arb-mvp 实现文件、两个相关后端测试文件和本任务书。
- `backend/app/services/arb_mvp_planner.py`：三个尺寸字段通过 `Decimal(str(value))` 按定点格式输出，去掉小数部分末尾零，不输出指数记法，不额外舍入；名称经原 `_base_name()` 映射后校验 1–40 字符，越界阻断且无 pair/payload。开平阈值继续复用原 Decimal 六位小数算法及严格大小关系；`maxSlippage` 保持 `"0.002"`。
- `backend/tests/test_arb_mvp.py`：先补契约测试，核对 pair 精确键集合、leg 允许/必需键、六个十进制字符串、整数杠杆、布尔开关及 `openSpread > closeSpread`；覆盖主 DEX/`xyz`、live/paper、RH-Lighter、负/极小阈值、指数来源的大尺寸、尺寸小数精度及名称边界。
- `backend/app/services/arb_mvp_client.py`：仅对 400/409 在原中文前缀后附加字符串 `message`。保留 LF 换行，移除其余 Unicode Cc/Cf 控制/格式字符；遮蔽本次 API key、签名值及 `x-sign` 标记后，将原因截为最多 300 字符。无字符串原因时保持中文前缀；其他状态码、签名原始字节、禁止开仓及不重试行为保持原状。
- 客户端新增原因展示、截断、控制字符清理、凭据回显遮蔽、非字符串原因及其他状态码回归测试。原安全断言全部保留；`backend/tests/test_arb_mvp_routes.py` 仅将两处旧数值预期改为严格字符串预期（`"120"`/`"100"`），匹配 PM 新契约，未放宽断言。
- 未修改 Astro 实现、前端实现、依赖、部署脚本、CI 或任务书禁止文件。未访问 WSL、生产服务器、真实 Astro、arb-mvp 或交易所。本轮未发现需要新增「待 PM 决定」的问题。

### 先测试、后实现的证据

先修改上述两份测试并执行定向测试，确认红灯后才修改实现。工作目录为 `backend`：

```powershell
& 'C:/Users/wubin/AppData/Local/Temp/claude/D-------claude---/7f276a81-4df2-4527-bc65-b410f9d557b6/scratchpad/venv-lock/Scripts/python.exe' -m pytest -q -p no:cacheprovider tests/test_arb_mvp.py tests/test_arb_mvp_routes.py
```

```text
# 修改实现前
18 failed, 47 passed, 1 warning in 9.97s
EXIT_CODE=1
# 修改实现后
65 passed, 1 warning in 8.89s
EXIT_CODE=0
```

日志目录为 `C:/Users/wubin/AppData/Local/Temp/`。红灯原始日志 `arb-mvp-pm2-red-20261030.log` 保留；因 PowerShell 追加输出产生混合编码，另保留同次运行的可读转码副本 `arb-mvp-pm2-red-20261030-utf8.log`，未重跑或覆盖红灯证据。定向通过日志为 `arb-mvp-pm2-targeted-20261030.log`。日志均包含命令、汇总和退出码。

### 三项全量验收

前端全量（工作目录 `frontend`）：

```powershell
npm test
```

```text
Test Files  28 passed (28)
     Tests  306 passed (306)
  Duration  78.32s
EXIT_CODE=0
```

日志：`arb-mvp-pm2-frontend-full-final-20261030.log`。首次沙箱运行用例同样全部通过（`28 passed / 306 passed`，`93.04s`），但写入 `node_modules/.vite/vitest/results.json` 遇到 `EPERM`，退出码 1，不计为验收通过；该日志保留在 `arb-mvp-pm2-frontend-full-20261030.log`。按本任务书已记录的 junction 缓存权限处理，经工具权限审查允许缓存写入后，原样重跑 `npm test` 获得以上退出码 0。

类型检查（工作目录 `frontend`）：

```powershell
npx tsc -b
```

```text
无诊断输出
EXIT_CODE=0
```

日志：`arb-mvp-pm2-tsc-20261030.log`。

后端全量（工作目录 `backend`，指定锁定依赖 venv）：

```powershell
& 'C:/Users/wubin/AppData/Local/Temp/claude/D-------claude---/7f276a81-4df2-4527-bc65-b410f9d557b6/scratchpad/venv-lock/Scripts/python.exe' -m pytest -q -p no:cacheprovider
```

```text
2368 passed, 6 deselected, 13 warnings in 158.08s (0:02:38)
EXIT_CODE=0
```

日志：`arb-mvp-pm2-backend-full-20261030.log`。6 项 deselected 仍来自仓库的 `not network and not benchmark` 配置；13 条 warnings 为 Starlette/httpx 及 `datetime.utcnow` 弃用提示。前端日志保留 React act、Ant Design 弃用提示及任务书已有记录的 cssinjs `TypeError: Cannot read properties of undefined (reading 'content')` stderr；最终 Vitest 无失败用例、退出码 0，未扩大范围修改这些既有提示。

### 本地交付与交接

- 三项验收全部通过；`git diff --check` 通过。复核仅本轮五个文件的差异后，按消息 `fix: send arb-mvp sizes as decimal strings and surface rejection reasons` 新建一个本地提交；不 amend、不 push、不部署。
- 原始测试日志留在上述临时目录，不纳入提交。本任务书继续作为交接记录，既有 PM 决定及历史验收记录完整保留。
- 本轮完成 PM 决定 2 的四条范围；真实服务契约/建卡、生产配置及线上运行未做验收。后续审阅、推送或部署由 PM 在新任务安排。

## PM 决定 3（2026-10-30，只读审查 4a540c0 的两个 P2）

审查日志：`output/arb-mvp-target-review-20261030.log`。
1. `backend/app/services/arb_mvp_client.py:63`：`httpx.InvalidURL` 等非 `HTTPError` 异常穿透到建卡主流程（错误 URL 如 `http://arb.test:bad` 复现 500）。改为：构造请求前校验 URL（http/https、有 host），非法 → 失败"arb-mvp 地址配置错误"；发送阶段捕获所有 `Exception`（不吞 `asyncio.CancelledError`）→ UNKNOWN_RESULT。建卡路由任何情况下不返回 500。加测试（错误 URL、transport 抛任意异常各一例，断言只请求 ≤1 次）。
2. `frontend/src/pages/DashboardPage.tsx:719`、`InstrumentLookupPage.tsx:1585`：Astro(10)→paper(150)→Astro 预览未完成→live→Astro 的切换顺序会把 arb 尺寸 150 缓存成 Astro 尺寸并提交。改为：Astro 尺寸与 arb 尺寸分开保存，只在"当前显示的是 Astro 档且 Astro 预览已就绪"时更新 Astro 缓存；提交时以当前 target 和对应已就绪预览为准，预览未就绪禁止提交。两页各加一个复现该顺序的测试。
3. 跑前端全量、`npx tsc -b`、后端全量，全过后新建 commit `fix: harden arb-mvp client errors and target switch sizing`，不 push。


## PM 决定 3 的实现与验收记录（2026-10-30）

### 范围与实现

- 基线 `4a540c0`，分支 `codex/arb-mvp-target`，工作树 `taoli1/.worktrees/arb-mvp-target`。开始时仅本任务书末尾的「PM 决定 3」未提交，完整保留该内容。本轮仅处理该决定的三条，不 push、不部署。
- `backend/app/services/arb_mvp_client.py`：构造请求前用 `httpx.URL` 解析并检查 http/https 及 host；非法 URL（包括非法端口）直接返回「arb-mvp 地址配置错误」。发送及响应解码阶段捕获 `Exception`，统一返回 `UNKNOWN_RESULT`，不重试、不泄漏异常内容；`asyncio.CancelledError` 继续向上传播。
- 两页分别保存 Astro/arb 的金额、杠杆和最小/最大订单尺寸；只在当前 target 的预览已就绪且有 pair 时保存对应表单缓存。打开弹窗重置两份缓存；切档立即清除预览就绪状态，最新响应填入对应缓存后才就绪。提交按钮及提交入口均校验就绪 target，表单异步校验后再次核对预览请求编号，继续过滤过期响应。
- 两页各新增一个完整复现：Astro(10) → paper(150) → Astro 预览挂起 → live → Astro；挂起期间不能提交，旧 Astro 响应不能解除禁用，最终实际发送 `target=astro, max_trade_usdt=10`。
- 后端新增非法 URL、任意 transport 异常及取消传播测试；非法 URL 不触发 transport，任意异常只触发一次。两个建卡入口 × paper/live × URL/transport 故障共八种路由组合，验证返回 HTTP 200、业务 `status=failed`，不调用 Astro 服务。
- 原有测试断言未修改；未修改禁止文件、依赖、部署脚本或 CI，未访问 WSL、生产服务器、真实 Astro、arb-mvp 或交易所。

### 先失败、后修复的记录

日志均保留在 `C:/Users/wubin/AppData/Local/Temp/`，以下命令分别在本工作树 `frontend`、`backend` 执行。

前端先增加复现测试，在实现改动前执行：

```powershell
npm test -- tests/DashboardPage.test.tsx tests/InstrumentLookupPage.test.tsx -t 'keeps Astro sizing'
```

```text
Test Files  2 failed (2)
     Tests  2 failed | 66 skipped (68)
AssertionError: expected 150 to be 10 // Object.is equality
EXIT_CODE=1
```

两页均在实际提交 payload 的金额断言失败。原始日志 `arb-mvp-pm3-frontend-red-20261030.log` 及同次运行的可读转码副本 `arb-mvp-pm3-frontend-red-20261030-utf8.log` 均保留；这次运行末尾另有已知 junction 缓存写入 `EPERM`，不影响上述两个用例确实失败的证据。修复后使用同一筛选加 `--cache=false` 定向检查，`2 passed | 66 skipped (68)`、退出码 0，日志 `arb-mvp-pm3-frontend-targeted-20261030.log`；正式全量仍按原始 `npm test` 命令执行。

后端先新增测试，再在实现改动前执行：

```powershell
& 'C:/Users/wubin/AppData/Local/Temp/claude/D-------claude---/7f276a81-4df2-4527-bc65-b410f9d557b6/scratchpad/venv-lock/Scripts/python.exe' -m pytest -q -p no:cacheprovider tests/test_arb_mvp.py tests/test_arb_mvp_routes.py
```

```text
13 failed, 66 passed, 1 warning in 11.09s
EXIT_CODE=1
```

原始日志 `arb-mvp-pm3-backend-red-20261030.log` 及可读转码副本保留。首次修复后定向检查发现生成测试时 PowerShell 编码导致 UNKNOWN_RESULT 的中文预期损坏（`5 failed, 74 passed`），记录在 `arb-mvp-pm3-backend-targeted-20261030.log`；修正测试文本后，临时以 HEAD 原始客户端重跑上述命令，并在 finally 恢复修复版客户端，独立确认：

```text
13 failed, 66 passed, 1 warning in 13.13s
EXIT_CODE=1
```

此确认日志为 `arb-mvp-pm3-backend-red-confirmed-20261030.log`，包含完整命令和基线实现说明。失败分别来自四种 URL、任意 transport 异常及八个路由组合；取消传播用例原本即通过。修复版随后进入下述后端全量验收。

### 三项全量验收

前端（工作目录 `frontend`）：

```powershell
npm test
```

```text
Test Files  28 passed (28)
     Tests  308 passed (308)
  Duration  84.70s
EXIT_CODE=0
```

日志：`arb-mvp-pm3-frontend-full-20261030.log`。按本任务书既有 junction 缓存权限处理，经工具权限审查允许写入 Vitest 缓存后执行完整原命令。

类型检查（工作目录 `frontend`）：

```powershell
npx tsc -b
```

```text
无诊断输出
EXIT_CODE=0
```

日志：`arb-mvp-pm3-tsc-20261030.log`。


后端全量（工作目录 `backend`，任务书指定锁定依赖 venv）：

```powershell
& 'C:/Users/wubin/AppData/Local/Temp/claude/D-------claude---/7f276a81-4df2-4527-bc65-b410f9d557b6/scratchpad/venv-lock/Scripts/python.exe' -m pytest -q -p no:cacheprovider
```

```text
2382 passed, 6 deselected, 13 warnings in 126.30s (0:02:06)
EXIT_CODE=0
```

日志：`arb-mvp-pm3-backend-full-20261030.log`。6 项 deselected 来自仓库既有 `not network and not benchmark` 配置；13 条 warnings 为 Starlette/httpx 与 `datetime.utcnow` 弃用提示。前端 stderr 原样保留，最终 Vitest 无失败用例、退出码 0。

### 本地交付与交接

- 三项全量验收全部通过，`git diff --check` 通过。复核差异仅涉及本轮八个文件，按指定消息 `fix: harden arb-mvp client errors and target switch sizing` 新建一个本地 commit；不 amend、不 push、不部署。
- 本任务书继续作为交接记录；既有 PM 决定、历史实现记录及原始日志完整保留，日志不纳入提交。本轮没有发现需新增「待 PM 决定」的问题。
- 验收限于本地离线测试，未验证生产配置或真实建卡。下一步如需审阅、推送或部署，由 PM 另开任务安排；本轮在此停止。
