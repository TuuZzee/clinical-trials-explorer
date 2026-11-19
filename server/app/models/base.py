"""Shared strict model configuration and numeric constraints."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
