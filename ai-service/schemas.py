from typing import Literal

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    user_request: str = Field(min_length=1, max_length=4000)


class RunResponse(BaseModel):
    task_id: str
    status: Literal["received"]
    message: str
