# 公开响应夹具

由 `python -m tools.fill_sim.smoke --include-spot --fixtures tools/fill_sim/tests/fixtures` 录制。

每个 JSON 包含 `spec` / `depth`，其子字段记录公开请求 URL、客户端 UTC 秒接收时间及响应。Binance/Aster exchangeInfo 只保留 BTCUSDT 的原始 symbol 行，其他字段不修改；其余响应保持原始结构。盘口均为 50 档。没有请求认证、订单、账户接口。

`test_fill_sim.py` 对每家 future/spot 分别读取夹具、按录制时刻验证规格/深度，同时禁止 socket connect。断流、限频、不同价格、数量取整等边界在测试内构造，不伪装为交易所录制内容。夹具的市场价格和费率绝不是实时交易建议。

2026-11-09 PM 第一轮补充重新录制了上述 12 份夹具；对应原始输出为 `../smoke-output-pm-round1.txt`。旧 smoke-output.txt 保留为上一轮历史证据。

`funding/{exchange}.json` 是六家 BTC 合约公开资金费响应，每份是请求记录数组，包含 URL、接收 UTC 秒和原始 response。Binance/Aster 的配置数组只保留解析时精确匹配的 BTCUSDT 原始行；其余不改。资金费 smoke 仅 Aster 使用推荐的 V3，原规格/深度仍是 V1。单元测试用录制时刻解析，不把历史快照当当前数据。

`fees/okx-spot.json` 来自 OKX 公开 `/fees` HTML 内嵌 `tableData.normal`，保存原样提取的 Regular user 行、URL、接收时刻及查询日期。无登录或账户接口；避免把 FAQ 中的示例费率当动态费率表证据。
