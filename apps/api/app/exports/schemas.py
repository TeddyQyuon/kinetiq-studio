from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class ExportCreateRequest(BaseModel):
    resolution: Literal["720p", "1080p", "4k"] = "1080p"


class ExportRead(BaseModel):
    id: str
    project_id: str
    revision: int
    resolution: str
    status: str
    download_url: str | None
    srt_url: str | None
    drive_file_id: str | None
    publish_kit: dict
    created_at: datetime
