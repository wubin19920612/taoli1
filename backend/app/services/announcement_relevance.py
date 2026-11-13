"""Title/category rules for the opt-in OTHER announcement alert path."""

import re

# Regex fragments are kept as module constants so new exchange wording can be
# added without changing the alert workflow. English terms use word boundaries
# to avoid matching e.g. LEARNUSDT, Clearpool or earnings as promotional content.
ARBITRAGE_RELEVANT_PATTERNS = (
    # Deposit/withdrawal notices require both a service and a status change.
    (
        r"^(?=.*\b(?:deposits?|withdraw(?:al)?s?)\b)"
        r"(?=.*\b(?:suspend\w*|suspension|resum\w*|resumption|halt\w*|reopen\w*|"
        r"open(?:s|ed|ing)?|clos(?:e|es|ed|ing|ure))\b)"
    ),
    r"^(?=.*(?:充值|充币|提币|提现|充提))(?=.*(?:暂停|恢复|开放|开启|关闭|停止))",
    r"\b(?:network upgrades?|hard forks?|mainnet|migration)\b",
    r"网络升级|硬分叉|主网|迁移",
    r"\b(?:funding (?:rate|fee|interval|cap)s?|settlement)\b",
    r"资金费率|资金费用|结算周期",
    (
        r"\b(?:contract size|specifications?|multiplier|tick size|"
        r"step size|size decimal places|price precision|"
        r"(?:minimum|min\.|maximum|max\.) order (?:size|quantity|value)|"
        r"maximum spot trade quantity|leverage|margin|collateral ratio|"
        r"position limits?|risk limits?)\b"
    ),
    r"合约规格|合约面值|面值调整|乘数|最小下单|最小订单|价格精度|杠杆|保证金|限仓|风险限额",
    (
        r"\b(?:stock split|reverse split|split adjustment|stock consolidation|"
        r"share capital adjustment|rebase)\b"
    ),
    r"拆股|合股|股票分割|份额合并",
    r"\b(?:delist\w*|removal|ceas(?:e|es|ed|ing)|terminat(?:e|es|ed|ing|ion)|delivery)\b",
    r"\bremov(?:e|es|ed|ing)\b.*\btrading pairs?\b",
    r"下架|停止交易|移除交易对|合约交割",
    r"\b(?:maintenance|system upgrades?)\b",
    r"\bupgrad(?:e|es|ed|ing)\b.*\b(?:perpetual futures|trading system)\b",
    r"维护|升级",
    r"\b(?:token swap|rebrand\w*|ticker change|renam(?:e|es|ed|ing))\b",
    r"换币|更名",
    r"\b(?:index price|mark price|futures index|contract index)\b",
    r"指数(?:价格)?(?:临时)?调整|合约指数|标记价格",
)

ARBITRAGE_EXCLUDED_PATTERNS = (
    (
        r"\b(?:campaigns?|airdrops?|rewards?|giveaways?|live streams?|livestreams?|"
        r"carnivals?|competitions?|championships?|contests?|invit\w*|copy trad(?:e|es|ing)|"
        r"earn(?:s|ed|ing)?|learn(?:s|ed|ing)?|leaderboards?|proof of reserves|"
        r"candydrop|candybomb|vouchers?|frenzy|advertis\w*)\b"
    ),
    r"\bfiat(?: to crypto| deposits?| on ramp)\b",
    r"\bshare\b.*\b(?:usdt|usdc|prize pool)\b",
    r"活动|空投|奖励|抽奖|瓜分|直播|嘉年华|竞赛|大赛|交易赛|邀请|跟单|理财|学习|排行榜|储备金|广告|法币入金",
)

_RELEVANT_RULES = tuple(re.compile(pattern) for pattern in ARBITRAGE_RELEVANT_PATTERNS)
_EXCLUDED_RULES = tuple(re.compile(pattern) for pattern in ARBITRAGE_EXCLUDED_PATTERNS)


def is_arbitrage_relevant_announcement(
    title: str,
    title_zh: str | None = None,
    category: str | None = None,
) -> bool:
    """Keep operational notices, with promotions taking precedence in any field.

    Unknown titles fail closed. This only decides relevance; exchange scope,
    announcement kind, age, deduplication and reminder policies live elsewhere.
    """
    text = " ".join((title, title_zh or "", category or "")).casefold()
    text = re.sub(r"[\s_\-]+", " ", text)
    return (
        not any(rule.search(text) for rule in _EXCLUDED_RULES)
        and any(rule.search(text) for rule in _RELEVANT_RULES)
    )
