from fastapi import Header, HTTPException


def verify_dashboard_password(expected_password: str, provided_password: str | None) -> None:
    if not expected_password:
        return
    if provided_password != expected_password:
        raise HTTPException(status_code=401, detail="Invalid dashboard password")


async def dashboard_password_header(x_dashboard_password: str | None = Header(default=None)) -> str | None:
    return x_dashboard_password
