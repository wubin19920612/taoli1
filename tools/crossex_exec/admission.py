"""Additional opening-only ceilings. No orders, I/O, or relaxed fallbacks."""
from statistics import median
from copy import copy

from tools.fill_sim.core import dec
from .baseline import BATCH_URLS, FRESH_SECONDS


MAX_BOOK_SKEW_MS = dec('300')
MAX_BOOK_AGE_SECONDS = dec('1')
MAX_FUNDING_DIFF_BPS_H = dec('0.5')
MAX_FUNDING_RATE_8H = dec('0.0005')
MAX_BASELINE_BPS = dec('10')
MAX_BASELINE_MAD_BPS = dec('10')
MAX_NORMAL_DEVIATION_BPS = dec('10')
MIN_OUTLIER_SHARE = dec('0.7')
MIN_VENUES = 3
BOOK_RETRIES = 1
SKIP_REASONS = ('book_skew', 'book_stale', 'funding_diff', 'baseline_not_flat',
                'not_single_venue_outlier', 'too_few_venues', 'size_too_small', 'oi_missing',
                'mapping_ratio_mismatch', 'volume_too_low', 'volume_unknown', 'category_unconfirmed')


class AdmissionError(ValueError):
    def __init__(self, reason, *, send=False):
        self.reason = reason
        # Retain the old diagnostic substring for existing send-age regressions.
        super().__init__('send_book_stale' if send and reason == 'book_stale' else reason)


def book_dict(book):
    return book if isinstance(book, dict) else book.to_dict()


def book_time(book):
    row = book_dict(book)
    return dec(row['received_at'] if row.get('exchange_at') is None else row['exchange_at'])


def book_skew_ms(books):
    """Observation only: bad/missing timestamps never block a reduction."""
    try:
        if len(books) == 2:
            return abs(book_time(books[0])-book_time(books[1]))*1000
    except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError):
        pass
    return None


class OpeningLimits:
    def restricted(self, values):
        result = copy(self)
        for name in ('book_skew_ms','book_age_seconds','funding_diff_bps_h','funding_rate_8h',
                     'baseline_bps','baseline_mad_bps','normal_deviation_bps','reference_age_seconds'):
            setattr(result, name, min(getattr(self, name), dec(values['max_'+name])))
        result.outlier_share = max(self.outlier_share, dec(values['min_outlier_share']))
        result.min_venues = max(self.min_venues, dec(values['min_venues']))
        return result

    def __init__(self, config=None):
        config = config or {}
        ceilings = dict(book_skew_ms=MAX_BOOK_SKEW_MS, book_age_seconds=MAX_BOOK_AGE_SECONDS,
                        funding_diff_bps_h=MAX_FUNDING_DIFF_BPS_H, funding_rate_8h=MAX_FUNDING_RATE_8H,
                        baseline_bps=MAX_BASELINE_BPS, baseline_mad_bps=MAX_BASELINE_MAD_BPS,
                        normal_deviation_bps=MAX_NORMAL_DEVIATION_BPS, reference_age_seconds=dec(FRESH_SECONDS))
        for name, ceiling in ceilings.items():
            value = dec(config.get('CROSSEX_MAX_'+name.upper(), ceiling))
            if not 0 <= value <= ceiling:
                raise ValueError('opening_limit_config_invalid')
            setattr(self, name, value)
        self.outlier_share = dec(config.get('CROSSEX_MIN_OUTLIER_SHARE', MIN_OUTLIER_SHARE))
        self.min_venues = dec(config.get('CROSSEX_MIN_VENUES', MIN_VENUES))
        if (not MIN_OUTLIER_SHARE <= self.outlier_share <= 1
                or not MIN_VENUES <= self.min_venues <= len(BATCH_URLS)
                or self.min_venues != self.min_venues.to_integral_value()):
            raise ValueError('opening_limit_config_invalid')

    def books(self, books, now, *, send=False):
        try:
            if len(books) != 2:
                raise ValueError('missing_book')
            now = dec(now)
            for book in books:
                row = book_dict(book)
                # A recent server clock cannot make an old local receipt fresh.
                if (not 0 <= now-dec(row['received_at']) <= self.book_age_seconds
                        or not 0 <= now-book_time(row) <= self.book_age_seconds):
                    raise ValueError('invalid_book_time')
        except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError):
            raise AdmissionError('book_stale', send=send) from None
        skew = book_skew_ms(books)
        if skew is None:
            raise AdmissionError('book_stale', send=send)
        if skew > self.book_skew_ms:
            raise AdmissionError('book_skew', send=send)
        return skew

    def funding(self, rows):
        try:
            if len(rows) != 2:
                raise ValueError('missing_funding')
            hourly = []
            for row in rows:
                interval = dec(row['interval'])
                if interval <= 0:
                    raise ValueError('invalid_interval')
                rate = dec(row['rate'])*3600/interval
                if abs(rate)*8 > self.funding_rate_8h:
                    raise ValueError('absolute_funding')
                hourly.append(rate)
            if abs(hourly[0]-hourly[1])*10000 > self.funding_diff_bps_h:
                raise ValueError('funding_difference')
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise AdmissionError('funding_diff') from None

    def flat(self, state):
        try:
            if (abs(dec(state['baseline_bps'])) > self.baseline_bps
                    or not 0 <= dec(state['mad_bps']) <= self.baseline_mad_bps):
                raise ValueError('not_flat')
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise AdmissionError('baseline_not_flat') from None

    def outlier(self, venues, books, snapshot, excess_bps, now):
        try:
            if not 0 <= dec(now)-dec(snapshot['at']) <= self.reference_age_seconds:
                raise ValueError('old_reference')
            mids = {}
            for ex, value in snapshot['mids'].items():
                try:
                    value = dec(value)
                    if ex in BATCH_URLS and value > 0:
                        mids[ex] = value
                except (TypeError, ValueError, ArithmeticError):
                    continue
            if (len(venues) != 2 or len(set(venues)) != 2 or not all(v in mids for v in venues)
                    or len(mids) < self.min_venues or not set(mids)-set(venues)):
                raise ValueError('missing_reference')
        except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError):
            raise AdmissionError('too_few_venues') from None
        try:
            center = median(mids.values())
            current = []
            for book in books:
                row = book_dict(book)
                bid, ask = dec(row['bids'][0][0]), dec(row['asks'][0][0])
                if not 0 < bid <= ask:
                    raise ValueError('invalid_mid')
                current.append(((bid+ask)/2-center)/center*10000)
            excess = dec(excess_bps)
            if len(current) != 2 or excess <= 0:
                raise ValueError('invalid_excess')
            # All independent references must agree with the median. A second
            # anomalous reference cannot be hidden by a majority of normal rows.
            peers = [abs(value-center)/center*10000 for ex,value in mids.items() if ex not in venues]
            if any(v > self.normal_deviation_bps for v in peers):
                raise ValueError('multiple_outliers')
            candidates = [n for n in (0,1)
                          if current[n]*(-1 if n == 0 else 1) >= excess*self.outlier_share
                          and abs(current[n]) > self.normal_deviation_bps
                          and abs(current[1-n]) <= self.normal_deviation_bps]
            if len(candidates) != 1:
                raise ValueError('not_directional_single_outlier')
            return dict(outlier_exchange=venues[candidates[0]], reference_median=str(center),
                        reference_at=snapshot['at'], reference_venues=sorted(mids),
                        deviations_bps=list(map(str,current)))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, ArithmeticError):
            raise AdmissionError('not_single_venue_outlier') from None
