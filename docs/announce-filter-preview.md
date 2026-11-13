# 其他公告过滤预览（2026-11-13）

数据：[近 7 天标题样本](../backend/tests/fixtures/announce-titles-7d.json)。
共 282 条，保留 **91** 条，排除 **191** 条。
按原始行号标记，保留重复标题；未提供中文标题或分类，使用纯函数的标题规则。
排除词优先；未命中保留规则的标题也归入排除。此表表示相关性，不包含交易所白名单、开关、历史窗口等最终推送条件。

复现：在仓库根目录执行 `python script/preview_announce_filter.py`。

| 保留（91） | 排除（191） |
| --- | --- |
| #3 [bitget/other] [Important] Bitget to disable margin and loan functions for MANTRA in unified trading account | #1 [gate/other] Gate Futures Points Airdrop #168: Claim 0.018 QQQG & 100 USDT |
| #5 [bitget/other] Bitget announcement on resuming ETH - Binance Smart Chain withdrawals | #2 [gate/other] Gate Live Stream Carnival Phase 28: Watch Live and Join the Daily Reward Spins |
| #6 [bitget/other] Bitget announcement on resuming ETH - Arbitrum One withdrawals | #4 [gate/other] Gate 研究院：全球科技股承压，BTC 跌破 71,000 美元 |
| #7 [bitget/other] Bitget announcement on resuming DOGE - Dogecoin withdrawals | #8 [bitget/other] Bitget spot copy trade now supports rMSBI/USDT |
| #9 [bitget/other] Bitget to adjust funding rate interval for HOTUSDT perpetual futures | #12 [bitget/other] Bitget spot copy trade now supports rEXTR/USDT |
| #10 [bitget/other] Bitget announcement on suspending ETH - Arbitrum One network withdrawal service | #14 [binance/other] Binance Completes Coverage of Assets Impacted in Security Incident; New Proof of Reserves Published |
| #11 [bitget/other] Bitget announcement on suspending ETH - Binance Smart Chain network withdrawal service | #15 [binance/other] Binance Stocks University: Trade bStocks to Share 2,000 USDC! |
| #13 [bitget/other] Bitget announcement on suspending DOGE - Dogecoin network withdrawal service | #18 [bitget/other] Candybomb 嘉年华（第13期）：交易瓜分 60,000 USDT！ |
| #16 [bybit/other] Important: BITOUSDT Contract Size Adjustment Due to a 1-for-2 Reverse Stock Split | #19 [binance/other] Introducing Binance Square Leaderboards: Discover Top Traders and Share Your Achievements |
| #17 [bybit/other] SNDK Perpetual & Perp Options: Stock Split Adjustment | #20 [binance/other] Binance Alpha Trading Competition: Trade LAB (LAB) and Share $200K Worth of Rewards (2026-11-12) |
| #32 [gate/other] Gate Announcement on Dividend Settlement for 7 Perpetual Futures Including COF, COP, and KLAC | #21 [bitget/other] Bitget 合约跟单已支持 FRHCUSDT 永续合约 |
| #35 [gate/other] Gate Announcement on Dividend Settlement for STRC (Strategy Inc Variable Rate Series A Perpetual Str... | #22 [gate/other] Gate C2C 有奖调研：填写问卷赢 GT 空投 |
| #36 [gate/other] Gate to Adjust Size Decimal Places for Certain Trading Pairs (November 13, 08:00 UTC) | #23 [bybit/other] Fiat-to-Crypto Frenzy: Complete Simple Tasks for a Chance to Win a Share of 80,000 USDT |
| #38 [bitget/other] Bitget announcement on resuming USDT - Arbitrum One withdrawals | #24 [gate/other] CandyDrop 192nd: CandyDrop x World Liberty Financial (WLFI) , Trade and Invite Friends to Share 906,... |
| #45 [binance/other] Binance Updates Commodity Options Settlement Price Rule | #25 [binance/other] Notice on ADR Fees for US Stocks on Binance Stock Trading - October 2026 |
| #48 [bitget/other] Bitget announcement on suspending STX - stacks deposit and withdrawal services | #26 [gate/other] CFD Trading Is Back: Claim a 200 USDx Position Voucher and Up to 686 USDT in Rewards |
| #54 [binance/other] Updates on Step Size for Spot Trading Pairs (2026-11-19) | #27 [gate/other] Gate Square Anthropic IPO Challenge: Share Your Subscription, Win 150 USDT in ANT Shares |
| #58 [bitget/other] Bitget announcement on suspending USDT - Arbitrum One network withdrawal service | #28 [binance/other] Binance Lite Loan Promotion Extended: Enjoy Simple Borrowing with 50% Off Service Fee! (2026-11-13) |
| #60 [bitget/other] Bitget announcement on suspending SUI - SUI network withdrawal service | #29 [gate/other] Convert New User Airdrop: Get $2 in WLFI, Earn Daily Rewards |
| #64 [bybit/other] Bybit to Support HYPEREVM (HYPE) nb-20261110 Network Upgrade | #30 [gate/other] NVIDIA Earnings Week Trading Challenge : Trade NVIDIA, 100% Win Rate |
| #73 [okx/other] 欧易关于对 ANTHROPIC 永续合约及 X-Perp 执行 Rebase 的公告 | #31 [bitget/other] Bitget CFD copy trading now supports custom account leverage |
| #78 [bitget/other] Announcement on Suspension of MANTRA, NESS Deposit Services | #33 [gate/other] Anthropic IPO Double Rewards : Subscribe for Bonus Trade to Win 100% |
| #79 [bitget/other] [Important] Bitget announcement on the ANTHROPIC stock perps rename and share capital adjustment | #34 [binance/other] Binance Earn: Dual Investment Supports HYPE |
| #81 [bybit/other] Bybit to Support Injective (INJ) v1.20.4 Network Upgrade | #37 [gate/other] Comeback Sprint Season 8, 20,000 USDT Subsidy to Fuel Your Recovery |
| #84 [gate/other] Gate to Upgrade BTC-M and USD1-M Perpetual Futures (November 12, 07:00 – 07:30, UTC) | #39 [binance/other] Binance Button Game: Play to Win $30,000 in Anthropic bStocks or USDC! |
| #85 [gate/other] Gate to Upgrade USDT-M Perpetual Futures (November 12, 16:00 – 16:30, UTC) | #40 [gate/other] Fifth Copy Trading New User Bonus: 20 USDT Credited Instantly |
| #89 [bybit/other] Bybit to Support Flare (FLR) v1.15.1 Network Upgrade | #41 [gate/other] New ETF Airdrop: Trade SOXL5L/5S to Share 60,000 USDT |
| #90 [binance/other] Binance Futures Will Adjust The Contract Size of USDⓈ-Margined SNDKUSDT Perpetual Contract (2026-11-20) | #42 [bitget/other] Onchain trading challenge round 134 (BSC)—share 30,000 USDT |
| #92 [bitget/other] [Important] Bitget Announcement on Cash Dividend Settlement for ROKUSDT、COPUSDT、KLACUSDT、XOMUSDT、CEGUSDT Stock Perps | #43 [bitget/other] Bitget spot copy trade now supports rIFNNY/USDT |
| #98 [bitget/other] Bitget to adjust funding rate interval and maximum/minimum limits for INITUSDT perpetual futures | #44 [gate/other] Gate Live Mining Program Bonuses Distribution Announcement (November 2 - November 8) |
| #99 [gate/other] Gate 已完成 Velodrome (VELODROME) 合并迁移到 Aero (AERO) 及 Aero (AERO) 合约置换的公... | #46 [gate/other] Gate Futures Points Airdrop #167: Claim 0.075 NVDAG & 100 USDT |
| #103 [gate/other] Gate Announcement on Adjustments for the GOVX (GeoVax Labs, Inc. New) Stock Consolidation | #47 [gate/other] Gate October 2026 Transparency Report: Gate Money Drives Integrated Financial Services Upgrades as G... |
| #104 [bitget/other] Bitget announcement on suspending USDC - SONIC network withdrawal service | #49 [gate/other] Gate 研究院：美股科技板块反弹，加密市场延续调整 |
| #107 [binance/other] Update on the Collateral Ratio Under Cross Margin and Portfolio Margin (2026-11-13) | #50 [gate/other] 开新星期四：DOGE 惊喜开箱，邀友解锁丰厚好礼 |
| #109 [gate/other] Gate Announcement on Adjustments for the EMBJ Stock Split | #51 [bitget/other] Apply for a Bitget Wallet Card for a chance to win the 500 USDC grand prize |
| #117 [bitget/other] Bitget announcement on resuming USDT - APTOS withdrawals | #52 [bitget/other] 📢 EUR (SEPA) Fiat Deposit & Withdrawal Maintenance |
| #118 [bybit/other] Bybit to Support MANTRA (MANTRA) v8.8.0 Network Upgrade | #53 [gate/other] Trenches Hot Tokens Competition Phase 70: Trade Max and HUNG to Share a 20,000 USDT Airdrop |
| #122 [gate/other] Gate Announcement on Dividend Settlement for STRC, SCHW, SBUX, MSCI, LLY and GLW Perpetual Futures | #55 [gate/other] ANT IPO Access Exclusive Benefits: USDT Earn with up to 200% Annualized Return |
| #123 [gate/other] Gate Announcement on Dividend Settlement for STRC, AAPL, FIX and TJX Perpetual Futures | #56 [bitget/other] Bitget spot copy trade now supports rHBT/USDT |
| #128 [binance/other] Updates on Tick Size for Multiple USDⓈ-M Perpetual Futures Contracts (2026-11-11) | #57 [binance/other] Binance Wallet to Discontinue Support for Blast |
| #130 [binance/other] Binance Futures Will Adjust The Contract Size of USDⓈ-Margined BITOUSDT Perpetual Contract (2026-11-19) | #59 [bitget/other] Bitget spot copy trade now supports rBRKB/USDT |
| #133 [bitget/other] [Important] Bitget Announcement on Cash Dividend Settlement for MSCIUSDT、BPUSDT、SBUXUSDT、LLYUSDT、GLWUSDT Stock Perps | #61 [gate/other] Direct Access IPO VIP Exclusive ANT Airdrop |
| #142 [bitget/other] Announcement on suspending INJECTIVE network deposit and withdrawal services | #62 [gate/other] Gate IPO Access Phase 3: Anthropic (ANT), Supporting Indicative Subscriptions in USDT |
| #146 [bybit/other] Notice of Suspension for TRON (TRX) Network Deposit and Withdrawal (Nov 10, 2026) | #63 [bitget/other] CandyBomb x MTX：交易瓜分 50,000 USDT |
| #147 [bybit/other] Bybit to Support Enjin (ENJ) v1.8.1 Network Upgrade | #65 [bitget/other] CFD 超级星期三（第3期）：竞猜 XAUUSD 涨跌，瓜分最高 $10,000 奖池！ |
| #149 [okx/other] OKX 关于 SNDK 拆股及相关现货、永续合约与 Xperp 产品调整的公告 | #66 [binance/other] Binance Convert and Buy/Sell Now Support Fiat Purchases of bStocks |
| #159 [bitget/other] Bitget announcement on suspending ANKR - Ethereum deposit service | #67 [gate/other] Futures Comeback: Trade to Get 100 USDT, Plus Up to 556 USDT in Airdrop Rewards |
| #167 [bybit/other] Risk limit adjustment for selected Perpetual Contracts STRKUSDT,ZROUSDT...(Nov 10, 2026) | #68 [gate/other] Gate ETF Treasure Hunt Edition 9: Up to 88 USDT per Draw & Trade for up to 900 USDT |
| #176 [gate/other] Gate 关于对 SOXL3L、TRUMP5L 等 10 个 ETF 进行份额合并的公告 | #69 [gate/other] VIP CFD Grand Prix: Share a 50,000 USDT Cash Prize Pool, Win the Latest MacBook & Exclusive Merch |
| #185 [bitget/other] [Important] Bitget Announcement on Cash Dividend Settlement for AAPLUSDT Stock Perps | #70 [gate/other] CFD New User Cash Airdrop: Claim 3 USDT on First Trade + 10 USDT via Daily Check-ins |
| #192 [bitget/other] Bitget announcement on suspending CC - CANTON Network deposit service | #71 [gate/other] Convert Promotion Phase 2: Sign Up for $8 BTC, Share 100,000 USDT Prize Pool |
| #193 [bitget/other] Bitget Announcement on Maintenance of HIVE Spot Trading Pair | #72 [gate/other] Bot Hot Token Challenge 4, Trade Trending Tokens to Share 20,000 USDT |
| #195 [bitget/other] Bitget to adjust funding rate interval for HOTUSDT perpetual futures | #74 [gate/other] Gate ETF Golden Rewards: XAUT Rewards for Futures Points Users, Stock Traders & VIP5+ |
| #200 [bitget/other] Bitget announcement on resuming USDC - AVAX C-Chain withdrawals | #75 [gate/other] Copy Trading Summit Plan: Compete on Two Leaderboards to Win Cash |
| #201 [bitget/other] Bitget announcement on resuming USDC - APTOS withdrawals | #76 [binance/other] Fund Your First Recurring Buy - Deposit USD and Earn Rewards |
| #202 [bitget/other] Bitget announcement on suspending CC - CANTON Network network withdrawal service | #77 [gate/other] A-Share Futures Trading Carnival: Get 3 USDT on Your First Order and Win Up to 8,000 USDT |
| #204 [bitget/other] Bitget announcement on suspending USDC - AVAX C-Chain network withdrawal service | #80 [bitget/other] BDT & PKR Exclusive: Spend $1 to Receive 10,000USDT Rewards! |
| #205 [bitget/other] Bitget announcement on resuming rJEPQ - Arbitrum One withdrawals | #82 [gate/other] Gate Web3 Bitway Perp Trading Competition: Trade BTW and Share 20,000 BTW |
| #206 [bitget/other] Bitget announcement on resuming XTZ - Tezos withdrawals | #83 [gate/other] Trenches Hot Tokens Competition Phase 69: Trade mihao and 牛逼 to Share a 20,000 USDT Airdrop |
| #208 [bitget/other] Bitget announcement on suspending USDT - APTOS network withdrawal service | #86 [binance/other] Binance Earn Yield Arena: Earn Up to 20% APR With This Week’s New Limited-Time Offers! (2026-11-11) |
| #210 [bitget/other] Bitget announcement on suspending USDC - APTOS network withdrawal service | #87 [gate/other] Gate Futures Meme Zone Now Supports Futures Trading, Trading Bots, and Copy Trading for MIHAO |
| #214 [bitget/other] Bitget announcement on resuming USDT - APTOS withdrawals | #88 [bitget/other] Bitget spot copy trading now supports 2 trading pairs |
| #216 [bitget/other] [Important] Bitget Announcement on Cash Dividend Settlement for ETNUSDT、IBMUSDT、VUSDT Stock Perps | #91 [gate/other] Gate Institutional Weekly: ETF Outflows Intensify, On-Chain Liquidity Divergence Persists (November... |
| #225 [bitget/other] Bitget announcement on suspending USDT - APTOS network withdrawal service | #93 [binance/other] Binance Academy Bitcoin Page: Complete Quiz to Earn BTC Rewards This November 2026! |
| #226 [bybit/other] Bybit AI maintenance | #94 [bitget/other] Bitget spot copy trade now supports rAVPT/USDT |
| #227 [gate/other] Gate Announcement on Adjustments for the SIBERIAN MINING Stock Consolidation | #95 [bitget/other] Bitget spot copy trade now supports rPLSE/USDT |
| #228 [gate/other] Gate Announcement on Dividend Settlement for STRC Perpetual Futures | #96 [bitget/other] Bitget spot copy trade now supports rUPBD/USDT |
| #229 [gate/other] Gate Announcement on Dividend Settlement for STRC, V and IBM Perpetual Futures | #97 [bitget/other] Bitget spot copy trading now supports 2 trading pairs |
| #231 [bitget/other] Bitget announcement on resuming HUMA - Solana withdrawals | #100 [gate/other] Gate Research: Has the Privacy Sector Entered a Broad-Based Recovery after ZEC's Repricing |
| #236 [gate/other] Gate to Support the SanDisk (SNDK) Stock Split and Adjust Related Trading Services | #101 [bitget/other] Elite Trading Position Voucher Guide |
| #237 [bitget/other] Bitget CFD Leverage Adjustment in the Second Week of November 2026 | #102 [binance/other] Binance Futures Will End Last Price Protected Period on USDⓈ-Margined BULLAUSDT Perpetual Contract (2026-11-10) |
| #238 [bybit/other] Notice on adjustment to maximum spot trade quantity | #105 [bitget/other] Bitget spot copy trade now supports rARR/USDT |
| #244 [bitget/other] Bitget CFD 合约 系统维护通知 | #106 [gate/other] Perp Hot Tuesday Round 7: Trade popular coins to Unlock Up to 100 USDT in Rewards |
| #248 [bitget/other] Bitget announcement on resuming PENDLE - Arbitrum One withdrawals | #108 [gate/other] Trade CFDs for the Gold Airdrop: Rewards Worth 880,000 USDx, Up to 3,712 USDT Worth of XAUT |
| #249 [bitget/other] Bitget announcement on resuming PARTI - BASE withdrawals | #110 [binance/other] Important: Transactions with Certain Crypto Platforms |
| #251 [bybit/other] Changes of Max. Order Size for USDT Perpetual Contracts Nov 9, 2026 | #111 [gate/other] SOXL, SOXS & QQQ Futures Trading Competition: 3 USDT on Your First Trade, Up to 8,000 USDT in Reward... |
| #252 [bitget/other] Bitget to support margin functions for select stock tokens in unified account | #112 [gate/other] Convert & Auto-Invest Benefits Phase 12: 300 USDT First-Trade Bonus, Up to 1,300 USDT |
| #255 [bitget/other] Bitget announcement on resuming ATOM - COSMOS withdrawals | #113 [gate/other] Futures Check-In Gold Edition: Trade Daily for USDT, Unlock Advanced Draws for XAUT Prizes |
| #257 [bitget/other] Bitget announcement on resuming HUMA - Binance Smart Chain withdrawals | #114 [gate/other] Gate ETF New Trader Rewards: Register to Get 10 USDT. Trade for Up to 600 WLFI |
| #260 [bitget/other] Bitget announcement on resuming ZEC - Zcash deposits and withdrawals | #115 [binance/other] Celebrating the New Year: Become A Binance VIP To Unlock Exclusive Gifts |
| #261 [bitget/other] Bitget announcement on resuming HAEDAL - SUI withdrawals | #116 [gate/other] Staking New User Rewards Phase 6: Enjoy 100% APR on USDT |
| #262 [bitget/other] Bitget announcement on resuming FET - Ethereum withdrawals | #119 [bitget/other] Bitget spot copy trade now supports rHROW/USDT |
| #263 [bitget/other] Bitget announcement on suspending QUBIC - QUBIC network withdrawal service | #120 [gate/other] Gate Futures Points Airdrop #166: Claim 0.07 NVDAG & 100 USDT |
| #266 [bitget/other] Bitget to adjust funding rate interval for HOTUSDT perpetual futures | #121 [gate/other] New User Kickoff: Get a Free Voucher and Earn Up to 213 USDT in Cash |
| #267 [bitget/other] Bitget announcement on suspending HUMA - Solana network withdrawal service | #124 [gate/other] Gate Research: October Risk Appetite Weakens Again as ETF Outflows Weigh on Market Breadth |
| #268 [binance/other] Recovery Update #9: Withdrawals for More Assets to Reopen (2026-11-06) | #125 [gate/other] Gate Prediction Markets Crypto Campaign: First-Trade Loss Coverage for New Users and 20,000 USDT in... |
| #269 [bitget/other] Bitget announcement on resuming PYUSD - Solana withdrawals | #126 [gate/other] Gate Card Benefits Upgrade: 50% Monthly Cashback on AI Subscriptions |
| #271 [gate/other] Gate Layer Mainnet Upgrade v2.0.1 | #127 [gate/other] Gate Research: U.S. Stocks Rebound Across the Board, BTC Fluctuates Around 75,000 USDT |
| #272 [bitget/other] Bitget to update EIGEN,JUP,NIGHT,ETHFI,TAO,SKY,ZRO collateral ratio for UTA | #129 [bitget/other] Bitget spot copy trading now supports 5 trading pairs |
| #275 [bitget/other] Bitget announcement on suspending ZEC - Zcash deposit and withdrawal services | #131 [bitget/other] Bitget spot copy trade now supports rCLBT/USDT |
| #281 [bitget/other] Bitget announcement on suspending ATOM - COSMOS network withdrawal service | #132 [binance/other] Binance’s Community 3rd Appreciation Campaign |
|  | #134 [bitget/other] Bitget spot copy trade now supports rSMA/USDT |
|  | #135 [bitget/other] Bitget spot copy trading now supports 2 trading pairs |
|  | #136 [bitget/other] Bitget spot copy trading now supports 2 trading pairs |
|  | #137 [bitget/other] Bitget spot copy trading now supports 4 trading pairs |
|  | #138 [bitget/other] Bitget spot copy trading now supports 6 trading pairs |
|  | #139 [bitget/other] Bitget spot copy trade now supports rCWB/USDT |
|  | #140 [bitget/other] Bitget spot copy trade now supports rESE/USDT |
|  | #141 [bybit/other] Evaluate Master Trader Performance with Advanced Filters |
|  | #143 [bitget/other] Bitget spot copy trade now supports rBGC/USDT |
|  | #144 [gate/other] Elite Trader Championship Season 2: Compete for a Share of 500,000 USDT and Climb the Rankings for a... |
|  | #145 [bitget/other] Bitget spot copy trade now supports rCHCO/USDT |
|  | #148 [bitget/other] Fiat Lucky Draw: Win Rewards worth 50,000 USDT! |
|  | #150 [bitget/other] Bitget spot copy trade now supports rBYDDY/USDT |
|  | #151 [bitget/other] Bitget 理财狂欢季：净充值达标，享最高 10% APR |
|  | #152 [bitget/other] Candybomb 嘉年华（第12期）：交易瓜分 80,000 USDT！ |
|  | #153 [gate/other] SOXL Trial Hub : First Trade 10 USDT, Trade More for Up to 100 USDT |
|  | #154 [gate/other] Convert Lucky Draw Spree: Unlock Mystery Boxes with $1, Win Up to $1,888 in SOL |
|  | #155 [gate/other] Options Account Upgrade to Unified Account Mode |
|  | #156 [bitget/other] Bitget CFD Championship (phase 21): Four reward pools, 75,000 USDT to share |
|  | #157 [bybit/other] Explore Bybit Lite Loan: No Liquidation for 30 days |
|  | #158 [bybit/other] RWA Earn Introduces JPE: Access SBI's Japan High-Dividend Equity Strategy |
|  | #160 [bitget/other] Bitget spot copy trade now supports rKRMN/USDT |
|  | #161 [gate/other] JCT & CLAUDIA Futures Hot Coin Airdrop: Register to Get 5 USDT, Up to 240 USDT Per Person |
|  | #162 [gate/other] NVDA Futures Trading Carnival: 3 USDT on Your First Trade, Up to 8,000 USDT in Rewards |
|  | #163 [gate/other] Dual Investment Challenge Phase 6: Up to $4,000 |
|  | #164 [bitget/other] Bitget spot copy trade now supports rGGAL/USDT |
|  | #165 [gate/other] Gate ETF Returning Trader Rewards: Get 10 USDT on Your First Trade, Up to 120 USDT Total |
|  | #166 [gate/other] Gate Announcement on the HONX and LLYX Stock Token Dividend |
|  | #168 [gate/other] Gate Futures Meme Zone Now Supports Futures Trading, Trading Bots, and Copy Trading for 牛逼 |
|  | #169 [gate/other] Gate Stocks Dividend Distribution Completed |
|  | #170 [gate/other] Gold & Silver vs. BTC & ETH: Peak Showdown, Win 68,000 USDT Grand Prize Pool |
|  | #171 [gate/other] GT Holding Carnival: Boost Your Rewards With Futures Trading, Hold GT to Share 3,000 GT |
|  | #172 [gate/other] Trenches Dual-Chain PK Contest: Base VS Robinhood, Compete for 30,000 USDT |
|  | #173 [binance/other] Introducing Sub-Affiliate: Flexible Commission Sharing on Binance |
|  | #174 [bybit/other] Bybit UTA Auto-Repayment Now Available: Trade, Repay, and Save on Interest |
|  | #175 [bitget/other] Bitget to extend Anthropic IPO Prime VIP subscription round |
|  | #177 [gate/other] Same-Day Expiry Options Profit Boost |
|  | #178 [gate/other] CandyDrop 191st: CandyDrop x  ValueX (VLX) , Trade and Invite Friends to Share 227,273 VLX |
|  | #179 [bitget/other] Bitget spot copy trading now supports 3 trading pairs |
|  | #180 [gate/other] Gate 研究院：全球科技股承压，加密市场遭遇逾 10 亿美元多头清算 |
|  | #181 [gate/other] Gate ETH Staking Product Upgrade Announcement |
|  | #182 [binance/other] Word of the Day: Test Your Knowledge on “Choose Your Binance AI” to Unlock BNB Rewards! |
|  | #183 [bitget/other] Bitget spot copy trade now supports rPRI/USDT |
|  | #184 [gate/other] Gate Research: October 2026 Crypto Market Review |
|  | #186 [bitget/other] Bitget spot copy trade now supports rTLK/USDT |
|  | #187 [bitget/other] Bitget spot copy trading now supports 5 trading pairs |
|  | #188 [bitget/other] Bitget spot copy trade now supports rMCY/USDT |
|  | #189 [bitget/other] Bitget spot copy trading now supports 3 trading pairs |
|  | #190 [bitget/other] Announcement on Resumption of HIVE Spot Trading |
|  | #191 [bitget/other] Bitget spot copy trade now supports rALHC/USDT |
|  | #194 [bitget/other] Bitget spot copy trade now supports rCE/USDT |
|  | #196 [bitget/other] Bitget spot copy trade now supports rMPT/USDT |
|  | #197 [bitget/other] Bitget spot copy trading now supports 2 trading pairs |
|  | #198 [bitget/other] Bitget spot copy trade now supports rWES/USDT |
|  | #199 [bitget/other] Bitget spot copy trading now supports 6 trading pairs |
|  | #203 [bitget/other] Bitget spot copy trade now supports rDORM/USDT |
|  | #207 [bitget/other] Bitget spot copy trade now supports rLEA/USDT |
|  | #209 [bitget/other] Bitget spot copy trading now supports 2 trading pairs |
|  | #211 [bitget/other] Bitget spot copy trade now supports rTGB/USDT |
|  | #212 [bitget/other] Bitget spot copy trade now supports rBXMT/USDT |
|  | #213 [bitget/other] Bitget spot copy trade now supports rHHH/USDT |
|  | #215 [bitget/other] Bitget spot copy trade now supports rNKTR/USDT |
|  | #217 [bitget/other] Bitget spot copy trade now supports rDLO/USDT |
|  | #218 [bitget/other] Bitget spot copy trade now supports rAEO/USDT |
|  | #219 [bitget/other] Bitget spot copy trading now supports 7 trading pairs |
|  | #220 [bitget/other] Bitget spot copy trading now supports 2 trading pairs |
|  | #221 [bitget/other] Bitget spot copy trading now supports 4 trading pairs |
|  | #222 [bitget/other] Bitget spot copy trading now supports 6 trading pairs |
|  | #223 [bitget/other] Bitget spot copy trading now supports 3 trading pairs |
|  | #224 [bitget/other] Bitget spot copy trade now supports rLOPE/USDT |
|  | #230 [bitget/other] Bitget spot copy trading now supports 2 trading pairs |
|  | #232 [gate/other] Gate Research: Agentic Trading Gains Momentum, AI Reshapes Crypto Trading and Value Capture |
|  | #233 [bitget/other] Bitget spot copy trading now supports 2 trading pairs |
|  | #234 [gate/other] Gate Announcement on the AAPLON, AAPLX, LLYON and SBUXON Stock Token Dividend |
|  | #235 [bitget/other] Bitget spot copy trade now supports rCPT/USDT |
|  | #239 [gate/other] Weekend Power Station: Register to Claim 385 ENA, Trade to Earn Another 815 ENA |
|  | #240 [gate/other] Master Grid Bots in 1 Minute 17, Zero Cost, New Bot Users Only |
|  | #241 [bitget/other] Bitget spot copy trade now supports rONTO/USDT |
|  | #242 [gate/other] SKHYNIX、SAMSUNG 和 KORU 合约交易赛：报名领 5 USDT，单人最高领 240 USDT |
|  | #243 [gate/other] Gate Research: Vaults Professionalize On-Chain Asset Management, Gate Expands Multi-Asset Wealth Man... |
|  | #245 [gate/other] Swap 7-day Arena: Share 1,000 USDT (W3) |
|  | #246 [gate/other] Gate ETF Boosted Rewards: Trade to Earn, Boost Your Points & Unlock up to 550 USDT |
|  | #247 [gate/other] Top Crypto Rewards: Invite Friends to Trade Futures and Win a Share of $50,000 in BTC and ETH |
|  | #250 [bitget/other] Bitget CFD 2026 年11月节假日交易时间调整 |
|  | #253 [bitget/other] Bitget spot copy trade now supports rMITSY/USDT |
|  | #254 [bitget/other] How Do I Set Up Futures Copy Trading? |
|  | #256 [bitget/other] Onchain trading challenge round 133 (SOL)—share 30,000 USDT |
|  | #258 [gate/other] Trenches 7-Day Check-in Sprint Season 2: Check In & Rank for 40,000 USDT, Win up to 2,000 USD |
|  | #259 [gate/other] Gate Futures Points Airdrop #165: Claim 0.018 QQQG & 100 USDT |
|  | #264 [gate/other] Gate Research: Alpenglow Reshapes Solana: Can Higher Performance Drive Stronger Value Capture |
|  | #265 [gate/other] Gate App Welcome Rewards: Download the App and Complete Tasks to Unlock 100 DOGE |
|  | #270 [bitget/other] Bitget spot copy trading now supports 2 trading pairs |
|  | #273 [bitget/other] Bitget spot copy trade now supports rSOXX/USDT |
|  | #274 [bitget/other] Bitget spot copy trading now supports 4 trading pairs |
|  | #276 [bitget/other] Bitget spot copy trading now supports 8 trading pairs |
|  | #277 [bitget/other] Bitget spot copy trading now supports 20 trading pairs |
|  | #278 [bitget/other] Bitget spot copy trading now supports 20 trading pairs |
|  | #279 [bitget/other] Bitget spot copy trading now supports 20 trading pairs |
|  | #280 [bitget/other] Bitget spot copy trading now supports 20 trading pairs |
|  | #282 [bitget/other] Bitget spot copy trading now supports 20 trading pairs |
