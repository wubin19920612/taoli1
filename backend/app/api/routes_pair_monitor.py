from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.db.pair_monitor_repository import PairMonitorRepository
from app.models.pair_monitor import (
    PairMonitorHistory,
    PairMonitorRule,
    PairMonitorSampleResult,
)
from app.services.pair_monitor import (
    PairMonitorSampler,
    build_pair_monitor_history,
)

router = APIRouter(prefix="/pair-monitor")


def _verify_write_access(request: Request, password: str | None) -> None:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)


def _repo(request: Request) -> PairMonitorRepository:
    repo = getattr(request.app.state, "pair_monitor_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Pair monitor repository is not ready")
    return repo


def _sampler(request: Request) -> PairMonitorSampler:
    sampler = getattr(request.app.state, "pair_monitor_sampler", None)
    if sampler is None:
        raise HTTPException(status_code=503, detail="Pair monitor sampler is not ready")
    return sampler


@router.get("/rules", response_model=list[PairMonitorRule])
async def list_pair_monitor_rules(request: Request) -> list[PairMonitorRule]:
    return await _repo(request).list_rules()


@router.post("/rules", response_model=PairMonitorRule)
async def create_pair_monitor_rule(
    rule: PairMonitorRule,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> PairMonitorRule:
    _verify_write_access(request, password)
    return await _repo(request).create_rule(rule)


@router.put("/rules/{rule_id}", response_model=PairMonitorRule)
async def update_pair_monitor_rule(
    rule_id: str,
    rule: PairMonitorRule,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> PairMonitorRule:
    _verify_write_access(request, password)
    return await _repo(request).upsert_rule(rule.model_copy(update={"id": rule_id}))


@router.delete("/rules/{rule_id}")
async def delete_pair_monitor_rule(
    rule_id: str,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> dict[str, str]:
    _verify_write_access(request, password)
    await _repo(request).delete_rule(rule_id)
    return {"status": "deleted"}


@router.post("/sample", response_model=list[PairMonitorSampleResult])
async def sample_pair_monitors(
    request: Request,
    rule_id: str | None = Query(default=None),
    password: str | None = Depends(dashboard_password_header),
) -> list[PairMonitorSampleResult]:
    _verify_write_access(request, password)
    return await _sampler(request).sample(
        request.app.state.snapshot_store.get_markets(),
        rule_id=rule_id,
    )


@router.get("/rules/{rule_id}/history", response_model=PairMonitorHistory)
async def get_pair_monitor_history(
    rule_id: str,
    request: Request,
    hours: float = Query(default=24 * 3, gt=0, le=24 * 30),
    point_limit: int = Query(default=2000, ge=1, le=10_000),
) -> PairMonitorHistory:
    repo = _repo(request)
    rule = await repo.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Pair monitor rule not found")
    since = datetime.now(UTC) - timedelta(hours=hours)
    points = await repo.list_points(rule_id, since=since, limit=point_limit)
    return build_pair_monitor_history(rule, points)
