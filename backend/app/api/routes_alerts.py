from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.db.repositories import AlertEventRepository, AlertRuleRepository
from app.models.alert import AlertEvent, AlertRule

router = APIRouter(prefix="/alerts")


def _verify_write_access(request: Request, password: str | None) -> None:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)


def _rule_repo(request: Request) -> AlertRuleRepository:
    repo = getattr(request.app.state, "alert_rule_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Alert repository is not ready")
    return repo


def _event_repo(request: Request) -> AlertEventRepository:
    repo = getattr(request.app.state, "alert_event_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Alert event repository is not ready")
    return repo


@router.get("/rules", response_model=list[AlertRule])
async def list_rules(request: Request) -> list[AlertRule]:
    return await _rule_repo(request).list()


@router.post("/rules", response_model=AlertRule)
async def create_rule(
    rule: AlertRule,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> AlertRule:
    _verify_write_access(request, password)
    return await _rule_repo(request).create(rule)


@router.put("/rules/{rule_id}", response_model=AlertRule)
async def update_rule(
    rule_id: str,
    rule: AlertRule,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> AlertRule:
    _verify_write_access(request, password)
    updated = rule.model_copy(update={"id": rule_id})
    return await _rule_repo(request).upsert(updated)


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: str,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> dict[str, str]:
    _verify_write_access(request, password)
    await _rule_repo(request).delete(rule_id)
    return {"status": "deleted"}


@router.get("/events", response_model=list[AlertEvent])
async def list_events(request: Request, limit: int = 100) -> list[AlertEvent]:
    return await _event_repo(request).list(limit=limit)


@router.post("/test", response_model=AlertEvent)
async def create_test_event(
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> AlertEvent:
    _verify_write_access(request, password)
    event = AlertEvent(
        rule_id="manual-test",
        opportunity_id="manual-test",
        symbol="TESTUSDT",
        status="sent",
        message="Manual alert test from dashboard",
        created_at=datetime.now(UTC),
    )
    return await _event_repo(request).create(event)
