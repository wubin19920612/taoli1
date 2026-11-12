"""Exact market eligibility and explicitly confirmed denomination mappings."""
from dataclasses import replace
import hashlib
from itertools import combinations
import re
import time

from tools.fill_sim.core import dec
from .policy import Instrument


FORBIDDEN = ('pre_ipo', 'commodity')
PRE_IPO_BASES = frozenset(('ANTHROPIC', 'OPENAI'))
COMMODITY_BASES = frozenset(('PAXG', 'XAUT', 'XAU', 'XAG'))
PREFIXES = (('1000000', 1000000), ('100000', 100000), ('10000', 10000),
            ('1000', 1000), ('1M', 1000000), ('K', 1000))


def category(ex, row):
    """Unknown metadata stays unconfirmed; an empty field is not crypto proof."""
    tokens = {str(row.get(k, '')).upper() for k in ('underlyingType', 'symbolType', 'contract_type')}
    subs = row.get('underlyingSubType')
    if isinstance(subs, list):
        tokens.update(str(v).upper() for v in subs)
    base=str(row.get('baseAsset',row.get('baseCoin',row.get('baseCcy','')))).upper()
    if base in PRE_IPO_BASES or row.get('isPreListing') is True or row.get('is_pre_market') is True or tokens & {'PREMARKET', 'PRE_MARKET', 'PRE_IPO'}:
        return 'pre_ipo'
    if base in COMMODITY_BASES or tokens & {'COMMODITY', 'COMMODITIES', 'METALS', 'METAL', 'GOLD', 'FOREX', 'INDICES', 'BONDS'}:
        return 'commodity'
    if tokens & {'STOCK', 'STOCKS', 'ETF'}:
        return 'listed_equity'
    if ex == 'okx':
        return {'1': 'crypto', '3': 'listed_equity', '4': 'commodity', '5': 'commodity', '6': 'commodity'}.get(row.get('instCategory'), 'unconfirmed')
    if ex == 'bitget':
        return 'crypto' if row.get('isRwa') == 'NO' else 'unconfirmed'
    if ex == 'binance' and row.get('underlyingType') == 'COIN' and isinstance(subs, list):
        # Unknown subtypes could be new RWA/TradFi categories; never guess.
        if set(subs) <= {'DEFI', 'HOT', 'BSC', 'NFT', 'AI', 'MEME', 'LAYER1', 'LAYER2', 'PAYMENTS'}:
            return 'crypto'
    return 'unconfirmed'


def alias(base):
    if base in ('KO', 'KAT'):
        return base, 1
    for prefix, multiplier in PREFIXES:
        if base.startswith(prefix) and len(base)-len(prefix) >= 3:
            return base[len(prefix):], multiplier
    return base, 1


def check_mapping_ratio(instruments, books):
    if not any(i.mapping_id for i in instruments):
        return
    try:
        mids = [(dec(b.bids[0][0])+dec(b.asks[0][0]))/2/i.multiplier for i, b in zip(instruments, books)]
        if len(mids) != 2 or min(mids) <= 0 or max(mids)/min(mids)-1 > dec('.01'):
            raise ValueError('bad_ratio')
    except (ValueError, IndexError, TypeError, AttributeError):
        raise ValueError('mapping_ratio_mismatch') from None


class Universe:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    def catalog(self):
        return {k: Instrument.load(v) for k, v in (self.store.meta('mapping', {}) or {}).get('instruments', {}).items()}

    def _decisions(self):
        return self.store.meta('universe_decisions', {})

    def classify(self, base, value):
        catalog = self.catalog()
        if not isinstance(value, dict) or set(value)-{'category', 'underlying_market', 'markets'}:
            raise ValueError('invalid_classification')
        selected = value.get('markets')
        kind = value.get('category')
        if (kind not in ('crypto', 'listed_equity', *FORBIDDEN) or not isinstance(selected, list) or not selected
                or any(not isinstance(k, str) for k in selected) or len(set(selected)) != len(selected)
                or (alias(base)[0] in PRE_IPO_BASES|COMMODITY_BASES and kind not in FORBIDDEN)
                or any(k not in catalog or catalog[k].spec.base != base
                       or (catalog[k].category in FORBIDDEN and kind not in FORBIDDEN) for k in selected)):
            raise ValueError('invalid_classification')
        underlying = value.get('underlying_market')
        if kind == 'listed_equity' and underlying not in ('US', 'KRX'):
            raise ValueError('underlying_market_required')
        if kind != 'listed_equity' and underlying is not None:
            raise ValueError('invalid_classification')
        if kind == 'crypto' and any(catalog[k].category == 'listed_equity' for k in selected):
            raise ValueError('invalid_classification')
        with self.store.transaction():
            decisions = self._decisions()
            old = decisions.get(base, {})
            decisions[base] = dict(old, category=kind, underlying_market=underlying, markets=selected)
            self.store.set_meta('universe_decisions', decisions)
            self.store.event('UNIVERSE_CHANGED', dict(base=base, old=old, new=decisions[base], at=self.clock(), source='admin_api'))
        return decisions[base]

    def exclude(self, base, excluded):
        if type(excluded) is not bool or not isinstance(base, str) or not re.fullmatch('[A-Z0-9]{1,40}', base):
            raise ValueError('invalid_base')
        with self.store.transaction():
            decisions = self._decisions()
            old = decisions.get(base, {})
            decisions[base] = dict(old, excluded=excluded)
            self.store.set_meta('universe_decisions', decisions)
            self.store.event('UNIVERSE_CHANGED', dict(base=base, old=old, new=decisions[base], at=self.clock(), source='admin_api'))
        return decisions[base]

    def eligibility(self, i):
        choice = self._decisions().get(i.spec.base, {})
        if choice.get('excluded') or self._decisions().get(i.base, {}).get('excluded'):
            raise ValueError('excluded')
        if i.category in FORBIDDEN or choice.get('category') in FORBIDDEN or alias(i.spec.base)[0] in PRE_IPO_BASES|COMMODITY_BASES:
            raise ValueError('category_unconfirmed')
        if i.market.key in choice.get('markets', []):
            return choice['category'], choice.get('underlying_market')
        if choice.get('category') == 'listed_equity':
            raise ValueError('category_unconfirmed')
        if i.category == 'crypto':
            return 'crypto', None
        raise ValueError('category_unconfirmed')

    def discover(self):
        with self.store.lock:
            self._discover()

    def _discover(self):
        catalog = self.catalog()
        quotes = self.store.meta('market_tickers', {})
        old = self.store.meta('symbol_mappings', {})
        groups, current = {}, {}
        for i in catalog.values():
            if i.category not in FORBIDDEN and alias(i.spec.base)[0] not in PRE_IPO_BASES|COMMODITY_BASES:
                base, factor = alias(i.spec.base)
                groups.setdefault(base, []).append((i, factor))
        for base, members in groups.items():
            for (a, fa), (b, fb) in combinations(members, 2):
                if a.market.exchange == b.market.exchange or a.spec.base == b.spec.base or fa == fb:
                    continue
                try:
                    qa, qb = quotes[a.market.key], quotes[b.market.key]
                    if any(not 0 <= self.clock()-q['at'] <= 300 for q in (qa, qb)):
                        continue
                    ma, mb = dec(qa['mid'])/fa, dec(qb['mid'])/fb
                    if min(ma, mb) <= 0 or max(ma, mb)/min(ma, mb)-1 > dec('.01'):
                        continue
                except (KeyError, ValueError, TypeError):
                    continue
                legs = sorted([dict(market_key=i.market.key, raw_base=i.spec.base, quantity_multiplier=str(f))
                               for i, f in ((a, fa), (b, fb))], key=lambda r: r['market_key'])
                identity = hashlib.sha256('|'.join(r['market_key']+':'+r['quantity_multiplier'] for r in legs).encode()).hexdigest()[:24]
                current[identity] = dict(id=identity, base=base, legs=legs, status=old.get(identity, {}).get('status', 'pending'),
                    at=self.clock(),ratio=str(dec(quotes[legs[0]['market_key']]['mid'])/dec(quotes[legs[1]['market_key']]['mid'])))
        # Keep decisions for absent rows without exposing stale candidates.
        for identity, row in old.items():
            if identity not in current:
                current[identity] = dict(row, available=False)
        for identity in set(current)-set(old):
            self.store.event('MAPPING_PENDING', dict(id=identity))
        self.store.set_meta('symbol_mappings', current)

    def mappings(self):
        catalog = self.catalog()
        decisions=self._decisions()
        return [r for r in self.store.meta('symbol_mappings', {}).values()
                if r.get('available', True) and r['base'] not in PRE_IPO_BASES
                and all(k['market_key'] in catalog and catalog[k['market_key']].category not in FORBIDDEN
                        and decisions.get(catalog[k['market_key']].spec.base,{}).get('category') not in FORBIDDEN for k in r['legs'])]

    def decide_mapping(self, identity, status):
        if status not in ('confirmed', 'rejected'):
            raise ValueError('invalid_mapping_status')
        with self.store.transaction():
            mappings = self.store.meta('symbol_mappings', {})
            row = mappings.get(identity)
            if not row or not row.get('available', True) or not 0 <= self.clock()-row['at'] <= 300:
                raise ValueError('mapping_unavailable')
            row = dict(row, status=status)
            mappings[identity] = row
            self.store.set_meta('symbol_mappings', mappings)
            self.store.event('MAPPING_CHANGED', dict(id=identity, status=status, at=self.clock(), source='admin_api'))
        return row

    def resolve(self, instruments):
        instruments = list(instruments)
        if len(instruments) != 2 or instruments[0].market.exchange == instruments[1].market.exchange:
            raise ValueError('identity_mismatch')
        if instruments[0].spec.base != instruments[1].spec.base:
            keys = {i.market.key for i in instruments}
            matches = [r for r in self.mappings() if {leg['market_key'] for leg in r['legs']} == keys and r['status'] == 'confirmed']
            if len(matches) != 1:
                raise ValueError('mapping_unconfirmed')
            row = matches[0]
            factors = {leg['market_key']: leg['quantity_multiplier'] for leg in row['legs']}
            instruments = [replace(i, canonical_base=row['base'], quantity_multiplier=factors[i.market.key], mapping_id=row['id']) for i in instruments]
        kinds = [self.eligibility(i) for i in instruments]
        if kinds[0] != kinds[1]:
            raise ValueError('category_unconfirmed')
        return instruments

    def volume_venues(self, instruments, values):
        """Count only compatible, confirmed identities in the selected market."""
        quotes=self.store.meta('market_tickers',{})
        venues=set()
        for candidate in self.compatible_markets(instruments):
            try:
                row=quotes[candidate.market.key]
                if 0<=self.clock()-row['at']<=300 and dec(row['volume'])>=dec(values['min_quote_volume_usd']):
                    venues.add(candidate.market.exchange)
            except (ValueError,KeyError,TypeError):
                continue
        return venues

    def compatible_markets(self, instruments):
        anchor=instruments[0]
        result=[]
        for candidate in self.catalog().values():
            try:
                if candidate.market.key==anchor.market.key:
                    self.eligibility(candidate)
                else:
                    resolved=self.resolve((anchor,candidate))
                    if resolved[0].base!=anchor.base or resolved[1].base!=anchor.base:
                        continue
                result.append(candidate)
            except (ValueError,KeyError,TypeError):
                continue
        return result

    def describe(self):
        quotes, decisions = self.store.meta('market_tickers', {}), self._decisions()
        result = {}
        for i in self.catalog().values():
            if (i.category=='pre_ipo' or decisions.get(i.spec.base,{}).get('category')=='pre_ipo'
                    or alias(i.spec.base)[0] in PRE_IPO_BASES):
                continue
            decision = decisions.get(i.spec.base, {})
            row = result.setdefault(i.spec.base, dict(base=i.spec.base, excluded=bool(decision.get('excluded')),
                category=decision.get('category', i.category), underlying_market=decision.get('underlying_market'), markets=[]))
            quote = quotes.get(i.market.key, {})
            try:
                effective,market = self.eligibility(i)
                eligible = True
            except ValueError:
                effective,market,eligible = decision.get('category',i.category),decision.get('underlying_market'),False
            row['markets'].append(dict(market_key=i.market.key, raw_symbol=i.market.raw_symbol, exchange=i.market.exchange,
                category=i.category, volume_usd=quote.get('volume') if 0 <= self.clock()-quote.get('at', 0) <= 300 else None,
                volume_basis=quote.get('volume_basis'),at=quote.get('at'),selected=i.market.key in decision.get('markets', []),
                effective_category=effective,underlying_market=market,eligible=eligible,
                needs_classification=not eligible and effective not in FORBIDDEN and not decision.get('excluded',False)))
        return list(result.values())
