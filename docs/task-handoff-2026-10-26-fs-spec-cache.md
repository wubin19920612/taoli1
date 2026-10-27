# M2-3a：FS 规格缓存瘦身交接

执行日期：2026-10-27。任务书：`docs/m2-3a-fs-spec-cache-spec-2026-10-26.md`。

## 范围与基线

- 工作目录：`.worktrees/m2-3a-fs-specs`；分支：`codex/m2-3a-fs-specs`。
- 开始时 HEAD：`1bfc8f43a40e1c6dadb9e5dab65025ffd3445d92`（任务书提交），父提交为任务书指定基线 `cfd79d6`。开始时工作区干净。
- 仅修改 `backend/app/services/fs_public.py`，仅新增本交接及 `backend/tests/test_fs_public_spec_cache.py`。
- 用户限定本地实现、测试和一个提交；不联网、不调用 WSL、不 push、不部署。
- 提交信息：`perf: slim binance exchangeInfo spec cache (M2-3a)`。本交接随该提交保存，提交号可由当前分支的 `git log -1` 获取。

## 实现与业务规则

唯一生产改动入口是 `PublicFsProvider.binance_specs()`：

- 每个 symbol 新建小字典，只保留 `symbol/status/baseAsset/quoteAsset/contractType/marginAsset/isMarginTradingAllowed` 中实际存在的键；不补默认值。
- 过滤器仍为列表，保留 `LOT_SIZE` 的 `stepSize/minQty`，以及 `NOTIONAL/MIN_NOTIONAL` 的 `minNotional/notional` 和各自的 `filterType`。保留顺序和重复项，读取方原有“最后一个同类型过滤器生效”、`NOTIONAL` 优先和缺字段异常语义不变。
- 不提前验证畸形过滤器。缺失或非字符串 `filterType`、非字典过滤器以及非列表 `filters` 保留读取时触发原异常所需的数据，避免原本仅影响 `current_instruments()` 的错误提前使 `borrow_reference()` 失败。
- 用 `finally` 清除原始 payload、symbol、filters 和 filter 的局部引用；持有缺少 symbol 的异常 traceback 时，也不继续保留原始顶层对象和原始 symbol 字典。
- 缓存键仍为 `("binance_specs", futures)`；TTL 仍是严格 `< 20` 秒，正好 20 秒开始重新请求。`at` 直接使用 `get()` 返回的 HTTP 响应完成时间。
- `borrow_reference()`、`current_instruments()`、每个 symbol 的 meta 缓存、`get()`、锁、限速和其他交易所逻辑均未修改。

## 测试与体积证据

环境：Windows 原生 Python 3.12.10，pytest 8.4.2。所有 pytest 命令均在 `backend` 目录执行，使用 worktree 内 `--basetemp`。HTTP 使用 `httpx.MockTransport`；后端已有的自动离线保护保持生效。

测试文件原样冻结基线的 `binance_specs()`、`borrow_reference()`、`current_instruments()` 三个方法作为旧实现。另以 AST 比较核对副本与 HEAD 完全一致，生产文件除 `binance_specs()` 外的 AST 也完全一致。

- 等价对照：34 种场景分别作用于现货和合约，共 68 例；比较两个调用方的完整返回对象与未排序 JSON 的 UTF-8 字节，时间用 ISO 格式序列化。重复读取验证缓存命中与 symbol meta；异常比较具体类型和参数。
- 覆盖正常、MIN_NOTIONAL 回退、两种 notional 都缺、优先级、显式 null、重复过滤器/市场、非 TRADING、非 PERPETUAL、非 USDT margin、base/quote 不匹配、symbol 不存在、关键键缺失及畸形过滤器。
- TTL：现货和合约分别检查 19.999 秒命中、20 秒和 21 秒重新请求；fake HTTP 推进时钟 250 毫秒，确认 `at` 是响应完成时间。
- 释放：使用可 weakref 的 dict 子类包装真实 JSON 解码结果，只记录弱引用；同时确认顶层对象和每个原始 symbol 字典不可达。覆盖正常返回及调用方仍持有 KeyError traceback，共 4 例。

缓存体积使用 3,000 个现货 symbol 和 600 个合约 symbol 的 JSON fixture，含 orderTypes、permissions、permissionSets 和九类 filters。旧、新实现分别经过 HTTP JSON 编解码后，测量整个 provider.cache；用 `sys.getsizeof` 完整遍历 dict/list/tuple 并按对象身份去重，不使用 census 的大容器抽样。

| 测量对象 | 深度体积（字节） | 十进制 MB |
| --- | ---: | ---: |
| 旧实现 | 25,714,147 | 25.714147 |
| 新实现 | 4,685,329 | 4.685329 |
| 减少 | 21,028,818 | 21.028818 |

降幅 **81.7792%**，通过至少减少 80% 和小于 5,000,000 字节的断言。这是本地合成 fixture 的缓存常驻体积，不是线上 71MB 的复测，也不代表解码峰值或进程 RSS 降幅。

### RED → GREEN

先运行原实现：

```powershell
python -m pytest tests/test_fs_public_spec_cache.py tests/test_fs_borrow_reference.py tests/test_fs_multi.py tests/test_fs_monitor.py tests/test_fs_astro.py --basetemp=../.pytest-tmp/m2-3a-red -q
```

结果：**3 failed, 147 passed in 26.59s**。预期失败为体积缩减（旧、新均 25,714,147 字节）及现货/合约原始 symbol 字典释放。

在生产实现修改前，再补充异常 traceback 的释放断言：

```powershell
python -m pytest tests/test_fs_public_spec_cache.py -k decoded_payload --basetemp=../.pytest-tmp/m2-3a-red-lifetime -q
```

结果：**4 failed, 71 deselected in 0.82s**，正常与异常路径的原始对象引用均按预期未释放。

实现后运行指定测试组：

```powershell
python -m pytest tests/test_fs_public_spec_cache.py tests/test_fs_borrow_reference.py tests/test_fs_multi.py tests/test_fs_monitor.py tests/test_fs_astro.py --basetemp=../.pytest-tmp/m2-3a-green -q -s
```

结果：**152 passed in 22.06s**。

随后运行后端全量：

```powershell
python -m pytest --basetemp=../.pytest-tmp/m2-3a-full -q
```

结果：**1987 passed, 4 deselected, 12 warnings in 110.27s**。沿用项目默认排除 `network` 和 `benchmark` 的设置；12 条警告来自 `test_api.py` 和 `test_index_components.py` 路径上的 `datetime.utcnow()` 弃用提示，与本次缓存改动无关。

静态检查 `python -m ruff check app/services/fs_public.py tests/test_fs_public_spec_cache.py` 通过；`git diff --check` 通过。

## 偏离任务书、线上状态与后续

- §3、§4 的实现约束与文件范围无偏离；读取方无需修改。释放测试使用任务书允许的 weakref 方案；体积测试采用精确遍历，避免抽样误差。
- 根据用户本次明确要求，仅创建本地提交，不 push；未连接服务器，未执行线上备份、部署、探针采样或页面验收，线上版本及效果未验证。
- `.pytest-tmp/` 是本次指定基目录产生的未跟踪测试产物，保留、不暂存；没有其他任务的既有 WIP。Python/pytest 的忽略缓存不纳入提交。
- 后续上线应另开服务器运维任务，按任务书 §5 获得部署授权后，与其他裁剪开关错开上线；备份数据库、记录镜像/提交，采集 1 小时探针并核对 Binance FS 状态，特别是 `stale_input` 与 `spot_market_missing`。
- 若线上出现回归，使用 revert 本次提交回滚。当前数据仅证明离线等价与 fixture 常驻缓存缩减，不替代线上验收。
