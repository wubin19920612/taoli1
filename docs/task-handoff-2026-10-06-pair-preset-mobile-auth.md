# 手机端标的对鉴权修复交接

日期：2026-10-06（Asia/Shanghai）。

## 目标与范围

- 模块：双腿价差页面的已保存标的对。修复手机删除时只显示 `Invalid dashboard password`、无法在当前页验证并继续的问题。
- 同一鉴权流程覆盖保存、删除、旧本机缓存迁移，以及从双腿页面加入关注浮窗；不改账户连接、交易执行、行情采集或密码配置。
- 验收：缺失/过期密码会提示验证；正确密码只重试原操作并在成功后记住；错误密码、取消和网络错误不会误删本机标的对或覆盖已保存密码。

## 分支与基线

- 分支：`codex/frontend-localization-polish`。
- 本地基线：`f1d998c41f0289607f16158b3cc0c3aa0f0f9921`。
- 开始时生产应用及两份镜像：`9a6810883dd163f340faa9ee37eee172f8d208d6`；生产跟踪文件干净，两个容器健康。
- 功能提交：`f8e871ae76d429700b7f7da0685c150055f81e4c`，已推送当前 GitHub 分支，没有 force push。
- 本文最终验收记录通过后续文档提交推送；生产服务器 HEAD 和运行镜像固定到上述功能提交，不为文档变更重启服务。

## 根因与关键入口

- 后端写接口一直要求 `X-Dashboard-Password`，读列表不需要密码。密码存在各浏览器/访问源自己的 `localStorage.dashboard_password`，电脑配置不会自动同步到手机；旧实现仅把 401 的英文错误显示出来。
- `frontend/src/api/client.ts`：`ApiError` 保留 HTTP 状态；将服务端明确的密码拒绝翻译为中文；标的对和加入浮窗接口允许请求级候选密码，不提前写入浏览器存储。
- `frontend/src/pages/PairMonitorPage.tsx`：`runPresetWrite` 识别 401，保存原操作闭包；`retryPresetWrite` 验证并重放；带加载/防重复提交保护的移动端兼容密码弹窗。后台缓存迁移只显示验证入口，不自动打断只读浏览。
- `frontend/src/utils/floatingWatch.ts`：从双腿页加入浮窗时继续传递同一个候选密码，避免第二个写接口重新使用旧密码。
- `frontend/tests/ApiClient.test.ts`、`frontend/tests/PairMonitorPage.test.tsx`：密码缺失/错误/取消、原删除重试、保存身份保留、缓存迁移、非 401 错误和浮窗双写回归。
- 后端 `backend/app/core/security.py`、`backend/app/api/routes_pair_spread.py` 未修改，不移除或放宽写接口保护。

## 重要规则

- 服务端写成功前不移除缓存中的标的对、不永久保存候选密码；错误候选不会替换旧凭据，关闭弹窗清空输入。
- 重试使用原标的 ID/保存快照，而非读取重试时已经变化的表单。保存快照继续保留 Hyperliquid DEX、原始市场、两腿倍率和查询范围。
- 不改变资金费率周期、双方成交额、成交价格、手续费或市场倍率的计算与展示，不产生交易请求。
- 设备、浏览器或访问源变化后需要各自验证面板密码；不是交易所密码或 API Key。继续沿用项目已有的本机密码存储方式，没有引入新凭据同步系统。

## 本地验证

- 在旧实现上先复现 6 个新增密码恢复断言失败；修复后定向前端回归：`ApiClient`、`PairMonitorPage`、`FloatingWatchPanel`、`AppShell`、`SettingsPage` 共 **108 passed**。
- 后端 `tests/test_pair_spread_presets.py`：**2 passed**，确认未经授权的写/删除仍返回 401。
- `npm run build`：TypeScript 检查及 Vite 生产构建通过；`git diff --check` 通过。
- 浏览器本地预构建页面：390px 手机和 1440px 桌面缺失密码、错误密码、正确密码重试均通过，返回序列 `[401, 401, 200]`，无页面异常或横向溢出。
- 验收脚本在浏览器中注入一个唯一的、线上不存在的合成标的 ID；不保存合成标的，不触碰真实标的。生产模式仅向真实接口删除这个不存在的 ID，并比较验收前后的服务器列表。
- 既有测试存在 Ant Design 废弃属性、React act 和 FastAPI/Starlette 兼容性警告，无相关失败；未扩展处理这些无关问题。

## 线上交付

- GitHub Actions `Build production images` 工作流 `37489093086`：backend、frontend 两个任务均成功；部署前在服务器分别验证两份功能 SHA 镜像 manifest 存在。
- 主库备份：`backups/radar-before-f8e871ae76d4-20261006T154103Z.db`，610230272 字节；`integrity_check=ok`，容器/宿主机 SHA-256 一致：`99e1ea9bb9d7f3784738a43691e4e2d7d96838335f7e8feb7ea33f8c3e9df28c`。
- 研究库备份：`backups/squeeze-route-before-f8e871ae76d4-20261006T154103Z.db`，217088 字节；`integrity_check=ok`，SHA-256 一致：`1e9d5a3ccce55eac100f6a998e2bfd94e58fb72b2d615e5ee9b674e36f8be937`。
- 生产是 2 GiB 主机，只在 GitHub Actions 构建镜像；`deploy/linux-update.sh` 已完成上述备份/校验、`git pull --ff-only`、拉取固定 SHA 镜像和 `up -d --no-build --wait`，执行结果为成功。没有生产构建、数据库恢复、修改密码或删除卷。
- 既有备份保留策略移除 2 份较旧主库备份和 2 份较旧研究库备份；本次两份备份均保留。
- 服务器 HEAD 与 backend/frontend 镜像标签均为 `f8e871ae76d429700b7f7da0685c150055f81e4c`；跟踪文件工作区干净，两容器 healthy。
- 2026-10-06 23:43（Asia/Shanghai）健康采样：前端代理与后端直连 `/api/health` 均返回 `status=ok`，11778 个市场，`exchange_errors={}`；前端首页 HTTP 200。
- 线上 Chromium：390px 手机和 1440px 桌面均使用线上构建与真实受保护删除接口，缺失密码/错误密码/有效密码依次返回 `[401, 401, 200]`；错误候选不持久化，成功后记住密码并移除合成卡片。
- 页面无横向溢出、无 pageerror；合成 ID 确认线上不存在，只执行无影响的删除，不新增或删除真实标的；验收前后服务器标的对列表完全一致。

## 已知边界与未跟踪产物

- 截图中的 HTTP 访问方式仍是既有部署边界；浏览器发送密码及本机存储的安全性仍依赖可信设备和 HTTPS。本任务没有更换认证协议、开启公开无密码写入或修改生产密码。
- 开始时已有 `.worktrees/`、多份 `backend/.pytest_*`、其他模块交接记录、`output/` 截图/脚本和研究产物，全部保留，不暂存、不删除、不覆盖。
- 本任务未跟踪验收产物：`output/verify-pair-preset-auth-20261006.mjs`、本地/生产的手机/桌面 `output/pair-preset-auth-*-20261006.png`；测试、构建、部署、健康与线上验收日志在忽略的 `output/pair-preset-auth-*-20261006.log`，构建结果在忽略的 `frontend/dist/`。验收密码仅在受控进程内存中使用，不写入源码、日志、截图或仓库。
- 实现、测试、GitHub 推送、备份、生产部署和线上验收已完成。本任务只提交 3 个前端源码文件、2 个测试文件及本文；所有其他改动和未跟踪产物保持原状。
- 下一模块请新建任务，以本记录作为交接。若再次出现同类问题，先检查当前设备/访问源的凭据及实际 401 状态，不通过取消后端鉴权来修复。
