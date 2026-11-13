# 价差页其他 HL DEX 卡片提示交接

## 目标与范围

- 按 `docs/pair-card-dex-hint-spec-2026-11-13.md` 实现其他 HL DEX 已有卡片的提示与切换查询。
- 分支：`codex/pair-card-dex-hint`；实现前基线：`b690822`。
- 本任务仅本地实现、测试和提交；用户明确禁止 push、部署和 WSL。

## 已完成功能和代码入口

- `backend/app/api/routes_astro.py`：lookup 从同一次实时 `list_pairs()` 中返回当前路线卡片与 `other_dex_cards`。后者要求与任一计划变体的 name/type/buyEx/sellEx 相同，HL DEX 不同。
- HL DEX 使用 `aEffectiveHlDex` / `aHlDex` / `main`，卖腿对应 b 字段；非 HL 腿返回 null。开平阈值沿用 `position_to_pct`，保留卡片 status。
- update 保持严格路线匹配及原有锁、并发修改检查和写入开关；其他 DEX 的卡片不能使当前路线变得可修改。
- `frontend/src/api/types.ts`：增加 `AstroOtherDexCard` 和可选 `AstroCardLookup.other_dex_cards`，兼容旧响应。
- `frontend/src/components/pairTemplate/PairTemplateCard.tsx` 与 CSS：每张卡片显示独立 warning、DEX 和开平阈值，并提供切换查询按钮。
- `frontend/src/pages/PairMonitorPage.tsx`：按模板买卖方向将卡片 DEX 映射回左右腿，更新表单并复用 `runQuery`。主 DEX 显式选择 `main`，避免空值触发自动匹配；清除旧标的 DEX 前缀，避免覆盖新选择。

## 验证

在 `frontend/` 执行：

- `npx.cmd tsc --noEmit`：退出码 0，无类型错误。
- `npx.cmd vitest run --no-cache src/components/pairTemplate/PairTemplateCard.test.tsx src/components/pairTemplate/PairMonitorDexHint.test.tsx`：`Test Files 2 passed (2)`，`Tests 34 passed (34)`。
- `npx.cmd vitest run --no-cache`：`Test Files 44 passed (44)`，`Tests 676 passed (676)`。

在 `backend/` 执行：

- `pytest tests/test_pair_card_template.py -q -p no:cacheprovider --basetemp ../.tmp-dexhint`：`58 passed in 50.46s`。

新增后端用例覆盖买/卖 HL 腿、HL/GC-HL 计划变体、Effective DEX 优先、主 DEX 回退、同 DEX、非 HL、多个其他 DEX、无关 name/type/exchange 排除、单次实时读取和 update 不越 DEX。

新增前端用例覆盖警告、开平阈值、多个卡片、修改按钮禁用、反向买卖映射、双 HL 腿，以及页面切换左右 HL 腿至 xyz/main 后重新查询、查卡和 URL 更新。旧响应由原有不含新增字段的夹具覆盖。

`git diff --check` 通过。前端运行输出包含现有 Ant Design 弃用提示和 React act 提示，没有测试失败。

## 线上状态、产物与下一步

- 未推送、未备份线上数据库、未部署、未做线上验证，未运行 WSL；未访问生产环境。
- 无功能简化项。浏览器人工/真实 Astro 验证未进行；当前验收证据来自本地类型检查、接口测试和 DOM 页面集成测试。
- 开始前已有未跟踪文件 `docs/pair-card-dex-hint-spec-2026-11-13.md`，保持原样且不纳入提交。
- 本地测试日志 `.tmp-dexhint-frontend-focused.log`、`.tmp-dexhint-frontend-all.log` 受现有 `*.log` 规则忽略，未提交。
- 后续如需上线，请在新任务中审查本提交，再按部署文档处理备份、推送、部署和线上验收。
