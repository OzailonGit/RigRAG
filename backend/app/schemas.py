import enum
import uuid

from pydantic import BaseModel, Field


class UseCase(str, enum.Enum):
    gaming = "gaming"
    productivity = "productivity"
    ai_ml = "ai_ml"
    video_editing = "video_editing"
    rendering_3d = "rendering_3d"
    programming = "programming"
    general = "general"
    mixed = "mixed"


class Resolution(str, enum.Enum):
    r1080p = "1080p"
    r1440p = "1440p"
    r4k = "4K"


class BuildRequest(BaseModel):
    """Structured build state produced by the intent parser (LLM or
    rule-based) and consumed by the recommendation engine.

    Nothing here is inferred by an LLM at recommendation time; by the time it
    reaches the engine, it is plain data.
    """

    budget_php: float = Field(gt=0)
    currency: str = "PHP"
    use_case: list[UseCase] = Field(default_factory=lambda: [UseCase.gaming])
    resolution: Resolution | None = None
    target_fps: int | None = None
    games: list[str] = Field(default_factory=list)

    cpu_brand: str | None = None  # "AMD" | "Intel"
    gpu_brand: str | None = None  # "AMD" | "NVIDIA"
    wifi_required: bool = False
    small_form_factor: bool = False
    quiet: bool = False
    low_power: bool = False
    prioritize_upgradeability: bool = False

    # category -> product_id the user already owns; the engine builds around
    # these instead of shopping for them.
    existing_parts: dict[str, uuid.UUID] = Field(default_factory=dict)
