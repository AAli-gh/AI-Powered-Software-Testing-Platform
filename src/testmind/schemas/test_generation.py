from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from testmind.ai_engine.test_generator.models import GenerationOptions, GenerationResult
from testmind.domain.enums import RunStatus


class TestGenerationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    __test__ = False

    id: uuid.UUID
    run_id: uuid.UUID
    status: RunStatus
    options: GenerationOptions
    provider: str
    model: str | None
    prompt_version: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: GenerationResult | None
