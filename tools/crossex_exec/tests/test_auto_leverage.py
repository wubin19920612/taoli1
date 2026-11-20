"""Opening review against synthetic private HTTP and the regular local journal."""
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.crossex_exec.accounts import Accounts, FeeSnapshot
from tools.crossex_exec.adapters import Bitget, CrossEx
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.market import native_instrument
from tools.crossex_exec.policy import Instrument
from tools.crossex_exec.security import Credentials
from tools.crossex_exec.storage import Store
from tools.crossex_exec.transport import APIError, Transport
from tools.fill_sim.network import DeadlineSocket
from baseline_fixtures import seed_baseline
from round18_fixtures import OfflineAccountHTTP
from test_engine import FakeMarket, trigger
from test_exec import FakeVenue, instrument, setUpModule, tearDownModule
from test_round2 import settings
from test_round11_external import rule as crossex_rule


GATE = instrument().symbol
BITGET = instrument('bitget').symbol
GATE_LEVERAGE = '/api/v4/crossex/positions/leverage'
GATE_POSITIONS = '/api/v4/crossex/positions'
GATE_ORDERS = '/api/v4/crossex/open_orders'
BG_LEVERAGE = '/api/v3/account/set-leverage'
BG_SETTINGS = '/api/v3/account/settings'
BG_POSITIONS = '/api/v3/position/current-position'
BG_ORDERS = '/api/v3/trade/unfilled-orders'
BG_STRATEGIES = '/api/v3/trade/unfilled-strategy-orders'


class LeverageHTTP(OfflineAccountHTTP):
    def __init__(self):
        super().__init__()
        self.responses.update({GATE_LEVERAGE: {GATE: '10'}, BG_SETTINGS: settings(), BG_STRATEGIES: []})
        self.writes = []
        self.apply_write = True
        self.gate_post_response = None
        self.after_admit = lambda: None
        self.after_read = lambda path: None
        self.before_write = lambda: None
        self.write_error = None

    def request(self, method, path, params=None, *, body=None, before_send=None, group='query', **kwargs):
        if method == 'GET':
            if isinstance(self.responses.get(path), Exception):
                raise self.responses[path]
            data = super().request(method, path, params, **kwargs)
            self.after_read(path)
            return data
        assert method == 'POST' and path in (GATE_LEVERAGE, BG_LEVERAGE)
        assert group == 'write'
        self.after_admit()
        before_send()
        before_send.on_send()
        self.before_write()
        self.writes.append((path, body))
        if self.write_error:
            raise self.write_error
        if path == GATE_LEVERAGE:
            if self.apply_write:
                self.responses[path][body['symbol']] = body['leverage']
            return dict(body) if self.gate_post_response is None else self.gate_post_response
        if self.apply_write:
            for row in self.responses[BG_SETTINGS]['symbolConfigList']:
                if row['symbol'] == body['symbol'] and row['category'] == body['category']:
                    row['leverage'] = body['leverage']
        return 'success'


@pytest.fixture
def http():
    return LeverageHTTP()


@pytest.fixture
def engine(http):
    market = FakeMarket()
    accounts = Accounts(CrossEx(http), Bitget(http), risk_confirmed=True)
    now = time.time()
    accounts.fee_snapshots = {k: FeeSnapshot('.0002', now) for k in market.mapping}
    accounts.leverages = dict(http.responses[GATE_LEVERAGE])
    with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp)/'exec.db') as store:
        runtime = Engine(store, market, accounts, live=True, stop_file=Path(tmp)/'STOP',
                         config={'CROSSEX_RISK_SCOPE': 'dedicated_account'})
        snapshot = accounts.snapshot()
        runtime.limits.initialize(snapshot)
        runtime.account_cache.publish(snapshot)
        runtime.risk = 'RUN'
        runtime.last_reconcile = now
        store.set_meta('mapping', {'at': now})
        seed_baseline(store, now)
        runtime.baseline.record(market.baseline_quotes(('BTC',)), ('BTC',))
        http.calls.clear()
        try:
            yield runtime
        finally:
            runtime.close()


def events(engine, kind):
    return [json.loads(row['value']) for row in engine.store.events() if row['kind'] == kind]


def reviewed(engine):
    # Stop at the existing executor boundary. Validate the two actual intents
    # produced by this trigger without submitting even a simulated order.
    with patch.object(engine.executor, 'execute_pair', return_value=[]) as execute, patch.object(engine, 'balance'):
        pair = engine.ingest(trigger())
    execute.assert_called_once()
    assert pair['trigger_id'] == 'shadow-1'
    assert [o['instrument']['symbol'] for o in execute.call_args.args[0]] == [GATE, BITGET]
    assert not events(engine, 'SKIP')


def rejected(engine, reason, identity='rejected', *, expected_locks=()):
    engine.store.set_meta('single_failures', 1)
    with (patch.object(engine, 'balance') as balance, patch.object(engine, 'flatten_all') as flatten,
          patch.object(engine, 'alert') as alert, patch.object(engine.executor, 'execute_pair') as execute):
        assert engine.ingest(trigger(identity)) is None
        for action in (balance, flatten, alert, execute):
            action.assert_not_called()
    assert events(engine, 'SKIP')[-1]['reason'] == reason
    assert set(engine.stops.reasons) == set(expected_locks) and not events(engine, 'ALERT')
    assert not engine.store.pairs() and not engine.store.all_orders()
    assert engine.store.meta('single_failures') == 1


def test_gate_sets_full_symbol_and_continues_this_review(engine, http):
    http.responses[GATE_POSITIONS] = [dict(symbol='OKX_FUTURE_BTC_USDT', position_qty='4'),
                                      dict(symbol=GATE, position_qty='0')]
    http.responses[GATE_ORDERS] = [dict(symbol='BYBIT_FUTURE_BTC_USDT')]
    reviewed(engine)
    assert http.writes == [(GATE_LEVERAGE, dict(symbol=GATE, leverage='3'))]
    assert engine.accounts.leverages[GATE] == '3'
    assert ('GET', GATE_LEVERAGE, {'symbols': GATE}) == http.calls[-1]
    assert events(engine, 'LEVERAGE_SET') == [dict(exchange='crossex', symbol=GATE, before={'leverage': '10'},
            target={'leverage': '3'}, result='success', error=None)]


def catalog_instrument(default_leverage):
    native = dict(symbol='BTCUSDT', status='TRADING', contractType='PERPETUAL', baseAsset='BTC', quoteAsset='USDT',
                  underlyingType='COIN', underlyingSubType=[], filters=[
                      dict(filterType='PRICE_FILTER', tickSize='.01'),
                      dict(filterType='LOT_SIZE', stepSize='.001', minQty='.001', maxQty='1000')])
    matched = crossex_rule(GATE)
    if default_leverage is not None:
        matched['default_leverage'] = default_leverage
    other = dict(crossex_rule('OKX_FUTURE_BTC_USDT'), default_leverage='1')
    return native_instrument('binance', native, [other, matched])


def test_missing_catalog_default_preserves_legacy_and_bitget_rows():
    for selected in (catalog_instrument(None), instrument('bitget')):
        row = selected.dump()
        assert 'default_leverage' not in row
        assert Instrument.load(row).dump() == row


@pytest.mark.parametrize(('raw', 'parsed', 'target'), [
    ('2', '2', '2'), ('1', '1', '1'), ('2.00', '2', '2'), ('3', None, '3'), ('5', None, '3'),
    ('2.5', None, '3'), (None, None, '3'), ('garbage', None, '3'), ('0', None, '3'),
    ('-1', None, '3'), ('NaN', None, '3'),
])
def test_crossex_catalog_default_controls_write_and_audit(engine, http, raw, parsed, target):
    selected = catalog_instrument(raw)
    restored = Instrument.load(selected.dump())
    assert restored.default_leverage == parsed
    engine.market.mapping[restored.market.key] = restored
    engine.accounts.leverages[GATE] = http.responses[GATE_LEVERAGE][GATE] = '5'

    reviewed(engine)

    assert http.writes == [(GATE_LEVERAGE, dict(symbol=GATE, leverage=target))]
    assert http.calls[-1] == ('GET', GATE_LEVERAGE, {'symbols': GATE})
    assert engine.accounts.leverages[GATE] == target
    attempt, = events(engine, 'LEVERAGE_SET_ATTEMPT')
    assert attempt == dict(exchange='crossex', symbol=GATE, before={'leverage': '5'}, target={'leverage': target})
    assert events(engine, 'LEVERAGE_SET') == [dict(attempt, result='success', error=None)]


def test_catalog_refresh_during_review_preserves_selected_cap_and_bitget_target(engine, http):
    selected = catalog_instrument('2')
    refreshed = catalog_instrument('1')
    key = selected.market.key
    engine.market.mapping[key] = selected
    http.responses[BG_SETTINGS]['symbolConfigList'][0]['leverage'] = '5'

    def publish_refresh(path):
        if path == BG_SETTINGS:
            engine.market.mapping = {**engine.market.mapping, key: refreshed}

    http.after_read = publish_refresh
    reviewed(engine)
    assert engine.market.mapping[key] is refreshed
    assert http.writes == [(GATE_LEVERAGE, dict(symbol=GATE, leverage='2')),
                           (BG_LEVERAGE, dict(symbol=BITGET, category='USDT-FUTURES', marginMode='crossed', leverage='3'))]


@pytest.mark.parametrize(('before', 'after', 'changed'), [
    ('2', '2.00', False), (None, '5', False), ('2', '5', True),
])
def test_send_catalog_check_uses_effective_leverage_cap(engine, before, after, changed):
    selected = catalog_instrument(before)
    key = selected.market.key
    engine.market.mapping[key] = selected
    engine.accounts.leverages[GATE] = '3'
    engine.live = engine.executor.live = False
    pair = engine.ingest(trigger())
    assert pair['state'] == 'SHADOW'

    engine.market.mapping[key] = catalog_instrument(after)
    instruments = [Instrument.load(row) for row in pair['instruments']]
    if changed:
        with pytest.raises(ValueError, match='^send_catalog_changed$'):
            engine.check_send_config(pair, instruments)
    else:
        engine.check_send_config(pair, instruments)


def test_crossex_capped_write_accepts_readback_above_catalog_target(engine, http):
    selected = catalog_instrument('1')
    engine.market.mapping[selected.market.key] = selected
    http.apply_write = False
    http.before_write = lambda: http.responses[GATE_LEVERAGE].update({GATE: '3'})

    reviewed(engine)
    assert http.writes == [(GATE_LEVERAGE, dict(symbol=GATE, leverage='1'))]
    assert engine.accounts.leverages[GATE] == '3'


@pytest.mark.parametrize('leverage', ['1', '2', '2.5'])
def test_lower_current_leverage_continues_without_writes_or_exposure_reads(engine, http, leverage):
    # Opening review now accepts the full [1, 3] interval on both legs.
    engine.accounts.leverages[GATE] = http.responses[GATE_LEVERAGE][GATE] = leverage
    http.responses[BG_SETTINGS]['symbolConfigList'][0]['leverage'] = leverage
    reviewed(engine)
    assert [path for _, path, _ in http.calls] == [BG_SETTINGS]
    assert http.writes == events(engine, 'LEVERAGE_SET_ATTEMPT') == []


def test_lower_leverage_on_fresh_reads_avoids_both_writes(engine, http):
    engine.accounts.leverages[GATE] = 'invalid-cache'
    http.responses[GATE_LEVERAGE][GATE] = '1'
    row = http.responses[BG_SETTINGS]['symbolConfigList'][0]
    row['leverage'] = '5'
    # The opening check sees crossed/5. The next Bitget settings read sees crossed/2.
    def lower_bitget_leverage(path):
        if path == BG_SETTINGS:
            row['leverage'] = '2'

    http.after_read = lower_bitget_leverage
    reviewed(engine)
    assert [path for _, path, _ in http.calls] == [BG_SETTINGS, GATE_LEVERAGE, BG_SETTINGS]
    assert http.writes == events(engine, 'LEVERAGE_SET_ATTEMPT') == []
    assert engine.accounts.leverages[GATE] == '1'
    assert engine.accounts.bitget_symbol_configs[BITGET] == dict(leverage='2', marginMode='crossed')


@pytest.mark.parametrize('leverage', ['0', '-1', 'not-a-number', 'NaN', '0.99999999999999999'])
def test_invalid_crossex_current_leverage_skips_without_write(engine, http, leverage):
    engine.accounts.leverages[GATE] = http.responses[GATE_LEVERAGE][GATE] = leverage
    rejected(engine, 'leverage_set_skipped')
    assert [path for _, path, _ in http.calls] == [BG_SETTINGS, GATE_LEVERAGE]
    assert http.writes == events(engine, 'LEVERAGE_SET_ATTEMPT') == []
    assert GATE not in engine.accounts.leverages


@pytest.mark.parametrize('ack_leverage', ['3', '2', '5'])
@pytest.mark.parametrize(('readback', 'result'), [
    ('3', 'success'), ('2', 'success'), ('5', 'failed'), ('3.00000000000000001', 'failed')])
def test_crossex_five_writes_three_and_checks_readback(engine, http, readback, result, ack_leverage):
    leverages = http.responses[GATE_LEVERAGE]
    leverages[GATE] = '5'
    http.gate_post_response = dict(symbol=GATE, leverage=ack_leverage)
    http.apply_write = False
    http.before_write = lambda: leverages.update({GATE: readback})
    if result == 'success':
        reviewed(engine)
        error = None
    else:
        rejected(engine, 'leverage_set_failed')
        error = dict(stage='readback_mismatch', error_kind='unknown')
    assert http.writes == [(GATE_LEVERAGE, dict(symbol=GATE, leverage='3'))]
    assert http.calls[-1] == ('GET', GATE_LEVERAGE, {'symbols': GATE})
    assert engine.accounts.leverages[GATE] == readback
    assert events(engine, 'LEVERAGE_SET') == [dict(exchange='crossex', symbol=GATE, before={'leverage': '5'},
                                                   target={'leverage': '3'}, result=result, error=error)]


@pytest.mark.parametrize('receipt', [
    dict(symbol='OKX_FUTURE_BTC_USDT', leverage='2'), dict(symbol=GATE, leverage='0'),
    dict(symbol=GATE, leverage='-1'), dict(symbol=GATE, leverage='Infinity'), dict(symbol=GATE, leverage='bad')])
def test_crossex_invalid_post_receipt_fails_before_readback(engine, http, receipt):
    http.gate_post_response = receipt
    rejected(engine, 'leverage_set_failed')
    assert http.writes == [(GATE_LEVERAGE, dict(symbol=GATE, leverage='3'))]
    assert http.calls.count(('GET', GATE_LEVERAGE, {'symbols': GATE})) == 1  # Pre-write GET only.
    result, = events(engine, 'LEVERAGE_SET')
    assert result['error']['stage'] == 'write'


@pytest.mark.parametrize('failure', ['stale_readback', 'write_timeout', 'readback_timeout', 'wrong_symbol', 'empty'])
def test_failure_is_an_expected_skip_with_safe_event(engine, http, failure):
    http.apply_write = False
    private = 'fictional-key fictional-secret fictional-signature fictional-passphrase'
    if failure == 'write_timeout':
        http.write_error = TimeoutError(private)
    elif failure == 'readback_timeout':
        http.after_admit = lambda: http.responses.update({GATE_LEVERAGE: TimeoutError(private)})
    elif failure == 'wrong_symbol':
        http.after_admit = lambda: http.responses.update({GATE_LEVERAGE: {'OKX_FUTURE_BTC_USDT': '3'}})
    elif failure == 'empty':
        http.after_admit = lambda: http.responses.update({GATE_LEVERAGE: {}})
    rejected(engine, 'leverage_set_failed')
    assert len(http.writes) == 1
    assert engine.accounts.leverages.get(GATE) != '3'
    event, = events(engine, 'LEVERAGE_SET')
    assert event['result'] == 'failed'
    assert event['error']['stage'] == {'stale_readback': 'readback_mismatch',
        'write_timeout': 'write', 'readback_timeout': 'readback', 'wrong_symbol': 'readback',
        'empty': 'readback_mismatch'}[failure]
    assert all(secret not in str(engine.store.events()) for secret in private.split())
    engine.ingest(trigger('rejected'))
    assert events(engine, 'SKIP') == [dict(id='rejected', reason='leverage_set_failed', stage='review')]
    assert len(http.writes) == 1


@pytest.mark.parametrize(('path', 'rows'), [
    (GATE_POSITIONS, [dict(symbol=GATE, position_side='LONG', position_qty='1')]),
    (GATE_POSITIONS, [dict(symbol=GATE, position_side='SHORT', position_qty='-1')]),
    (GATE_POSITIONS, [dict(symbol=GATE, position_qty='1'), dict(symbol=GATE, position_qty='-1')]),
    (GATE_ORDERS, [dict(symbol=GATE)]), (GATE_POSITIONS, None), (GATE_POSITIONS, [dict(symbol=GATE)]),
    (GATE_ORDERS, None), (GATE_ORDERS, [{}]),
    (BG_POSITIONS, {'list': [dict(symbol=BITGET, posSide='short', total='1')]}),
    (BG_POSITIONS, {}), (BG_POSITIONS, {'list': {}}), (BG_POSITIONS, {'list': ''}), (BG_POSITIONS, {'list': 0}),
    (BG_ORDERS, {'list': [dict(symbol=BITGET)], 'cursor': ''}),
    (BG_ORDERS, {'list': [], 'cursor': 'incomplete'}), (BG_STRATEGIES, [dict(symbol=BITGET)]),
    (BG_STRATEGIES, None),
])
def test_occupied_or_unreadable_contract_skips_without_write(engine, http, path, rows):
    if path.startswith('/api/v3/'):
        engine.accounts.leverages[GATE] = '3'
        http.responses[BG_SETTINGS]['symbolConfigList'][0]['leverage'] = '10'
    http.responses[path] = rows
    rejected(engine, 'leverage_set_skipped')
    assert http.writes == events(engine, 'LEVERAGE_SET') == []
    assert engine.store.meta('leverage_set_window', []) == []
    outcome, = events(engine, 'LEVERAGE_OUTCOME')
    assert outcome['outcome'] == 'leverage_set_skipped' and outcome['exchange'] in ('crossex', 'bitget')
    assert outcome['symbol'] in (GATE, BITGET) and outcome['error_type'] and outcome['location']


def test_leverage_outcome_records_step_and_cause_without_secrets(engine, http):
    private = 'fictional-key fictional-secret fictional-signature'
    http.apply_write = False
    http.write_error = TimeoutError(private)
    rejected(engine, 'leverage_set_failed')
    outcome, = events(engine, 'LEVERAGE_OUTCOME')
    assert outcome['outcome'] == 'leverage_set_failed' and outcome['exchange'] == 'crossex'
    assert outcome['symbol'] == GATE and outcome['step'] == 'set_and_confirm'
    assert outcome['error_type'] == 'TimeoutError' and outcome['location'] and 'cause' not in outcome
    assert all(secret not in str(engine.store.events()) for secret in private.split())


def test_leverage_outcome_diagnostics_failure_keeps_fail_closed_result(engine, http):
    http.responses[GATE_ORDERS] = [dict(symbol=GATE)]
    real_event = engine.store.event
    def event(kind, value):
        if kind == 'LEVERAGE_OUTCOME':
            raise OSError('disk')
        return real_event(kind, value)
    with patch.object(engine.store, 'event', side_effect=event):
        rejected(engine, 'leverage_set_skipped')
    assert http.writes == []


def test_bitget_crossed_hedge_sets_both_sides_once_and_confirms(engine, http):
    engine.accounts.leverages[GATE] = '3'
    row = http.responses[BG_SETTINGS]['symbolConfigList'][0]
    row['leverage'] = '12'
    reviewed(engine)
    assert http.writes == [(BG_LEVERAGE, dict(symbol=BITGET, category='USDT-FUTURES',
                                            leverage='3', marginMode='crossed'))]
    assert http.calls[-1][1] == BG_SETTINGS
    assert engine.accounts.bitget_symbol_configs[BITGET] == dict(leverage='3', marginMode='crossed')
    assert events(engine, 'LEVERAGE_SET')[0]['before'] == dict(leverage='12', marginMode='crossed')


def test_bitget_crossed_five_writes_three_and_confirms_two(engine, http):
    engine.accounts.leverages[GATE] = '3'
    row = http.responses[BG_SETTINGS]['symbolConfigList'][0]
    row['leverage'] = '5'
    http.apply_write = False
    http.before_write = lambda: row.update(leverage='2')
    reviewed(engine)
    target = dict(leverage='3', marginMode='crossed')
    assert http.writes == [(BG_LEVERAGE, dict(symbol=BITGET, category='USDT-FUTURES', **target))]
    assert http.calls[-1][1] == BG_SETTINGS
    assert engine.accounts.bitget_symbol_configs[BITGET] == dict(leverage='2', marginMode='crossed')
    assert events(engine, 'LEVERAGE_SET') == [dict(exchange='bitget', symbol=BITGET,
        before=dict(leverage='5', marginMode='crossed'), target=target, result='success', error=None)]


@pytest.mark.parametrize('other_symbols', [[], ['ETHUSDT']], ids=['empty_list', 'other_contract_only'])
def test_missing_bitget_row_sets_once_and_continues_this_review(engine, http, other_symbols):
    engine.accounts.leverages[GATE] = '3'
    rows = http.responses[BG_SETTINGS]['symbolConfigList']
    configured = rows.pop()
    rows.extend(dict(configured, symbol=symbol) for symbol in other_symbols)
    # Only the settings GET after the POST can observe the new row.
    http.before_write = lambda: rows.append(configured)
    reviewed(engine)
    target = dict(leverage='3', marginMode='crossed')
    assert http.writes == [(BG_LEVERAGE, dict(symbol=BITGET, category='USDT-FUTURES', **target))]
    assert [path for _, path, _ in http.calls].count(BG_SETTINGS) == 3
    assert http.calls[-1][1] == BG_SETTINGS
    assert engine.accounts.bitget_symbol_configs[BITGET] == target
    audit = dict(exchange='bitget', symbol=BITGET, before=dict(leverage=None, marginMode=None), target=target)
    assert events(engine, 'LEVERAGE_SET_ATTEMPT') == [audit]
    assert events(engine, 'LEVERAGE_SET') == [dict(audit, result='success', error=None)]


@pytest.mark.parametrize('readback', [[], [('isolated', '3')], [('crossed', '5')], [('crossed', '3')] * 2],
                         ids=['missing', 'isolated', 'wrong_leverage', 'duplicate'])
def test_missing_bitget_row_requires_exact_readback(engine, http, readback):
    engine.accounts.leverages[GATE] = '3'
    rows = http.responses[BG_SETTINGS]['symbolConfigList']
    configured = rows.pop()
    http.apply_write = False
    http.before_write = lambda: rows.extend(dict(configured, marginMode=mode, leverage=value)
                                            for mode, value in readback)
    rejected(engine, 'leverage_set_failed')
    assert http.writes == [(BG_LEVERAGE, dict(symbol=BITGET, category='USDT-FUTURES',
                                              marginMode='crossed', leverage='3'))]
    assert http.calls[-1][1] == BG_SETTINGS
    result, = events(engine, 'LEVERAGE_SET')
    assert result['result'] == 'failed' and result['error']['stage'] == 'readback_mismatch'
    outcome, = events(engine, 'LEVERAGE_OUTCOME')
    assert (outcome['step'], outcome['stage'], outcome['outcome']) == (
        'set_and_confirm', 'readback_mismatch', 'leverage_set_failed')


@pytest.mark.parametrize('target_present', [False, True], ids=['without_target', 'with_target'])
@pytest.mark.parametrize('bad_row', [
    dict(symbol='ETHUSDT', marginMode='crossed', leverage='3'),
    dict(symbol=None, category='USDT-FUTURES', marginMode='crossed', leverage='3'),
    dict(symbol='', category='USDT-FUTURES', marginMode='crossed', leverage='3'),
    dict(symbol='ETHUSDT', category=None, marginMode='crossed', leverage='3'),
    dict(symbol='ETHUSDT', category='USDT-FUTURES', marginMode=None, leverage='3'),
    dict(symbol='ETHUSDT', category='USDT-FUTURES', marginMode='crossed', leverage='bad'),
], ids=['missing_category', 'null_symbol', 'empty_symbol', 'null_category', 'null_margin_mode', 'bad_leverage'])
def test_bitget_malformed_row_skips_opening_without_writes(engine, http, bad_row, target_present):
    rows = http.responses[BG_SETTINGS]['symbolConfigList']
    if not target_present:
        rows.clear()
    rows.append(bad_row)
    rejected(engine, 'leverage_set_skipped')
    assert [path for _, path, _ in http.calls] == [BG_SETTINGS]
    assert http.writes == events(engine, 'LEVERAGE_SET_ATTEMPT') == []


@pytest.mark.parametrize('bad_row_index', [0, 1])
def test_bitget_valid_and_malformed_readback_rows_fail(engine, http, bad_row_index):
    engine.accounts.leverages[GATE] = '3'
    rows = http.responses[BG_SETTINGS]['symbolConfigList']
    rows[0]['leverage'] = '5'
    bad_row = dict(rows[0], symbol='ETHUSDT', leverage='NaN')
    http.before_write = lambda: rows.insert(bad_row_index, bad_row)
    rejected(engine, 'leverage_set_failed')
    assert http.writes == [(BG_LEVERAGE, dict(symbol=BITGET, category='USDT-FUTURES',
                                              marginMode='crossed', leverage='3'))]
    assert http.calls[-1][1] == BG_SETTINGS
    assert BITGET not in engine.accounts.bitget_symbol_configs
    result, = events(engine, 'LEVERAGE_SET')
    assert result['result'] == 'failed' and result['error']['stage'] == 'readback_mismatch'


def test_missing_bitget_row_after_crossex_repair_attempts_both_writes(engine, http):
    # The original [missing] case also starts with CrossEx at 10.
    http.responses[BG_SETTINGS]['symbolConfigList'].clear()
    rejected(engine, 'leverage_set_failed')
    assert http.writes == [(GATE_LEVERAGE, dict(symbol=GATE, leverage='3')),
                           (BG_LEVERAGE, dict(symbol=BITGET, category='USDT-FUTURES', marginMode='crossed', leverage='3'))]
    assert [(e['exchange'], e['result']) for e in events(engine, 'LEVERAGE_SET')] == [
        ('crossex', 'success'), ('bitget', 'failed')]
    outcome, = events(engine, 'LEVERAGE_OUTCOME')
    assert outcome['symbol'] == BITGET and outcome['stage'] == 'readback_mismatch'


def test_missing_bitget_row_is_rejected_by_default(engine, http):
    assert engine.accounts._bitget_leverage(BITGET) == 3
    http.responses[BG_SETTINGS]['symbolConfigList'].clear()
    assert engine.accounts._bitget_leverage(BITGET, allow_missing=True) is None
    assert BITGET not in engine.accounts.bitget_symbol_configs
    with pytest.raises(ValueError, match='^leverage_set_skipped$'):
        engine.accounts._bitget_leverage(BITGET)
    assert http.writes == events(engine, 'LEVERAGE_SET') == []


@pytest.mark.parametrize(('settings_update', 'cause'), [
    (dict(symbolConfigList=[dict(settings()['symbolConfigList'][0], marginMode='isolated')]), 'not_crossed'),
    (dict(symbolConfigList=settings()['symbolConfigList'] * 2), 'duplicate_rows'),
    (dict(symbolConfigList=[dict(settings()['symbolConfigList'][0], leverage='NaN')]), 'invalid_leverage'),
    (dict(holdMode='one_way_mode', symbolConfigList=[]), 'settings_invalid'),
    (dict(symbolConfigList={}), 'settings_invalid'),
    (dict(symbolConfigList=[{}]), 'settings_invalid'),
    (dict(symbolConfigList=[{'symbol': None}]), 'settings_invalid'),
    (OSError('fictional-key fictional-signature'), 'settings_read_failed'),
    (dict(symbolConfigList=[dict(settings()['symbolConfigList'][0], marginMode='isolated', leverage='2')]), 'not_crossed'),
    (dict(symbolConfigList=[dict(settings()['symbolConfigList'][0], leverage='0.99')]), 'invalid_leverage'),
], ids=['isolated', 'duplicate', 'invalid_leverage', 'account_mode', 'invalid_list', 'invalid_row',
        'invalid_symbol', 'read_error', 'isolated_two', 'below_one'])
def test_bitget_check_instruments_records_safe_skip(engine, http, settings_update, cause):
    if isinstance(settings_update, Exception):
        http.responses[BG_SETTINGS] = settings_update
    else:
        http.responses[BG_SETTINGS].update(settings_update)
    rejected(engine, 'leverage_set_skipped')
    assert http.writes == events(engine, 'LEVERAGE_SET_ATTEMPT') == events(engine, 'LEVERAGE_SET') == []
    outcome, = events(engine, 'LEVERAGE_OUTCOME')
    assert outcome['exchange'] == 'bitget' and outcome['symbol'] == BITGET
    assert outcome['step'] == 'check_instruments' and outcome['stage'] is None
    assert outcome['outcome'] == 'leverage_set_skipped' and outcome['cause'] == cause
    assert outcome['error_type'] and outcome['location']
    assert all(secret not in str(engine.store.events()) for secret in ('fictional-key', 'fictional-signature'))


def test_bitget_check_instruments_rejects_when_outcome_cannot_be_recorded(engine, http):
    http.responses[BG_SETTINGS] = OSError('settings unavailable')
    event = engine.store.event

    def fail_outcome(kind, *args, **kwargs):
        if kind == 'LEVERAGE_OUTCOME':
            raise OSError('journal unavailable')
        return event(kind, *args, **kwargs)

    with patch.object(engine.store, 'event', side_effect=fail_outcome) as record:
        rejected(engine, 'leverage_set_skipped')
    assert [call.args[0] for call in record.call_args_list].count('LEVERAGE_OUTCOME') == 1
    assert http.writes == events(engine, 'LEVERAGE_SET_ATTEMPT') == []


def test_bitget_null_positions_allow_setting(engine, http):
    engine.accounts.leverages[GATE] = '3'
    http.responses[BG_SETTINGS]['symbolConfigList'][0]['leverage'] = '7'
    http.responses[BG_POSITIONS] = {'list': None}
    reviewed(engine)
    assert http.writes == [(BG_LEVERAGE, dict(symbol=BITGET, category='USDT-FUTURES',
                                              leverage='3', marginMode='crossed'))]
    attempt, = events(engine, 'LEVERAGE_SET_ATTEMPT')
    assert attempt == dict(exchange='bitget', symbol=BITGET, before=dict(leverage='7', marginMode='crossed'),
                           target=dict(leverage='3', marginMode='crossed'))
    assert events(engine, 'LEVERAGE_SET')[0]['result'] == 'success'


@pytest.mark.parametrize('new_order', [GATE_ORDERS, BG_ORDERS, BG_STRATEGIES])
def test_order_created_during_position_read_prevents_write(engine, http, new_order):
    bitget = new_order != GATE_ORDERS
    if bitget:
        engine.accounts.leverages[GATE] = '3'
        http.responses[BG_SETTINGS]['symbolConfigList'][0]['leverage'] = '7'
    reads = []
    http.after_admit = lambda: reads.append('admitted')
    positions = BG_POSITIONS if bitget else GATE_POSITIONS

    def add_order(path):
        if path in (GATE_POSITIONS, BG_POSITIONS, GATE_ORDERS, BG_ORDERS, BG_STRATEGIES):
            reads.append(path)
        if path == positions:
            orders = [dict(symbol=BITGET if bitget else GATE)]
            http.responses[new_order] = dict(list=orders, cursor='') if new_order == BG_ORDERS else orders

    http.after_read = add_order
    rejected(engine, 'leverage_set_skipped')
    assert reads[0] == 'admitted' and reads.count(positions) == 1
    assert new_order in reads[1:reads.index(positions)] and new_order in reads[reads.index(positions)+1:]
    assert http.writes == events(engine, 'LEVERAGE_SET_ATTEMPT') == events(engine, 'LEVERAGE_SET') == []


def test_attempt_is_committed_and_visible_before_post(engine, http):
    payload = dict(exchange='crossex', symbol=GATE, before=dict(leverage='10'), target=dict(leverage='3'))
    with Store(engine.stop_file.parent/'exec.db') as observer:
        def before_write():
            rows = [r for r in observer.events() if r['kind'].startswith('LEVERAGE_SET')]
            assert [(r['kind'], json.loads(r['value'])) for r in rows] == [('LEVERAGE_SET_ATTEMPT', payload)]
            assert http.writes == []

        http.before_write = before_write
        reviewed(engine)
    assert events(engine, 'LEVERAGE_SET') == [dict(payload, result='success', error=None)]


@pytest.mark.parametrize(('failed_event', 'write_fails'), [
    ('LEVERAGE_SET_ATTEMPT', False), ('LEVERAGE_SET', False), ('LEVERAGE_SET', True)])
def test_audit_errors_map_to_whether_a_post_was_sent(engine, http, failed_event, write_fails):
    event = engine.store.event
    if write_fails:
        http.write_error = TimeoutError()

    def fail_event(kind, *args, **kwargs):
        if kind == failed_event:
            raise OSError('journal unavailable')
        return event(kind, *args, **kwargs)

    sent = failed_event == 'LEVERAGE_SET'
    with patch.object(engine.store, 'event', side_effect=fail_event):
        rejected(engine, 'leverage_set_failed' if sent else 'leverage_set_skipped')
    assert len(http.writes) == len(events(engine, 'LEVERAGE_SET_ATTEMPT')) == int(sent)
    assert len(engine.store.meta('leverage_set_window', [])) == int(sent)


@pytest.mark.parametrize('delay', ['queue', 'positions', 'audit', 'post', 'readback'])
def test_five_second_limit_at_each_stage(engine, http, delay):
    now = [10000.]
    engine.accounts.clock = lambda: now[0]
    request, event = http.request, engine.store.event

    def slow_request(method, path, *args, **kwargs):
        data = request(method, path, *args, **kwargs)
        if ((delay == 'positions' and path == GATE_POSITIONS) or (delay == 'post' and method == 'POST')
                or (delay == 'readback' and method == 'GET' and path == GATE_LEVERAGE and http.writes)):
            now[0] += 5
        return data

    def slow_event(kind, *args, **kwargs):
        event(kind, *args, **kwargs)
        if delay == 'audit' and kind == 'LEVERAGE_SET_ATTEMPT':
            now[0] += 5

    def slow_queue():
        now[0] += 5

    if delay == 'queue':
        http.after_admit = slow_queue
    sent = delay in ('post', 'readback')
    with patch.object(http, 'request', side_effect=slow_request), patch.object(engine.store, 'event', side_effect=slow_event):
        rejected(engine, 'leverage_set_failed' if sent else 'leverage_set_skipped')
    assert len(http.writes) == int(sent)
    assert [r['result'] for r in events(engine, 'LEVERAGE_SET')] == (['failed'] if sent else [])
    assert engine.accounts.leverages.get(GATE) != '3'
    if delay == 'post':
        assert [path for _, path, _ in http.calls].count(GATE_LEVERAGE) == 1


def test_both_contracts_share_one_deadline(engine, http):
    now = [10000.]
    engine.accounts.clock = lambda: now[0]
    http.responses[BG_SETTINGS]['symbolConfigList'][0]['leverage'] = '7'

    def queue():
        now[0] += 2.6

    http.after_admit = queue
    rejected(engine, 'leverage_set_failed')
    assert [path for path, _ in http.writes] == [GATE_LEVERAGE]
    assert [r['exchange'] for r in events(engine, 'LEVERAGE_SET_ATTEMPT')] == ['crossex']


def test_monotonic_deadline_holds_when_account_clock_does_not_advance(engine, http):
    engine.accounts.clock = lambda: 10000.
    elapsed = [100.]

    def queue():
        elapsed[0] += 5.1

    http.after_admit = queue
    with patch('tools.crossex_exec.accounts.time') as clock:
        clock.monotonic.side_effect = lambda: elapsed[0]
        rejected(engine, 'leverage_set_skipped')
    assert http.writes == events(engine, 'LEVERAGE_SET_ATTEMPT') == []


def test_bitget_stops_pagination_when_total_budget_expires(engine, http):
    now = [10000.]
    engine.accounts.clock = lambda: now[0]
    engine.accounts.leverages[GATE] = '3'
    http.responses[BG_SETTINGS]['symbolConfigList'][0]['leverage'] = '7'
    request, cursors = http.request, []

    def page(method, path, params=None, **kwargs):
        if path == BG_ORDERS:
            cursors.append(params.get('cursor'))
            assert time.monotonic() < kwargs['deadline'] <= time.monotonic() + 5
            now[0] += 2.6
            return dict(list=[dict(symbol='OTHER')], cursor=str(len(cursors)))
        return request(method, path, params, **kwargs)

    with patch.object(http, 'request', side_effect=page):
        rejected(engine, 'leverage_set_skipped')
    assert cursors == [None, '1']
    assert http.writes == events(engine, 'LEVERAGE_SET_ATTEMPT') == []


@pytest.mark.parametrize('error', [APIError('unknown', http_status=429), APIError('rate_limited')])
def test_rate_limited_write_pauses_other_venue_across_restart_for_ten_minutes(engine, http, error):
    now = [10000.]
    engine.accounts.clock = lambda: now[0]
    http.write_error = error
    rejected(engine, 'leverage_set_failed')
    assert events(engine, 'LEVERAGE_SET')[0]['result'] == 'failed'
    http.write_error = None
    http.responses[BG_SETTINGS]['symbolConfigList'][0]['leverage'] = '7'
    fresh = Accounts(CrossEx(http), Bitget(http), risk_confirmed=True, clock=lambda: now[0])
    with Store(engine.stop_file.parent/'exec.db') as reopened:
        now[0] += 599
        calls_before_retry = list(http.calls)
        with pytest.raises(ValueError, match='^leverage_set_skipped$'):
            fresh.ensure_opening_leverage([('bitget', BITGET)], reopened, lambda: True)
        assert http.calls == calls_before_retry
        assert len(http.writes) == len(events(engine, 'LEVERAGE_SET_ATTEMPT')) == 1
        now[0] += 1
        fresh.ensure_opening_leverage([('bitget', BITGET)], reopened, lambda: True)
    assert [path for path, _ in http.writes] == [GATE_LEVERAGE, BG_LEVERAGE]


@pytest.mark.parametrize(('status', 'kind', 'code'), [
    (422, 'unknown', 'INVALID_PARAM_VALUE'), (200, 'unknown', 'INVALID_PARAM_VALUE'),
    (422, 'rate_limited', 'TOO_MANY_REQUESTS'),
], ids=['http_422', 'business_error', 'http_422_with_rate_limit_label'])
def test_leverage_422_or_parameter_error_only_cools_down_contract_writes(engine, http, status, kind, code):
    now = [10000.]
    engine.accounts.clock = lambda: now[0]
    http.responses[GATE_LEVERAGE][GATE] = '5'
    http.write_error = APIError(kind, http_status=status, api_code=code)
    rejected(engine, 'leverage_set_failed')
    failure, = events(engine, 'LEVERAGE_SET')
    assert failure['result'] == 'failed' and failure['target'] == {'leverage': '3'}
    assert failure['error'] == dict(stage='write', error_kind=kind, http_status=status, api_code=code)
    assert engine.store.meta('leverage_set_cooldown_until') is None
    assert engine.store.meta('leverage_set_window') == [
        dict(exchange='crossex', symbol=GATE, at=now[0], failed_at=now[0])]

    now[0] += 599
    http.write_error = None
    rejected(engine, 'leverage_set_skipped', 'still-five')
    http.responses[GATE_LEVERAGE][GATE] = '2'
    with patch.object(engine.executor, 'execute_pair', return_value=[]) as execute, patch.object(engine, 'balance'):
        assert engine.ingest(trigger('now-two'))['trigger_id'] == 'now-two'
    execute.assert_called_once()
    assert engine.accounts.leverages[GATE] == '2'
    assert http.writes == [(GATE_LEVERAGE, dict(symbol=GATE, leverage='3'))]
    assert events(engine, 'LEVERAGE_SET') == [failure]


# An empty-body HTTP 429 is a pre-existing transport edge; transport.py is
# intentionally unchanged here, so only the recognized rate-limit form is covered.
@pytest.mark.parametrize('http_status', [200])
def test_signed_post_rate_limit_preserves_transport_pause_and_persists_leverage_pause(engine, http_status):
    engine.accounts.clock = lambda: 10000.
    server = FakeVenue('gate')
    try:
        transport = Transport('gate', Credentials('test-key', 'test-secret', 'test-pass'),
                              base=server.url, local_test=True, live=True)
        engine.accounts.cross = CrossEx(transport)
        server.responses.update({GATE_LEVERAGE: {GATE: '10'}, GATE_POSITIONS: [], GATE_ORDERS: []})
        handler = server.server.RequestHandlerClass
        reply = handler.reply

        def rate_limit_post(request, status, data):
            if request.command != 'POST':
                return reply(request, status, data)
            if http_status == 200:
                return reply(request, 200, {'label': 'TOO_MANY_REQUESTS'})
            request.send_response(429)
            request.send_header('Content-Length', '0')
            request.end_headers()

        started = time.monotonic()
        with patch.object(handler, 'reply', rate_limit_post):
            rejected(engine, 'leverage_set_failed')
        assert started + 60 <= transport.paused <= time.monotonic() + 60
        with Store(engine.stop_file.parent/'exec.db') as reopened:
            assert reopened.meta('leverage_set_cooldown_until') == 10600
        failure, = events(engine, 'LEVERAGE_SET')
        assert failure['error']['http_status'] == http_status and failure['error']['error_kind'] == 'rate_limited'
    finally:
        server.close()


# Opening review now tries a crossed write for a missing row. The missing-row
# readback and default strict-read tests above cover the two rejection paths.
@pytest.mark.parametrize('config', ['isolated', 'duplicate', 'wrong_category'])
def test_bitget_unconfirmed_cross_mode_prevents_any_write(engine, http, config):
    rows = http.responses[BG_SETTINGS]['symbolConfigList']
    if config == 'isolated':
        rows[0]['marginMode'] = 'isolated'
    elif config == 'duplicate':
        rows.append(dict(rows[0]))
    else:
        rows[0]['category'] = 'COIN-FUTURES'
    rejected(engine, 'leverage_set_skipped')
    assert http.writes == events(engine, 'LEVERAGE_SET') == []


def test_already_at_target_issues_no_write_or_exposure_reads(engine, http):
    engine.accounts.leverages[GATE] = '3'
    reviewed(engine)
    assert http.writes == [] and [call[1] for call in http.calls] == [BG_SETTINGS]


def test_stale_gate_cache_already_correct_on_venue(engine, http):
    http.responses[GATE_LEVERAGE][GATE] = '3'
    reviewed(engine)
    assert http.writes == [] and engine.accounts.leverages[GATE] == '3'


def test_unconfigured_gate_contract_requires_exact_readback(engine, http):
    engine.accounts.leverages.clear()
    http.responses[GATE_LEVERAGE].clear()
    reviewed(engine)
    assert len(http.writes) == 1
    assert events(engine, 'LEVERAGE_SET')[0]['before'] == {'leverage': None}
    assert engine.accounts.leverages[GATE] == '3'


def test_both_legs_repaired_before_one_review(engine, http):
    http.responses[BG_SETTINGS]['symbolConfigList'][0]['leverage'] = '7'
    reviewed(engine)
    assert [path for path, _ in http.writes] == [GATE_LEVERAGE, BG_LEVERAGE]
    assert [r['exchange'] for r in events(engine, 'LEVERAGE_SET')] == ['crossex', 'bitget']


def test_bitget_readback_requires_both_leverage_and_mode(engine, http):
    engine.accounts.leverages[GATE] = '3'
    row = http.responses[BG_SETTINGS]['symbolConfigList'][0]
    row['leverage'] = '7'
    http.after_admit = lambda: row.update(marginMode='isolated')
    rejected(engine, 'leverage_set_failed')
    assert row['leverage'] == '3' and BITGET not in engine.accounts.bitget_symbol_configs
    assert len(http.writes) == 1


@pytest.mark.parametrize('gate', ['stop', 'stop_after_queue', 'stop_lock', 'shadow', 'rate_limit'])
def test_no_write_when_opening_or_transport_blocks(engine, http, gate):
    expected = 'leverage_set_skipped'
    if gate == 'stop':
        engine.stop_file.touch()
        expected = 'filter_or_open_gate'
    elif gate == 'stop_after_queue':
        http.after_admit = engine.stop_file.touch
    elif gate == 'stop_lock':
        http.after_admit = lambda: engine.stops.add('manual_hold')
    elif gate == 'shadow':
        engine.live = False
    else:
        def rate_limited():
            raise APIError('rate_limited')
        http.after_admit = rate_limited
    rejected(engine, expected, expected_locks=('manual_hold',) if gate == 'stop_lock' else ())
    assert http.writes == events(engine, 'LEVERAGE_SET') == []


def test_stop_during_budget_reservation_releases_unsent_slot(engine, http):
    set_meta = engine.store.set_meta

    def pause_on_reserve(key, value):
        set_meta(key, value)
        if key == 'leverage_set_window' and value:
            engine.stop_file.touch()

    with patch.object(engine.store, 'set_meta', side_effect=pause_on_reserve):
        rejected(engine, 'leverage_set_skipped')
    assert http.writes == events(engine, 'LEVERAGE_SET') == []
    assert engine.store.meta('leverage_set_window') == []


def test_cooldown_survives_restart_and_expires_only_after_ten_minutes(engine, http):
    now = [10000.]
    engine.accounts.clock = lambda: now[0]
    http.apply_write = False
    rejected(engine, 'leverage_set_failed', 'failed')
    now[0] += 599
    other = Accounts(CrossEx(http), Bitget(http), risk_confirmed=True, clock=lambda: now[0])
    with Store(engine.stop_file.parent/'exec.db') as reopened:
        with pytest.raises(ValueError, match='leverage_set_skipped'):
            other.ensure_opening_leverage([('crossex', GATE)], reopened, lambda: True)
        assert len(http.writes) == 1
        now[0] += 2
        http.apply_write = True
        other.ensure_opening_leverage([('crossex', GATE)], reopened, lambda: True)
    assert len(http.writes) == 2 and other.leverages[GATE] == '3'


def test_hourly_limit_is_rolling_and_shared_across_venues(engine, http):
    now = [10000.]
    engine.accounts.clock = lambda: now[0]
    for n in range(30):
        symbol = f'GATE_FUTURE_C{n}_USDT'
        http.responses[GATE_LEVERAGE][symbol] = '10'
        engine.accounts.ensure_opening_leverage([('crossex', symbol)], engine.store, lambda: True)
    http.responses[BG_SETTINGS]['symbolConfigList'][0]['leverage'] = '10'
    now[0] += 3599
    with pytest.raises(ValueError, match='leverage_set_skipped'):
        engine.accounts.ensure_opening_leverage([('bitget', BITGET)], engine.store, lambda: True)
    assert len(http.writes) == len(events(engine, 'LEVERAGE_SET')) == 30
    now[0] += 1
    engine.accounts.ensure_opening_leverage([('bitget', BITGET)], engine.store, lambda: True)
    assert len(http.writes) == 31 and http.writes[-1][0] == BG_LEVERAGE


def test_signed_transport_checks_flatness_after_write_admission(engine):
    server = FakeVenue('gate')
    try:
        transport = Transport('gate', Credentials('test-key', 'test-secret', 'test-pass'),
                              base=server.url, local_test=True, live=True)
        engine.accounts.cross = CrossEx(transport)
        server.responses.update({GATE_LEVERAGE: {GATE: '10'}, GATE_POSITIONS: [], GATE_ORDERS: []})
        admit = transport.admit

        def add_order_while_queued(group, deadline):
            if group == 'write':
                server.responses[GATE_ORDERS] = [dict(symbol=GATE)]
            return admit(group, deadline)

        with patch.object(transport, 'admit', side_effect=add_order_while_queued):
            rejected(engine, 'leverage_set_skipped')
        assert [(method, path) for method, path, _, _ in server.calls] == [
            ('GET', GATE_LEVERAGE), ('GET', GATE_ORDERS)]
        assert events(engine, 'LEVERAGE_SET') == []
    finally:
        server.close()


@pytest.mark.parametrize('transport_timeout', [2, 10])
def test_signed_bitget_write_does_not_log_credentials_or_signature(engine, transport_timeout):
    server = FakeVenue('bitget')
    try:
        credentials = Credentials('test-key', 'test-secret', 'test-pass')
        transport = Transport('bitget', credentials, base=server.url, local_test=True, live=True,
                              timeout=transport_timeout)
        engine.accounts.bitget = Bitget(transport)
        engine.accounts.leverages[GATE] = '3'
        data = settings()
        data['symbolConfigList'][0]['leverage'] = '7'
        server.responses.update({BG_SETTINGS: data, BG_LEVERAGE: 'success', BG_POSITIONS: {'list': []},
                                 BG_ORDERS: {'list': [], 'cursor': None}, BG_STRATEGIES: []})
        with (patch.object(transport, 'request', wraps=transport.request) as requests,
              patch('tools.crossex_exec.transport.DeadlineSocket', wraps=DeadlineSocket) as sockets):
            rejected(engine, 'leverage_set_failed')  # Server ACKs, settings still report 7.
        # The initial check_instruments read precedes ensure. Every subsequent
        # HTTP call is bounded, including the wire read after the signed POST.
        assert all(call.kwargs['deadline'] is not None for call in requests.call_args_list[1:])
        # transport.py is intentionally unchanged (order sends must keep their
        # behavior), so the signed POST keeps its own per-request wire timeout
        # after before_send instead of being capped by the leverage budget.
        (_, path, body, headers), = [call for call in server.calls if call[0] == 'POST']
        assert path == BG_LEVERAGE and body == dict(symbol=BITGET, category='USDT-FUTURES',
                                                    marginMode='crossed', leverage='3')
        logged = json.dumps(events(engine, 'LEVERAGE_SET'))
        assert 'readback_mismatch' in logged
        assert all(value not in logged for value in ('test-key', 'test-secret', 'test-pass', headers['ACCESS-SIGN']))
        attempt_log = json.dumps(events(engine, 'LEVERAGE_SET_ATTEMPT'))
        assert all(value not in attempt_log for value in ('test-key', 'test-secret', 'test-pass', headers['ACCESS-SIGN']))
    finally:
        server.close()
