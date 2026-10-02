# Astro 字节码反编译与双腿执行时序评估

日期：2026-10-02。模块：自建套利执行器调研。状态：离线研究与设计，非已实现交易系统。分支 `codex/frontend-localization-polish`；本轮起点 `a96a28dde28fec9f4d82c8b2bdc1babcee35aa65`。

后续进展见 [核心行为与独立实现规格](astro-core-reimplementation-spec-2026-10-02.md)：已将双腿匿名调用映射到 tradeSpot/tradeFuture，继续追踪 HL IOC、mid 缓存、nonce、签名和 HTTP 路径，并以原文件字节核对 30 个区域的 1,971 条指令。本报告保留前一阶段的结果与限制，新确认事项以新规格和证据索引为准。

## 1. 结论与语言判断

已经从字符串检查推进到整个 core 主文件的字节码解析、函数索引和逐指令线性伪代码：2,867 个 bytecode arrays 全部生成线性输出。这不等于恢复了 2,867 个可运行、语义正确的源码函数。完整高级控制流重建超时，小函数重建还发现实际错误。

Astro core 是 Node.js/V8 执行的 JavaScript 字节码，使用 Bytenode 分发。原工程可能使用 JavaScript，也可能由 TypeScript 编译而来，现有证据不能区分。Lighter 签名组件有 Go WebAssembly 证据，不代表整个策略引擎使用 Go。V8 底层语言也不等于 Astro 业务开发语言。

相似功能及执行性能可以作为自建目标，但现在没有 Astro 同条件基准，也没有自建实盘实现，不能宣称已达到。跨交易所只能缩小发单时间差、控制单腿敞口，不能保证两边同时成交或原子提交。

## 2. 样本、工具与产物

- [官方安装说明](https://github.com/astro-btc/Astro/blob/a21552e6802d0464824cac97e5798c9c584645da/INSTALL.md)、[v1.9.24 发布](https://github.com/astro-btc/Astro/releases/tag/v1.9.24)。不能与浮动 latest 镜像混用。
- ZIP：6,522,965 bytes；SHA-256 `d28dc395203c2c6c5db1c9b9e39679c13ce942823eb3304a327ce6e770e1d3e8`。
- `astro-core/src/main.jsc`：2,322,352 bytes；SHA-256 `54339066c361389ae0a745a294a4d7641ad9945ef3d7d660e1ea17a11ec93850`。
- 工具：[v8asm 固定源码](https://github.com/aynakeya/v8asm/tree/69be8af3389a4d03bae2119e43888fdc49b55298)，源码 ZIP SHA-256 `f0bc76851d188c2e08b973da09a6c475d4bdade8bcc7103b27836aa49962d195`。纯 Python 离线解析，不加载目标程序到 V8。
- 解析器选中 V8 `12.9.202.28` profile，legacy runtime variant，tagged size 8，未提供 snapshot。profile 匹配不证明原 Node 精确版本、构建参数或 runtime ID 全部一致。

研究目录：`C:\Users\wubin\.codex\visualizations\2026\10\01\01a0f7eb-045a-73f1-b336-a920cbad9f98\astro-release-research`。工具在 `v8asm-source`，输出在 `decompilation-20261002`。大文件留本机，不作为自建系统依赖。

| 产物 | 结果 | 含义 |
| --- | --- | --- |
| core-entry.disasm.json | 20 arrays，309 对象，2,078,185 bytes | 入口解析成功 |
| core-main.disasm.json | 2,867 arrays，33,880 对象，328,033,545 bytes，约 20.64 秒 | 主文件解析完成 |
| core-entry.decompiled.js | 31,402 bytes | 入口高级伪代码，仍含未解引用 |
| core-main.linear.js | 11,479,192 bytes，2,867 函数标题 | 全部逐指令输出，非可执行 JS |
| selected-functions | 23 个线性文件、6 个高级文件 | 恢复、双腿分派、模式判断等 |
| core-main.decompiled.js | 全文件高级重建 150 秒超时，空文件 | 失败，不计为恢复源码 |
| quality-report.json | 1,161 次不支持翻译、21,609 次未解只读引用 | 出现次数，非不同未知符号数量 |

复现入口：`python -m disassembler <file.jsc> --format json`。从 JSON 构造 `DecompilerContext`，对每个 `V8BytecodeArray` 调用 `decompile_bytecode(context, array, linear=True)`。未 require 目标 .jsc、未执行 WASM、未连接交易账户。入口存在单实例锁及孤儿进程处理线索，不能在生产上运行以试兼容。

工具验证：`python -m pytest tests/test_checkversion.py tests/test_string_object.py -q -p no:cacheprovider`，补齐固定版本官方 fixture 后 7 passed。初次失败由选择性解包遗漏 fixture 引起。这只是工具局部测试，不证明 Astro 语义等价。

## 3. 已识别的执行逻辑

下列地址是解析器序列化对象标识，不是文件偏移或生产内存地址。

### CrossEx 恢复决策

`anonymous_770 @ 0xf00000498c00` 初始化与 `kZ @ 0xf0000049a000` 分支显示：MAX_INITIAL_CHECKS=8、MAX_CANCEL_ATTEMPTS=8、MAX_POST_CANCEL_CHECKS=8、MAX_NOT_FOUND_AFTER_CANCEL=5。

| helper 输入状态 | 返回规则 |
| --- | --- |
| 已请求撤单，notFound 为真 | 累加 notFoundAfterCancel，达到 5 返回 ALLOW_RETRY，否则 REQUEUE |
| 已请求撤单，非 notFound | 累加 postCancelChecks，达到 8 返回 FAIL_CLOSED，否则 REQUEUE |
| 未请求撤单，retries 小于 8 | 增加 retries，REQUEUE |
| 初始检查额度用尽，cancelFailed 为假 | CANCEL |
| cancelFailed 为真 | 累加 cancelAttempts，达到 8 返回 FAIL_CLOSED，否则退避重排 |

错误原因包括“cancel accepted but order stayed pending”和“cancel could not be confirmed”。延迟逻辑有 600、5000、倍增/指数相关指令，但内建引用未解且 Exp 翻译不支持，不能把推测的 min 公式当成事实。

这些仅是特定 helper 的返回规则，调用方、计数重置及平台适用范围未全链验证。不能泛化为 Astro 所有订单重试 8 次，也不能把撤单后 notFound 五次直接移植为安全规则。自建仍以场所实际最终状态和查询一致性决定重发。

### 双腿分派及时间字段

`Un_1 @ 0xf0000074f200` 有 abFirst、lastTradeCountForAbFirst、abFirstShouldTradeLeft 及先后腿分支。另一分支连续调用 BUY、SELL 下游函数，在该 helper 内两调用之间没有等待 ACK 的 await 指令。这证明该层连续分派，不证明网络同时发送：下游签名、限流、SDK、nonce 和网络发送未全部解出，BUY 调用仍可能同步阻塞。

`Yr_1 @ 0xf00000750b00` 有 boostMode 提前返回、slowMode 选择 288/111 及 20*1000/60*1000、ts 差值比较与更新；`te @ 0xf00000780700` 有 222/111、60000 与 boostMode 分支。时间源、调用频率、适用范围未完全确认，不能称 Astro 下单延迟为 111 ms 或慢速固定为 288 ms。

## 4. 反编译可信度与后续路径

发现具体高级控制流错误：Yr_1 线性 offset 17 在 Ve 为真时跳到 25；offset 23 在 $t 为假时跳到 27。因此两者都假时可进入后续时间判断。高级 structured 文件却在该块后生成无条件 return true，错误地使后续代码不可达。

局部可人工理解为 boostMode 或两个上下文条件任一成立时提前返回，否则继续判断；两条件业务含义仍未知。此例证明高级伪代码只能辅助阅读，不能直接运行。缺 snapshot、闭包映射、压缩命名和 async 状态机也限制还原。unsupported 次数不是指令解析错误数，引用次数不能转成恢复百分比。

后续逆向聚焦“分派 → 签名/限流/发送 → 成交回报 → 补腿/对账”，逐指令核对并用隔离 mock 做差分验证。匹配完整 snapshot 为独立工程项；无需等全部源码还原才建设自有系统。动态实验必须独立无秘密、限制网络和资源，不引入生产依赖。

## 5. 自建执行器与性能方案

更新前稿：保留 Python/FastAPI 雷达；新增 strategy-worker、trade-gateway 优先使用 TypeScript + 经适配器验证的受支持 Node LTS。理由是 JS SDK 生态、异步连接和类型契约，不是 Python 一定慢，也不把样本使用的旧 Node 版本推荐给生产。Rust/Go 仅在实测存在值得优化的热点时采用。

资金计算使用固定尺度整数/BigInt 或经验证 decimal 库，禁止 Number 直接算资金。两进程隔离策略与密钥；两腿协调在同一网关内，避免跨执行网关同步。阻塞签名/WASM、数据库和重计算进入受控 worker，其排队/通信耗时也必须测量。

### 两种模式

1. 顺序对冲 IOC：小切片先成交一腿，再按已确认增量对冲。适合适配器初期及盘口不对称场景，但承担等待期间单腿风险。
2. 受保护并行 IOC：校验双边余额、实际盘口、共同 base 数量、费用、nonce 和限流额度，原子预留任一腿独自成交的最坏风险。两腿意图与发送尝试先可靠提交，再连续放行，不等 A ACK 才发 B。

并行不是只写 Promise.all。放行前须重验行情与风控；两次发单之间不插入同步日志、数据库提交或昂贵签名；预构造必须满足签名有效期与 nonce 规则。A 已发且 B 确定未发进入单腿恢复；B 发送结果不明则 UNKNOWN。进程可能在两次 write 间崩溃，同事务落盘不能使外部成交原子化。

两边 IOC 均可能部分成交；按实际 base 风险与所有在途余量核算，不按相同 USDT 名义额假定匹配。FOK 也没有跨场所原子保证。不支持/无法核验 IOC 的场所不启用此模式。并行在 mock 阶段设计验证，生产默认顺序；通过部分成交、单腿失败、ACK 丢失、限流、崩溃恢复门槛后单独启用。

### 时间一致性指标

| 指标 | 口径 |
| --- | --- |
| 行情到发送 | 接收可信盘口到各腿提交请求，另记源行情年龄 |
| 本地发单差 | 两腿可观测传输提交时点之差；SDK 调用时间单列 |
| ACK 差 | 本地收到确认的时间差，不等于成交差 |
| 首次/完成成交差 | 首笔与累计完成分开，源时间与本地接收时间分开 |
| 裸露风险 | 最大已确认/最大可能 delta、持续时间、金额时间积分、恢复损失 |
| 净效果 | 费用、滑点、资金费后收益，双方成交额/周期/倍率/估算标记齐全 |

同机单调高精度时钟；跨场所时间戳有精度、偏差和语义差异，不直接相减声称同时成交。SDK 无底层发送时点时明确代理观测限制。首轮并行本地发单差提出 p99 ≤ 5 ms 待测目标，保留本地决策至可发约 25 ms 规划预算；都不是已测结果或到达/成交承诺。单腿超时按平台、盘口寿命和敞口实测确定，无通用安全值。

## 6. 功能对齐和验收

先做 SF/FF、开平阈值、共同数量切片、订单幂等恢复、费用资金费记账、组合盈亏和卡片状态；再做阶梯、网格、先后腿配置及更多 DEX。FR/回归比率不与普通价差混用。SDK 只解决通信，不能替代账本和风险规则。

与 Astro 比较必须固定场所、账户权限/费率档、区域/网络、订单类型/大小、深度、负载和时段。先相同行情回放，再至少 7 天 shadow；shadow 不证明真实排队和成交质量，资金受限的小额实测另行授权。报告 p50/p95/p99、错误/拒单/限流率、最大敞口和净成本。不能为测速重复开仓。无法给 Astro 内部埋点时仅比较外部同口径指标。

Arcus 官方入口和协议未确认，不能估计最终适配难度及时延。本轮完成研究与方案，未实现完整系统、未恢复全部算法、未做实盘或性能对照。仅文档变更，不修改生产数据库/容器，因此无新增备份或部署版本。原有未跟踪文件保留。

下一模块建议新任务“自建套利执行器 P0/P1”：读取本报告、主方案和交接，先完成 capability 契约、mock 网关、执行账本、状态机及时序埋点；Arcus 适配依赖其官方身份确认。
