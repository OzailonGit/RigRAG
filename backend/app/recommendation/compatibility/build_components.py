from pydantic import BaseModel, ConfigDict


class BuildComponents(BaseModel):
    """The concrete parts under consideration for one candidate build.

    Each field holds the flat dict produced by
    app.services.parts_service.to_full_dict (Product row merged with its
    category spec row). The /api/parts endpoints return the same shape, so
    compatibility checks, the recommendation engine, and the API share one
    representation.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    cpu: dict
    motherboard: dict
    ram: dict
    storage: list[dict]
    psu: dict
    cooler: dict
    case: dict
    gpu: dict | None = None
