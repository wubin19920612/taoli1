# crossex-exec：登记外部负债额度（2026-11-17）

## 背景
用户在 HL（CrossEx 账户，exchange_type=HYPERLIQUID，coin=USDC）上留有一笔自己的 USDC 负债（约 274），
没有外部仓位可以归属，`program_risk.debt_attribution` 把它判为 blocking → `account_liability` 全局停开锁。
用户明确：这笔负债由用户自己负责，希望登记后程序照常开仓。

## 要做
1. **登记数据**：store meta `external_liability_allowances` = `{revision:int, items:[{exchange_type, coin, cap, reason, registered_at}]}`。
   - cap 为该币种原生数量（Decimal 字符串），`0 < cap <= EXTERNAL_LIABILITY_CAP_MAX`（新常数 `dec('1000')`）；reason 1–200 字符、无控制字符；exchange_type/coin 走与现有外仓登记相同的格式校验。
   - 每个 (exchange_type, coin) 至多一条；再次登记即覆盖（需 expected_revision 匹配）。
2. **管理接口**（照抄 management.py 中 `external-positions/register|unregister` 的写法：admin token、JSON body、WriteConflict 409、审计事件、事务）：
   - `POST /api/v1/external-liabilities/register` body `{exchange_type, coin, cap, reason, expected_revision}`
   - `POST /api/v1/external-liabilities/unregister` body `{exchange_type, coin, expected_revision}`
   - `GET /api/v1/external-liabilities`：返回 revision、items，以及当前快照中每条匹配负债的 `liability`、`within_cap`（读不到快照时字段为 null，不报错）。
   - 审计事件 kind：`EXTERNAL_LIABILITY_ALLOWANCE_REGISTERED` / `..._UNREGISTERED`。
3. **风控判定**（program_risk.debt_attribution）：现有按外仓保证金归属的逻辑不变；对原本会进 blocked 的负债，若同时满足下列条件则改判为 external（输出 `dict(debt, allowance_cap=str(cap))`）：
   - route=='crossex'、source_field=='liability'、amount>0；
   - 资产行唯一且 `liability` 与 debt 完全相等（与现有校验一致）；
   - 有对应 (exchange_type, coin) 的登记且 `amount <= cap`。
   - 登记额度**不受** `mixed`（程序在该域有仓/在途单）影响，因为额度本身封顶；但超额一分钱也 blocking。
   - 读取登记失败/格式异常 → 视为无登记（fail closed）。
   - 检查 debt_attribution 的所有调用方（limits.py、equity.py、engine.check_new_risk、恢复逻辑 ACCOUNT_LIABILITY_RECOVERED 等），确认新字段不破坏它们；必要时同步调整。
4. **告警降噪**：limits.py 的 external_liability 告警，对于仅由额度覆盖的负债，只在“状态集合变化”（登记生效/失效、首次出现、超过额度 80% 首次跨越）时告警，金额小幅变动不再重复告警。
5. **summary/只读输出**：external-positions 读接口或 summary 中暴露登记额度（只读），字段向后兼容。
6. 不改：下单、开平仓、程序盈亏归属、其他风控阈值。

## 验收
- 测试：非 admin 403；body 校验（cap 上限/非正/非数字、reason、未知字段）400；revision 不符 409；登记后负债 ≤ cap → 不 blocking、可开仓；> cap → blocking 加锁；注销后恢复 blocking；登记数据损坏 → blocking；额度覆盖时程序在同域有仓也按额度判；告警降噪；已有外仓保证金归属测试全部不变。
- `python -m unittest discover -s tools/crossex_exec/tests` 与 `python -m unittest discover -s tools/fill_sim/tests -t .` 全过。
- 只改 tools/crossex_exec/ 下代码与测试；不要 git commit。
