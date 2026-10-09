from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from statistics import median

from .models import utc_iso
from .route_engine import (
    LegSnapshot,
    Route,
    RouteEvaluation,
    evaluate_route,
)

D = Decimal


@dataclass
class RouteTracker:
    route_id: str
    phase: str = "watching"
    baseline: Decimal | None = None
    baseline_at: datetime | None = None
    baseline_samples: list[tuple[datetime, Decimal]] = field(default_factory=list)
    first_dislocation_at: datetime | None = None
    peak_at: datetime | None = None
    peak_difference: Decimal | None = None
    reference_price: Decimal | None = None
    frozen_quantities: tuple[Decimal, ...] | None = None
    confirmation_started_at: datetime | None = None
    confirmation_count: int = 0
    previous_expensive_sequence: int | None = None
    previous_cheap_sequence: int | None = None
    previous_sample_at: datetime | None = None
    cooldown_until: datetime | None = None
    event_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "route_id": self.route_id, "phase": self.phase,
            "baseline": str(self.baseline) if self.baseline is not None else None,
            "baseline_at": utc_iso(self.baseline_at) if self.baseline_at else None,
            "baseline_samples": [
                [utc_iso(at), str(value)] for at, value in self.baseline_samples
            ],
            "first_dislocation_at": utc_iso(self.first_dislocation_at) if self.first_dislocation_at else None,
            "peak_at": utc_iso(self.peak_at) if self.peak_at else None,
            "peak_difference": str(self.peak_difference) if self.peak_difference is not None else None,
            "reference_price": str(self.reference_price) if self.reference_price is not None else None,
            "frozen_quantities": [str(q) for q in self.frozen_quantities] if self.frozen_quantities else None,
            "confirmation_started_at": utc_iso(self.confirmation_started_at) if self.confirmation_started_at else None,
            "confirmation_count": self.confirmation_count,
            "previous_expensive_sequence": self.previous_expensive_sequence,
            "previous_cheap_sequence": self.previous_cheap_sequence,
            "previous_sample_at": utc_iso(self.previous_sample_at) if self.previous_sample_at else None,
            "cooldown_until": utc_iso(self.cooldown_until) if self.cooldown_until else None,
            "event_id": self.event_id,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> RouteTracker:
        def at(key: str) -> datetime | None:
            return datetime.fromisoformat(payload[key]) if payload.get(key) else None

        def dec(key: str) -> Decimal | None:
            return D(payload[key]) if payload.get(key) is not None else None

        return cls(
            route_id=payload["route_id"], phase=payload.get("phase", "watching"),
            baseline=dec("baseline"), baseline_at=at("baseline_at"),
            baseline_samples=[
                (datetime.fromisoformat(row[0]), D(row[1]))
                for row in payload.get("baseline_samples", [])
            ],
            first_dislocation_at=at("first_dislocation_at"),
            peak_at=at("peak_at"), peak_difference=dec("peak_difference"),
            reference_price=dec("reference_price"),
            frozen_quantities=tuple(D(q) for q in payload["frozen_quantities"])
            if payload.get("frozen_quantities") else None,
            confirmation_started_at=at("confirmation_started_at"),
            confirmation_count=int(payload.get("confirmation_count", 0)),
            previous_expensive_sequence=payload.get("previous_expensive_sequence"),
            previous_cheap_sequence=payload.get("previous_cheap_sequence"),
            previous_sample_at=at("previous_sample_at"),
            cooldown_until=at("cooldown_until"), event_id=payload.get("event_id"),
        )

    def _clear_event(self) -> None:
        self.phase = "watching"
        self.first_dislocation_at = None
        self.peak_at = None
        self.peak_difference = None
        self.reference_price = None
        self.frozen_quantities = None
        self.confirmation_started_at = None
        self.confirmation_count = 0
        self.event_id = None

    def mark_source_gap(self) -> None:
        self._clear_event()
        self.phase = "blocked"
        self.previous_sample_at = None

    def advance(
        self, route: Route, expensive: LegSnapshot, cheap: LegSnapshot, now: datetime,
    ) -> tuple[RouteEvaluation, str | None]:
        if route.route_id != self.route_id:
            raise ValueError("route tracker identity mismatch")
        now = now.astimezone(UTC)
        sampling_gap = (
            self.previous_sample_at is not None
            and now - self.previous_sample_at > timedelta(seconds=12)
        )
        self.previous_sample_at = now
        independent = (
            expensive.sequence is not None and cheap.sequence is not None
            and (self.previous_expensive_sequence is None
                 or expensive.sequence > self.previous_expensive_sequence)
            and (self.previous_cheap_sequence is None
                 or cheap.sequence > self.previous_cheap_sequence)
        )
        if expensive.sequence is not None:
            self.previous_expensive_sequence = max(
                expensive.sequence, self.previous_expensive_sequence or 0
            )
        if cheap.sequence is not None:
            self.previous_cheap_sequence = max(
                cheap.sequence, self.previous_cheap_sequence or 0
            )
        evaluation = evaluate_route(
            route, expensive, cheap, now, target_residual=self.baseline,
            quantity_overrides=self.frozen_quantities,
        )
        if not independent or sampling_gap:
            evaluation = RouteEvaluation(
                **{**evaluation.__dict__, "quality": "blocked",
                   "blockers": (*evaluation.blockers, "sampling_gap" if sampling_gap
                                else "duplicate_or_regressed_sequence")}
            )

        first = evaluation.capacities[0]
        clean_for_baseline = (
            independent and evaluation.blockers in (
                ("normal_reference_missing",), ("normal_reference_missing", "no_capacity_has_valid_depth")
            )
            and first.open_difference is not None
            and first.close_difference_now is not None
            and first.expensive_open_sell is not None
            and first.cheap_open_buy is not None
            and not any("depth_insufficient" in reason or "impact_excess" in reason
                        for reason in first.blockers)
        )
        if self.baseline is None and clean_for_baseline:
            reference = first.cheap_open_buy.unit_price
            if abs(first.open_difference / reference) < route.anomaly_rate:
                self.baseline_samples.append((now, first.close_difference_now))
                self.baseline_samples = self.baseline_samples[-3:]
                if (len(self.baseline_samples) == 3
                        and now - self.baseline_samples[0][0] >= timedelta(seconds=3)):
                    self.baseline = D(str(median(value for _, value in self.baseline_samples)))
                    self.baseline_at = now
                    evaluation = evaluate_route(route, expensive, cheap, now, target_residual=self.baseline)

        if evaluation.quality == "blocked":
            if self.phase in {"dislocation", "confirming", "confirmed"}:
                self._clear_event()
            self.phase = "blocked"
            return evaluation, None
        if self.phase == "blocked":
            self.phase = "watching"
        if first.open_difference is None or first.cheap_open_buy is None:
            return evaluation, None
        current = evaluation.capacities[0]
        if current.open_difference is None or current.cheap_open_buy is None:
            return evaluation, None
        reference = self.reference_price or current.cheap_open_buy.unit_price
        open_difference = current.open_difference
        if self.phase == "confirmed":
            if (self.cooldown_until is not None and now >= self.cooldown_until
                    and open_difference / reference < route.anomaly_rate / 2):
                self._clear_event()
            return evaluation, None
        if self.phase == "watching":
            if (self.cooldown_until is not None and now < self.cooldown_until):
                return evaluation, None
            if (self.baseline is not None and open_difference > self.baseline
                    and open_difference / reference >= route.anomaly_rate
                    and not any("depth_insufficient" in reason or "impact_excess" in reason
                                for reason in current.blockers)):
                self.phase = "dislocation"
                self.first_dislocation_at = now
                self.peak_at = now
                self.peak_difference = open_difference
                self.reference_price = reference
                self.frozen_quantities = tuple(cap.base_quantity for cap in evaluation.capacities)
                self.event_id = sha256(
                    f"{route.route_id}|{utc_iso(now)}".encode()
                ).hexdigest()[:32]
                return evaluation, "dislocation"
            return evaluation, None
        assert self.peak_difference is not None and self.first_dislocation_at is not None
        if (now - self.first_dislocation_at <= timedelta(seconds=60)
                and open_difference > self.peak_difference):
            self.peak_difference = open_difference
            self.peak_at = now
            self.confirmation_started_at = None
            self.confirmation_count = 0
            self.phase = "dislocation"
            return evaluation, None
        excess = self.peak_difference - self.baseline
        contracted = (
            excess > 0
            and open_difference <= self.peak_difference - excess * D("0.20")
            and not current.blockers
        )
        if not contracted:
            self.phase = "dislocation"
            self.confirmation_started_at = None
            self.confirmation_count = 0
            return evaluation, None
        if self.confirmation_started_at is None:
            self.confirmation_started_at = now
            self.confirmation_count = 1
        else:
            self.confirmation_count += 1
        self.phase = "confirming"
        if self.confirmation_count >= 3 and now - self.confirmation_started_at >= timedelta(seconds=3):
            self.phase = "confirmed"
            self.cooldown_until = now + timedelta(minutes=30)
            return evaluation, "confirmed"
        return evaluation, None
