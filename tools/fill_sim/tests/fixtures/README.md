# 公开响应夹具

由 `python -m tools.fill_sim.smoke --include-spot --fixtures tools/fill_sim/tests/fixtures` 录制。

每个 JSON 包含 `spec` / `depth`，其子字段记录公开请求 URL、客户端 UTC 秒接收时间及响应。Binance/Aster exchangeInfo 只保留 BTCUSDT 的原始 symbol 行，其他字段不修改；其余响应保持原始结构。盘口均为 50 档。没有请求认证、订单、账户接口。

`test_fill_sim.py` 对每家 future/spot 分别读取夹具、按录制时刻验证规格/深度，同时禁止 socket connect。断流、限频、不同价格、数量取整等边界在测试内构造，不伪装为交易所录制内容。夹具的市场价格和费率绝不是实时交易建议。
