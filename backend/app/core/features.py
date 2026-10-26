"""Shared guards read the immutable startup configuration, never the environment."""
from collections.abc import Callable

from fastapi import HTTPException, Request

from app.core.config import OPTIONAL_FEATURES


def require_feature(feature: str) -> Callable:
    if feature not in OPTIONAL_FEATURES:
        raise ValueError(f"Unknown feature guard: {feature}")

    async def check(request: Request) -> None:
        if feature in request.app.state.settings.disabled_features:
            raise HTTPException(status_code=404, detail="feature_disabled")

    return check
