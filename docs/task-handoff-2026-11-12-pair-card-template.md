# 价差页推荐开平模板与快捷建卡/改卡

日期：2026-11-12。工作树：`.worktrees/pair-card-template`；分支：`codex/pair-card-template`；基线：`58581df57816d303266215cf097ac0a4df531410`。

本任务只处理价差查询页推荐模板及 Astro 建卡/改卡。用户明确要求只做本地 Git 提交，禁止 push、部署和 WSL。线上状态未改变，未备份或连接生产服务器，也未实际写入外部 Astro。

## 已实现与入口

| 文件 | 作用 |
| --- | --- |
| `backend/app/models/astro.py` | 开平阈值覆盖模型、预览与更新请求；成对、有限值、绝对值不超过 50、开大于平校验 |
| `backend/app/services/astro_planner.py` | 覆盖值沿用 decimal/ratio 转换与严格大小规则；反算百分比；立即开仓提示 |
| `backend/app/services/astro_alerts.py` | 创建链路传递覆盖值；公开并复用原有按卡名锁 |
| `backend/app/api/routes_astro.py` | 预览覆盖、实时查卡、乐观并发改卡；独立更新日志 |
| `backend/tests/test_pair_card_template.py` | 39 项新增后端测试 |
| `frontend/src/api/types.ts` | 阈值覆盖及查卡响应类型 |
| `frontend/src/api/client.ts` | 预览覆盖与查卡、更新请求 |
| `frontend/src/components/astroCard/AstroCardCreateModal.tsx` | 从标的查询页抽出的共用预览/确认 UI；展示模板百分比及实际 SDK 开平值 |
| `frontend/src/components/pairTemplate/recommend.ts` | 分位、方向、成本、最小间距、资金费调整、状态机回测和独立样本外拟合 |
| `frontend/src/components/pairTemplate/recommend.test.ts` | 推荐算法 8 项单测 |
| `frontend/src/components/pairTemplate/PairTemplateCard.tsx` | 三档展示、手调、手续费持久化、查卡、确认创建/修改 |
| `frontend/src/components/pairTemplate/PairTemplateCard.test.tsx` | 按钮边界、预览后确认、expected 值、跨资产与 DEX 保留测试 |
| `frontend/src/components/pairTemplate/pairTemplate.css` | 三列模板卡及窄屏单列布局 |
| `frontend/src/components/pairTemplate/chartLines.test.tsx` | 原版 SVG 虚线联动测试 |
| `frontend/src/components/pairChartV2/PairChartV2.tsx` | 新版虚线、比例轴转换、自动缩放纳入模板阈值 |
| `frontend/src/components/pairChartV2/PairChartV2.test.tsx` | 新版阈值更新、旧线移除与缩放测试 |
| `frontend/src/pages/PairMonitorPage.tsx` | 摘要下方挂载面板；新旧图接入选中模板 |
| `frontend/src/pages/InstrumentLookupPage.tsx` | 使用共用弹窗，保留原有创建控制器及 arb-mvp 流程 |
| `frontend/src/pages/DashboardPage.tsx` | 仅调整表单类型，避免新增可选阈值被 `Required` 变成必填；不改变页面行为 |
| `docs/task-handoff-2026-11-12-pair-card-template.md` | 本交接与验证报告 |

## 重要业务规则

- 推荐使用完整查询区间中位数确定方向；负中位数交换多空方向，并以 `-close_exec` / `-open_exec` 重算当前可成交开平差。
- 三档分别为 Q85/Q40、Q75/Q40、Q65/Q45，默认均衡。半边成本 `h` 来自当前盘口，不可用时为 0.05%；往返手续费默认 0.16%，最小净利默认 0.15%。
- 资金费复用 `pairSummary/metrics.ts`，按实际方向取正负。先以基础间距回测取得已平仓交易的平均持仓时间，再一次性增加不利资金费间距；没有已平仓交易时平均持仓为 0，不进行循环拟合。
- 样本内：使用全查询区间拟合并回测。样本外：仅使用前 2/3 拟合方向、分位、基础持仓时长，在后 1/3 从空仓回测；单独显示训练阈值和方向，避免未来方向信息泄漏。
- 手调只更新所选模板、图线和样本内结果；样本外保留训练得出的阈值及结果，并明确标注，不能把手调结果冒充样本外。
- 回测单笔净收益按任务书扣除往返手续费，不包含资金费实收实付；固定使用当前盘口成本。资金费缺失显示按 0 估算。末尾未平仓的浮盈亏单列，不并入已平仓总收益。
- 数据需至少 120 个有效、不同时间戳点且覆盖 6 小时。无推荐或阈值非法均禁用写操作。`leg2_multiplier != 1` 时两个写按钮禁用。
- 建卡必须先预览再确认；原始市场与 HL DEX 原样传递；查询腿 symbol 或快照 symbol 不同即显式 `custom_cross_asset`。
- 查卡调用非缓存 `list_pairs()`，按现有 `_same_route` 匹配建卡的原生/GC 路线；多条时拒绝修改，不任取第一条。
- 修改在建卡同一把按卡名锁内重新查远端卡，检查 expected 开平值，仅替换 `openPosition` 与 `closePosition`；其余字段原样回传。遵守人工写开关和 dry-run，并记录 `astro_card_update` 日志。

## 实际验证命令与结果

工作树根目录运行的后端命令：

| 命令 | 实际结果 |
| --- | --- |
| `pytest backend/tests/test_astro_planner.py backend/tests/test_astro_instrument_routes.py backend/tests/test_astro_alerts.py -q -p no:cacheprovider --basetemp .tmp-pair-template-initial` | `251 passed in 24.33s` |
| `pytest backend/tests/test_pair_card_template.py -q -p no:cacheprovider --basetemp .tmp-pair-template-new` | 初版 `37 passed in 20.97s` |
| `pytest backend/tests/test_pair_card_template.py -q -p no:cacheprovider --basetemp .tmp-pair-template-extra` | 新增 ratio/HL 用例后 `39 passed, 1 error`；HL 目录查询未模拟，已修复 |
| `pytest backend/tests/test_pair_card_template.py -q -p no:cacheprovider --basetemp .tmp-pair-template-final-new` | 修正后 `39 passed in 24.53s` |
| `pytest backend/tests -q -p no:cacheprovider --basetemp .tmp-pair-template-backend-full` | `2 failed, 3234 passed, 11 deselected, 12 warnings, 1 error in 483.42s (0:08:03)`；HL 用例已修正，另外两项为根目录启动导致子进程导入错误 |
| `pytest backend/tests -q -p no:cacheprovider --basetemp .tmp-pair-template-backend-final` | `2 failed, 3234 passed, 11 deselected, 12 warnings in 456.46s (0:07:36)`；仅剩下述根目录子进程导入问题 |

从根目录启动时，`test_memory_probe.py` 的两个 `python -c` 子进程导入根目录 `app.py`，随后报 `ModuleNotFoundError: No module named 'src'`。未修改无关代码，改从 `backend/` 启动复验：

| 命令（cwd=`backend/`） | 实际结果 |
| --- | --- |
| `pytest tests/test_memory_probe.py -q -p no:cacheprovider --basetemp ../.tmp-pair-template-memory-probe` | `38 passed, 1 deselected in 17.93s` |
| `pytest tests -q -p no:cacheprovider --basetemp ../.tmp-pair-template-backend-cwd` | `3236 passed, 11 deselected, 12 warnings in 323.62s (0:05:23)`，退出码 0 |

`backend/pyproject.toml` 默认排除 `network` 和 `benchmark`，因此保留 deselected 数量；不擅自执行真实网络测试。

前端命令均在 `frontend/` 执行，Windows 使用 `npx.cmd` / `npm.cmd`：

| 命令 | 实际结果 |
| --- | --- |
| `npx.cmd tsc --noEmit` | 最终退出码 0，无 TypeScript 诊断 |
| `npx.cmd vitest run src/components/pairTemplate src/components/pairChartV2` | `6 passed (6)` / `49 passed (49)`，但结束写链接目录的缓存被拒绝，退出码 1 |
| `npx.cmd vitest run --no-cache` | 最终 `Test Files 38 passed (38)` / `Tests 521 passed (521)`；`Duration 221.03s` |
| `npx.cmd vitest run --no-cache src/components/pairTemplate/chartLines.test.tsx` | 初次测试误断言三位小数而原版实际为两位；修正断言后已包含在最终全量 521 项中通过 |
| `npx.cmd vitest run --no-cache src/components/pairChartV2/PairChartV2.test.tsx` | 自动缩放增补后的 `1 passed (1)` / `11 passed (11)` |
| `npm.cmd run build` | Vite 尝试在链接依赖目录 `.vite-temp` 写临时配置，被工作区权限拒绝 |
| `npm.cmd run build -- --configLoader runner` | 最终退出码 0，`✓ built in 12.67s` |
| `git diff --check` | 通过 |

标的查询页原有 `InstrumentLookupPage.test.tsx` 的 45 项测试全部通过。中途类型检查发现的共用表单类型和图表回调类型问题已修正。最初在根目录误执行 `npx.cmd tsc --noEmit -p frontend/tsconfig.json`，未找到本地 tsc 并触发 npm 缓存权限错误；随后统一使用前端工作目录中的现有依赖。没有安装或修改依赖链接。

## 未做、简化与验收边界

- 未 push、未部署、未运行 WSL；没有线上版本、备份或真实 Astro 写入验收。
- 新版与原版虚线均已实现，没有采用任务书允许的“只做新版图”简化。
- 修改卡片复用原有建卡锁并记录独立日志；没有新增更新尝试账本记录，采用任务书允许的最低要求“不绕过锁”。
- 资金费调整采用上述一次估算；不是逐笔资金费历史现金流回测。当前 h 固定用于历史回测，属于任务书指定的近似模型。
- 原有创建控制器保留在标的查询页，共用组件抽出的是完整预览/表单/确认弹窗；价差页使用自己的请求状态并复用同一 UI。
- 未进行真实浏览器人工视觉验收；本地组件交互、图表模拟 API、HTTP 测试及生产构建已验证。
- 测试仍有既有 Ant Design 弃用 / React act 与 Python 弃用提示；具体汇总以日志为准。

## 工作区与下一步

- 用户提供的 `docs/pair-card-template-spec-2026-11-12.md` 原先未跟踪，保持原样，不纳入本次提交。
- 本任务生成的 `.tmp-pair-template-*` 测试目录和日志，以及前端构建产物，仅供本地诊断，不纳入提交。主要日志：`.tmp-pair-template-vitest-final.log`、`.tmp-pair-template-pytest-backend-cwd.log`、`.tmp-pair-template-build-final.log`。
- 本任务交付后如需上线或继续其他模块，应在新任务中以本分支提交为起点，明确授权 push/部署；不能把本地提交视为已上线。

## a20e9a8 后续两项修复（2026-11-12，11-13 复验）

本次修复基于 `a20e9a8fcfd31a9c634ee818f5c99b391a05f548`，仍在 `codex/pair-card-template` 分支及原工作树。范围仅为手调阈值重置和改卡立即开仓提示；用户要求本地提交，提交说明为 `fix: 模板方向变化重置手调值、改卡立即开仓提示`，禁止 push 和 WSL，不部署。

- `frontend/src/components/pairTemplate/PairTemplateCard.tsx`：手调阈值绑定查询两腿（含 DEX）、倍率、查询时长、采样间隔、历史起止时间和推荐方向；任一上下文变化时清空全部模板手调值，返回原上下文也不恢复旧手调。渲染提交前即使用新推荐值，防止旧阈值进入反向图线或操作。相同上下文内更新盘口、历史点或推荐值仍保留手调；手续费、最小净利和模板选择行为保持原样。
- `backend/app/api/routes_astro.py`：改卡成功后，若请求的新开仓百分比小于或等于本次路线的当前可成交开仓价差，返回 warning `当前价差已达到开仓线，修改后可能立即开仓`；修改继续执行，原有锁、冲突检查、阈值编码、日志和其他卡片字段均沿用。
- 前端修改成功提示同时展示响应 message 和全部 warnings，缺少 warnings 时按空数组处理。
- `frontend/src/components/pairTemplate/PairTemplateCard.test.tsx`：增加方向反转、交易对、DEX、时长、采样间隔、窗口起止、倍率变化及返回原查询时的重置测试，同时验证全部模板输入和选中图线；增加相同查询刷新保留手调和修改成功显示 warnings 的测试。文件合计 21 项。
- `backend/tests/test_pair_card_template.py`：增加开仓线低于、等于和高于可成交价差的三项边界测试；同时确认 warning 不阻止实际修改且其他字段原样保留。文件合计 42 项。

本次验证：

| 工作目录 | 命令 | 汇总 |
| --- | --- | --- |
| `frontend/` | `npx.cmd tsc --noEmit` | 退出码 0，无诊断 |
| `frontend/` | `npx.cmd vitest run --no-cache` | 最终 `Test Files 38 passed (38)` / `Tests 531 passed (531)`，`Duration 201.73s`，原生退出码 0 |
| `frontend/` | `npx.cmd vitest run --no-cache src/components/pairTemplate/PairTemplateCard.test.tsx` | `Test Files 1 passed (1)` / `Tests 21 passed (21)` |
| `backend/` | `pytest tests/test_pair_card_template.py -q -p no:cacheprovider --basetemp ../.tmp-fix` | `42 passed in 51.12s`，退出码 0 |

先补回归测试并确认原实现失败，再实现修复。首次类型检查发现可选 warnings 需要空数组回退，已修复并复验类型检查。前端首轮测试断言全通过，但 PowerShell 的 `*>` 重定向把既有 stderr 记成 `NativeCommandError`，外层退出码为 1；最终全量复验改由 Windows `cmd.exe` 重定向日志并显式保留原生退出码，不涉及 WSL。日志保留在 `.tmp-fix-vitest*.log` 和 `.tmp-fix-card-vitest.log`，不提交；既有 Ant Design/React 提示和其他测试输出未在本次修改。

其中一次全量复验跨夜，日志记录 `Duration 40735.41s`，大量测试及 worker RPC 超时，汇总为 `3 failed | 33 passed` / `66 failed | 384 passed` / `51 errors`，原生退出码 1。该轮日志为 `.tmp-fix-vitest-final.log`；保留诊断，不将其视为通过。环境恢复后保持代码不变，再次执行同一全量命令，最终 38 个文件、531 项测试全部通过，原生退出码 0；日志为 `.tmp-fix-vitest-resumed.log`。

线上版本和 Astro 状态均未改变，没有生产备份、部署或真实外部写入验收。开始时已有的三个 `.tmp-pair-template-backend-*` 未跟踪目录及 `docs/pair-card-template-spec-2026-11-12.md` 保持原样、不暂存；本次 `.tmp-fix` 为指定 pytest 临时目录，不纳入提交。后续其他模块或 push/部署应另开任务并明确范围。
