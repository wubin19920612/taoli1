from typing import Any, Literal

from pydantic import BaseModel, Field


class AstroFieldAssumption(BaseModel):
    field: str
    source: str
    assumed_value: str
    note: str
    needs_verification: bool = True


class AstroPairPlan(BaseModel):
    opportunity_id: str
    symbol: str
    mode: Literal["dry_run"] = "dry_run"
    can_submit: bool
    pair: dict[str, Any] | None = None
    sdk_payload: dict[str, Any] | None = None
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[AstroFieldAssumption] = Field(default_factory=list)


class AstroSdkStatus(BaseModel):
    configured: bool
    dry_run_only: bool
    base_url: str
    admin_prefix: str
    api_key_configured: bool
    list_path: str
    pair_path: str
    message_path: str
    message: str | None = None


class AstroCardCreateRequest(BaseModel):
    max_trade_usdt: float | None = Field(default=None, gt=0)
    leverage: int | None = Field(default=None, ge=1)
    min_notional: float | None = Field(default=None, ge=0)
    max_notional: float | None = Field(default=None, gt=0)
    save_as_default: bool = False


class AstroAlertActionResult(BaseModel):
    enabled: bool
    status: Literal["disabled", "skipped", "created", "updated", "failed"]
    action: str
    message: str
    pair_name: str | None = None
    pair_type: str | None = None

    def format_message(self) -> str:
        return f"Astro: {self.message}"
