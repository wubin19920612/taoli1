from pydantic import BaseModel, Field


class ServiceControlDetail(BaseModel):
    name: str
    available: bool = False
    container_id: str | None = None
    container_name: str | None = None
    state: str | None = None
    status: str | None = None


class ServiceControlStatus(BaseModel):
    enabled: bool
    environment: str = ""
    services: list[str] = Field(default_factory=list)
    details: list[ServiceControlDetail] = Field(default_factory=list)
    message: str | None = None


class ServiceRestartResult(BaseModel):
    service: str
    status: str
    message: str | None = None
