from typing import Literal

from pydantic import BaseModel


class CompatibilityResult(BaseModel):
    rule: str
    compatible: bool
    severity: Literal["blocking", "warning", "info"]
    message: str
