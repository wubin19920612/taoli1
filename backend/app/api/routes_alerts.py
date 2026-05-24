from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.db.repositories import AlertEventRepository, AlertRuleRepository
from app.models.alert import AlertEvent, AlertRule
from app.models.settings import AlertMessageTemplateSettings
from app.services.alert_metrics import observe_alert_metrics
from app.services.alert_messages import build_alert_message

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


def _history_repo(request: Request):
    repo = getattr(request.app.state, "history_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Opportunity history repository is not ready")
    return repo


async def _alert_message_template(request: Request) -> AlertMessageTemplateSettings:
    repo = getattr(request.app.state, "settings_repo", None)
    if repo is None:
        return AlertMessageTemplateSettings()
    return await repo.get_alert_message_template()


async def _resolve_event_message(request: Request, event: AlertEvent) -> str:
    rule = await _rule_repo(request).get(event.rule_id)
    if rule is None:
        return event.message

    history_repo = _history_repo(request)
    rows = await history_repo.list_before(
        opportunity_id=event.opportunity_id,
        before=event.created_at,
        limit=max(rule.consecutive_hits, 1),
    )
    if not rows:
        return event.message

    observations = [observe_alert_metrics(row, row.observed_at) for row in reversed(rows)]
    return build_alert_message(
        rule,
        rows[0],
        observations=observations,
        template=await _alert_message_template(request),
    )


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
    events = await _event_repo(request).list(limit=limit)
    resolved: list[AlertEvent] = []
    for event in events:
        resolved_message = await _resolve_event_message(request, event)
        resolved.append(event.model_copy(update={"message": resolved_message}))
    return resolved


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
