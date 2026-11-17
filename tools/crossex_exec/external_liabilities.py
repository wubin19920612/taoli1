"""Explicit bounded allowances for exact CrossEx native-coin liabilities."""
import re

from tools.fill_sim.core import dec


EXTERNAL_LIABILITY_CAP_MAX = dec('1000')
ALLOWANCE_KEY = 'external_liability_allowances'
RECORD_FIELDS = {'exchange_type', 'coin', 'cap', 'reason', 'registered_at'}


def checked_group(exchange_type, coin):
    # Keep the exchange spelling used by external.symbol_identity and the
    # native currency format used by external_fx; never normalize an identity.
    if (not isinstance(exchange_type, str) or len(exchange_type) > 160
            or not re.fullmatch(r'[A-Z][A-Z0-9]*', exchange_type)
            or not isinstance(coin, str) or not re.fullmatch(r'[A-Z0-9]{2,20}', coin)):
        raise ValueError('invalid_external_liability_identity')
    return exchange_type, coin


def checked_cap(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9.eE+\-]{1,80}', value):
        raise ValueError('invalid_external_liability_cap')
    amount = dec(value)
    if not 0 < amount <= EXTERNAL_LIABILITY_CAP_MAX:
        raise ValueError('invalid_external_liability_cap')
    return amount


def checked_reason(reason):
    if (not isinstance(reason, str) or not reason.strip() or len(reason) > 200
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in reason)):
        raise ValueError('invalid_external_liability_reason')
    return reason


def matching_liability(snapshot, group):
    assets = snapshot['raw_accounts']['crossex']['assets']
    if not isinstance(assets, list):
        raise ValueError('account_liability_unknown')
    matched = [a for a in assets if (a['exchange_type'], a['coin']) == group]
    if len(matched) != 1:
        raise ValueError('account_liability_unknown')
    amount = dec(matched[0]['liability'])
    if amount < 0:
        raise ValueError('account_liability_unknown')
    return amount


class ExternalLiabilityAllowances:
    def __init__(self, store):
        self.store = store

    def records(self):
        value = self.store.meta(ALLOWANCE_KEY, dict(revision=0, items=[]))
        if (not isinstance(value, dict) or set(value) != {'revision', 'items'}
                or type(value['revision']) is not int or value['revision'] < 0
                or not isinstance(value['items'], list)):
            raise ValueError('invalid_external_liability_allowances')
        groups = set()
        for row in value['items']:
            if not isinstance(row, dict) or set(row) != RECORD_FIELDS:
                raise ValueError('invalid_external_liability_allowances')
            group = checked_group(row['exchange_type'], row['coin'])
            checked_cap(row['cap'])
            checked_reason(row['reason'])
            if (group in groups or type(row['registered_at']) not in (int, float)
                    or dec(row['registered_at']) <= 0):
                raise ValueError('invalid_external_liability_allowances')
            groups.add(group)
        return value

    def caps(self):
        try:
            return {(r['exchange_type'], r['coin']): checked_cap(r['cap'])
                    for r in self.records()['items']}
        except Exception:
            # An unreadable or partially corrupt opt-in never grants any cap.
            return {}

    def describe(self):
        value = self.records()
        try:
            snapshot = (None if self.store.meta('account_snapshot_error')
                        else self.store.meta('account_snapshot'))
        except Exception:
            snapshot = None
        items = []
        for row in value['items']:
            liability = within_cap = None
            try:
                amount = matching_liability(snapshot, (row['exchange_type'], row['coin']))
                liability, within_cap = str(amount), amount <= checked_cap(row['cap'])
            except (KeyError, TypeError, ValueError, ArithmeticError):
                pass
            items.append(dict(row, liability=liability, within_cap=within_cap))
        return dict(revision=value['revision'], items=items)
