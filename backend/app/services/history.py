from datetime import UTC, datetime, timedelta

from app.db.repositories import OpportunityHistoryRepository
from app.models.opportunity import Opportunity
from app.models.settings import HistorySettings
from app.services.risk_labels import known_volume_24h_usdt


class OpportunityHistoryRecorder:
    def __init__(
        self,
        repository: OpportunityHistoryRepository,
        settings: HistorySettings,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self._last_sample_at: datetime | None = None
        self._last_vacuum_at: datetime | None = None

    async def record(
        self,
        opportunities: list[Opportunity],
        now: datetime | None = None,
        force: bool = False,
    ) -> int:
        if not self.settings.enabled:
            return 0
        observed_at = now or datetime.now(UTC)
        if not force and self._last_sample_at is not None:
            elapsed = (observed_at - self._last_sample_at).total_seconds()
            if elapsed < self.settings.sample_seconds:
                return 0

        selected = self._select(opportunities)
        rows = [
            self.repository.row_from_opportunity(item, observed_at)
            for item in selected
        ]
        inserted = await self.repository.insert_many(rows)
        self._last_sample_at = observed_at
        await self._prune(observed_at)
        return inserted

    def _select(self, opportunities: list[Opportunity]) -> list[Opportunity]:
        min_volume = self.settings.min_volume_24h_usdt

        def eligible(item: Opportunity) -> bool:
            if item.open_spread_pct < self.settings.min_open_spread_pct:
                return False
            known_volume = known_volume_24h_usdt(item)
            if min_volume > 0 and (known_volume is None or known_volume < min_volume):
                return False
            return True

        return sorted(
            [item for item in opportunities if eligible(item)],
            key=lambda item: item.open_spread_pct,
            reverse=True,
        )[: self.settings.keep_top_n]

    async def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=self.settings.retention_days)
        deleted = await self.repository.prune_before(cutoff)
        if deleted <= 0:
            return
        if self._last_vacuum_at is not None:
            elapsed = (now - self._last_vacuum_at).total_seconds()
            if elapsed < self.settings.vacuum_interval_seconds:
                return
        await self.repository.vacuum()
        self._last_vacuum_at = now
