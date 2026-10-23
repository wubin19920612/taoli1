# 价差查询：大价格差组合显示比值

## 范围与基线

分支 `codex/frontend-localization-polish`，开始基线 `38128b3`；线上功能基线 `c0ac671`。只处理价差查询的展示，不改变后端 API、交易身份、告警阈值、资金费率或交易操作。

## 行为与入口

- 按后端已归一化、已折算右腿倍率的价格判定：左/右 >= 2 或 <= 0.5 时显示价格比值，否则维持价差百分比。
- 比值方向固定左 ÷ 右；开仓使用左 ask / 右 bid，平仓左 bid / 右 ask，中价和标记价使用对应字段。左右交换后比值取倒数，右腿不能再次应用倍率。
- 四张卡片、主图几何/坐标轴/最新值/转折点/悬停、同时段比较、历史明细统一切换。比值保留六位有效数字，极小/极大值保留有效精度；缺失和无效报价显示 `-`。
- 曲线使用真实比值重新绘制，不将百分比简单除100或换后缀；API对象不被修改。比值卡片中性色，不把正比值当作正收益。资金费率、溢价和诊断仍保持其百分比口径。
- `frontend/src/utils/pairRatio.ts`：判定、有效价格、格式和绘图数据副本。
- `frontend/src/pages/PairMonitorPage.tsx`：卡片、主曲线、同时段对比、明细。
- `frontend/tests/pairRatio.test.ts` 与 `frontend/tests/PairMonitorPage.test.tsx`：比例边界、反向、无效报价、倍率只应用一次、实际曲线值、页面显示。

## 验证

工作区版本相关前端测试70项通过（含2项既有布局WIP测试），TypeScript/Vite生产构建通过。真实线上只读行情接入本地前端：XAU/CL约39.74、CL/XAU约0.02517，BTC/BTC仍百分比；1440px/390px无横向溢出、无页面脚本错误。日志和截图在 `output/pair-ratio-*20261023*`，浏览器脚本 `output/verify-pair-ratio-20261023.mjs`；不提交认证信息。

首次测试因Ant Design表头有测量副本而匹配到两个标题，修正测试定位后通过；原失败日志保留。首次类型检查发现项目target不支持Array.at，改为索引访问后通过。

## 工作区保护

开始已有PairMonitor页面布局调整及2项布局测试，均保留；本次通过从HEAD应用等价功能修改生成独立index blob，只提交比值改动，不提交布局移动。原始文件备份 `output/pair-ratio-original-{page,test}-20261023.txt`。既有未跟踪文件、pytest产物、DB/WAL/SHM和日志全部保留。

## 线上与后续

本次已完成双预构建镜像、双库独立容器备份、完整性/哈希校验、git pull --ff-only与no-build部署，最终证据见文末。部署沿用上次已核实的 `output/fs-borrow-isolated-deploy-20261022.sh` 包装，不在2GB生产机构建，不在业务后端容器内做重型备份。服务器原有重启/内存问题另属运维模块，本次不宣称修复。

后续其他功能请新建任务，从本交接继续；如需用户自选显示模式或修改2倍阈值，可独立小改。当前阈值附近可能随行情自动切换，是明确的自动规则。

## 提交版本复核

功能提交 `2e9afa2fb502c437f6bb87637e3bd0823adab8bd` 已推送。独立detached worktree `.worktrees/pair-ratio-validation-20261023` 排除两项既有布局WIP测试后，68项通过，TypeScript/Vite构建通过；保留该worktree作为验证证据。原工作区仍只有原有页面布局/测试WIP（71行增加、49行删除）。

干净版本浏览器5组均通过：XAU/CL桌面和手机、CL/XAU反向、BTC/BTC同价普通百分比、XAU/CL设置0.025倍率后价格归一至约0.99而恢复百分比。真实API价格比值与卡片/最新值一致；同时段比较和悬停无百分号；无页面错误、无整页横向溢出。截图 `output/pair-ratio-clean-*-20261023.png` 已目视检查；JSON `output/pair-ratio-clean-browser-20261023.json`。浏览器脚本的同名Switch/共享图表class定位已缩窄到主图，失败日志保留，未将工具定位失败视为产品故障。

GitHub Actions [39887421666](https://github.com/wubin19920612/taoli1/actions/runs/39887421666) backend/frontend均success，服务器对两个完整SHA镜像执行manifest inspect均通过。

## 最终线上验收

2026-10-23 11:07（Asia/Seoul）部署成功，脚本退出0。线上Git及两份镜像完整SHA均为 `2e9afa2fb502c437f6bb87637e3bd0823adab8bd`，tracked clean，backend/frontend healthy、RestartCount均0；`/api/health` status=ok、12197市场、exchange_errors={}。短期健康不能证明既有长期内存问题已修复。

备份均通过 SQLite integrity_check=ok、独立容器与宿主机SHA256一致：

- 主库：`backups/radar-before-2e9afa2fb502-20261023T020523Z.db`，SHA256 `f7bd402f36add8df086599916223fc0a57ce5bc7f898fb93c17db12fb673bd7a`。
- 路线库：`backups/squeeze-route-before-2e9afa2fb502-20261023T020523Z.db`，SHA256 `8b565b0683e365a0682c4f7676fb538f9a2371d821362a054d6cc1f3b5f32162`。
- 隔离备份包装：`output/pair-ratio-isolated-deploy-20261023.sh`，由上一轮包装规范换行为LF后通过scp传输，bash -n通过。既有备份保留策略执行，命名手工备份不受影响。完整日志 `output/pair-ratio-deployment-20261023.log`。

真实生产页面5组验证全部通过，结果 `output/pair-ratio-production-browser-20261023.json`：XAU/CL约39.9144、CL/XAU约0.0250445；BTC/BTC以及倍率0.025折算后的XAU/CL仍显示百分比。主图悬停、同时段比值图、历史表头验证通过，1440px桌面和390px手机均无整页横向溢出、无页面脚本错误，截图已目视检查。只读浏览器验证拦截所有非GET/HEAD API请求，没有保存或更改线上配置。健康证据 `output/pair-ratio-production-health-20261023.log`。

最终交接以独立 `[skip ci]` 文档提交推送；线上运行镜像保持功能SHA。遗留限制为2倍阈值附近自动切换、比值仅为价格关系；原有服务器长期内存问题和未提交布局WIP留待各自任务。
