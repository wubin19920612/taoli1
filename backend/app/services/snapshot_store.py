from threading import RLock

from app.models.market import MarketSnapshot
from app.models.opportunity import Opportunity


class SnapshotStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._markets: list[MarketSnapshot] = []
        self._opportunities: list[Opportunity] = []
        self._exchange_errors: dict[str, str] = {}

    def set_markets(self, markets: list[MarketSnapshot]) -> None:
        with self._lock:
            self._markets = markets

    def get_markets(self) -> list[MarketSnapshot]:
        with self._lock:
            return list(self._markets)

    def set_opportunities(self, opportunities: list[Opportunity]) -> None:
        with self._lock:
            self._opportunities = opportunities

    def get_opportunities(self) -> list[Opportunity]:
        with self._lock:
            return list(self._opportunities)

    def set_exchange_errors(self, errors: dict[str, str]) -> None:
        with self._lock:
            self._exchange_errors = dict(errors)

    def get_exchange_errors(self) -> dict[str, str]:
        with self._lock:
            return dict(self._exchange_errors)
