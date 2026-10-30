from app.services.market_identity_catalog import (
    BITGET_COINS,
    BITGET_CONTRACTS,
    GATE_COINS,
    GATE_CONTRACTS,
    catalog_bindings,
)


def test_same_ticker_is_not_merged_when_official_contract_addresses_differ():
    data = {
        GATE_COINS: [
            {
                "currency": "PRL",
                "name": "Perle",
                "chains": [{"name": "SOL", "addr": "PERLEQKUNUp1dgFZ8EvyXHdN9d6ZQqfGxALDvfs6pDs"}],
            }
        ],
        BITGET_COINS: {
            "data": [
                {
                    "coin": "PRL",
                    "chains": [
                        {
                            "chain": "SOL",
                            "contractAddress": "85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ",
                        }
                    ],
                }
            ]
        },
        GATE_CONTRACTS: [{"name": "PRL_USDT", "type": "direct", "quanto_multiplier": "10"}],
        BITGET_CONTRACTS: {"data": [{"symbol": "PRLUSDT", "baseCoin": "PRL", "quoteCoin": "USDT"}]},
    }
    rows = catalog_bindings(data)
    assert len(rows) == 1 and rows[0].market.exchange == "gate"


def test_chain_aliases_match_but_solana_address_case_is_preserved():
    data = {
        GATE_COINS: [
            {
                "currency": "ABC",
                "name": "Example",
                "chains": [{"name": "ETH", "addr": "0x" + "A" * 40}],
            }
        ],
        BITGET_COINS: {
            "data": [
                {
                    "coin": "OTHER",
                    "chains": [{"chain": "ERC20", "contractAddress": "0x" + "a" * 40}],
                }
            ]
        },
        GATE_CONTRACTS: [{"name": "ABC_USDT", "type": "direct", "quanto_multiplier": "10"}],
        BITGET_CONTRACTS: {
            "data": [{"symbol": "OTHERUSDT", "baseCoin": "OTHER", "quoteCoin": "USDT"}]
        },
    }
    rows = catalog_bindings(data)
    assert len(rows) == 2 and rows[0].asset_id == rows[1].asset_id
    assert rows[0].contract_size == 10
    assert all(row.price_multiplier == 1 for row in rows)


def test_address_ambiguity_does_not_automatically_select_one_project():
    data = {
        GATE_COINS: [
            {
                "currency": "BTC",
                "name": "Bitcoin",
                "chains": [{"name": "ETH", "addr": "0x" + "a" * 40}],
            },
            {
                "currency": "SCAM",
                "name": "Other project",
                "chains": [{"name": "ETH", "addr": "0x" + "a" * 40}],
            },
        ],
        BITGET_COINS: {
            "data": [
                {"coin": "SCAM", "chains": [{"chain": "ERC20", "contractAddress": "0x" + "a" * 40}]}
            ]
        },
        BITGET_CONTRACTS: {
            "data": [{"symbol": "SCAMUSDT", "baseCoin": "SCAM", "quoteCoin": "USDT"}]
        },
    }
    assert catalog_bindings(data) == []


def test_placeholder_addresses_never_establish_an_asset_identity():
    data = {
        GATE_COINS: [
            {
                "currency": "OPENAI",
                "name": "OpenAI",
                "chains": [
                    {"name": "SOL", "addr": "invalid-OPENAI-8700368-380"},
                    {"name": "ETH", "addr": "0x1234"},
                ],
            }
        ],
        GATE_CONTRACTS: [{"name": "OPENAI_USDT", "type": "direct", "quanto_multiplier": "1"}],
    }
    assert catalog_bindings(data) == []
