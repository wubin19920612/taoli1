from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.security import dashboard_password_header, verify_dashboard_password
from app.db.repositories import PhonePriceAlertEventRepository, PhonePriceAlertRuleRepository
from app.models.phone_alert import (
    PhonePriceAlertDiagnostic,
    PhonePriceAlertDiagnostics,
    PhonePriceAlertEvent,
    PhonePriceAlertRule,
)
from app.services.phone_price_alerts import (
    find_phone_price_alert_market,
    phone_price_crosses_threshold,
    resolve_phone_price_alert_price,
)

router = APIRouter(prefix="/phone-alerts")


def _verify_write_access(request: Request, password: str | None) -> None:
    verify_dashboard_password(request.app.state.settings.dashboard_password, password)


def _rule_repo(request: Request) -> PhonePriceAlertRuleRepository:
    repo = getattr(request.app.state, "phone_price_alert_rule_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Phone price alert repository is not ready")
    return repo


def _event_repo(request: Request) -> PhonePriceAlertEventRepository:
    repo = getattr(request.app.state, "phone_price_alert_event_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Phone price alert event repository is not ready")
    return repo


@router.get("/rules", response_model=list[PhonePriceAlertRule])
async def list_rules(request: Request) -> list[PhonePriceAlertRule]:
    return await _rule_repo(request).list()


@router.post("/rules", response_model=PhonePriceAlertRule)
async def create_rule(
    rule: PhonePriceAlertRule,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> PhonePriceAlertRule:
    _verify_write_access(request, password)
    return await _rule_repo(request).create(rule)


@router.put("/rules/{rule_id}", response_model=PhonePriceAlertRule)
async def update_rule(
    rule_id: str,
    rule: PhonePriceAlertRule,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> PhonePriceAlertRule:
    _verify_write_access(request, password)
    return await _rule_repo(request).upsert(rule.model_copy(update={"id": rule_id}))


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: str,
    request: Request,
    password: str | None = Depends(dashboard_password_header),
) -> dict[str, str]:
    _verify_write_access(request, password)
    await _rule_repo(request).delete(rule_id)
    return {"status": "deleted"}


@router.get("/events", response_model=list[PhonePriceAlertEvent])
async def list_events(request: Request, limit: int = 100) -> list[PhonePriceAlertEvent]:
    return await _event_repo(request).list(limit=limit)


@router.get("/diagnostics", response_model=PhonePriceAlertDiagnostics)
async def get_diagnostics(request: Request) -> PhonePriceAlertDiagnostics:
    rules = await _rule_repo(request).list()
    markets = request.app.state.snapshot_store.get_markets()
    exchange_errors = request.app.state.snapshot_store.get_exchange_errors()
    items = [_diagnose_rule(rule, markets, exchange_errors) for rule in rules]
    return PhonePriceAlertDiagnostics(
        phone_enabled=bool(request.app.state.settings.feishu_phone_enabled),
        items=items,
    )


def _diagnose_rule(
    rule: PhonePriceAlertRule,
    markets: list,
    exchange_errors: dict[str, str],
) -> PhonePriceAlertDiagnostic:
    market = find_phone_price_alert_market(markets, rule)
    exchange_error = _exchange_error_for_rule(rule, exchange_errors)
    if market is None:
        reason = "market not found"
        if exchange_error:
            reason = f"{reason}; exchange error: {exchange_error}"
        return PhonePriceAlertDiagnostic(
            rule_id=rule.id,
            rule_name=rule.name,
            symbol=rule.symbol,
            exchange=rule.exchange,
            market_type=rule.market_type,
            price_field=rule.price_field,
            condition=rule.condition,
            target_price=rule.target_price,
            market_found=False,
            triggered=False,
            exchange_error=exchange_error,
            reason=reason,
        )

    price_result = resolve_phone_price_alert_price(market, rule.price_field)
    if price_result is None:
        return PhonePriceAlertDiagnostic(
            rule_id=rule.id,
            rule_name=rule.name,
            symbol=rule.symbol,
            exchange=rule.exchange,
            market_type=rule.market_type,
            price_field=rule.price_field,
            condition=rule.condition,
            target_price=rule.target_price,
            market_found=True,
            triggered=False,
            exchange_error=exchange_error,
            reason=f"{rule.price_field.value} price unavailable",
        )

    observed_price, resolved_field = price_result
    triggered = phone_price_crosses_threshold(rule, observed_price)
    if triggered:
        reason = "trigger ready"
    else:
        operator = ">=" if rule.condition.value == "above" else "<="
        reason = f"current price {observed_price:g} has not reached {operator} {rule.target_price:g}"
    return PhonePriceAlertDiagnostic(
        rule_id=rule.id,
        rule_name=rule.name,
        symbol=market.symbol,
        exchange=market.exchange,
        market_type=market.market_type,
        price_field=rule.price_field,
        resolved_price_field=resolved_field,
        condition=rule.condition,
        target_price=rule.target_price,
        market_found=True,
        observed_price=observed_price,
        triggered=triggered,
        exchange_error=exchange_error,
        reason=reason,
    )


def _exchange_error_for_rule(rule: PhonePriceAlertRule, errors: dict[str, str]) -> str | None:
    if not rule.exchange:
        return None
    exact = errors.get(f"{rule.exchange}:{rule.market_type.value}")
    if exact:
        return exact
    exchange_prefix = f"{rule.exchange}:"
    return next((message for key, message in errors.items() if key.startswith(exchange_prefix)), None)
