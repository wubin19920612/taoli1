from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

InboxStatus = Literal["new", "researched", "dropped"]
MAX_IMAGES = 9
MAX_IMAGE_BYTES = 15 * 1024 * 1024
FORM_LIMITS = {"client_id": 36, "text": 20000, "note": 2000, "source_app": 200}


class InboxUpload(BaseModel):
    client_id: UUID
    text: str | None = Field(default=None, max_length=FORM_LIMITS["text"])
    note: str | None = Field(default=None, max_length=FORM_LIMITS["note"])
    source_app: str | None = None
    image_count: int = Field(ge=0, le=MAX_IMAGES)

    @model_validator(mode="after")
    def require_content(self):
        if not (self.image_count or (self.text or "").strip() or (self.note or "").strip()):
            raise ValueError("收件箱条目不能为空")
        return self


class InboxItem(BaseModel):
    id: UUID
    created_at: datetime
    text: str | None
    note: str | None
    source_app: str | None
    image_count: int
    status: InboxStatus
    result: str | None
    synced_at: datetime | None
