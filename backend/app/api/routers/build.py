import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.llm.explain import explain_build
from app.llm.providers.factory import get_llm_provider
from app.recommendation.engine import BuildResult, RecommendationEngine
from app.schemas import BuildRequest

logger = logging.getLogger(__name__)
router = APIRouter()


class BuildResponse(BuildResult):
    explanation: str | None = None


@router.post("", response_model=BuildResponse)
def build(
    request: BuildRequest,
    explain: bool = Query(default=False, description="Also generate an LLM explanation of the build."),
    db: Session = Depends(get_db),
) -> BuildResponse:
    result = RecommendationEngine.recommend(db, request)
    if result is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Could not find a compatible build under these constraints. "
                "Try increasing the budget or relaxing a preference (brand, Wi-Fi, form factor)."
            ),
        )

    explanation = None
    if explain:
        try:
            explanation = explain_build(get_llm_provider(), result, request)
        except Exception as exc:
            logger.warning("LLM explanation failed (%s); returning build without one.", exc)
            explanation = "(explanation unavailable: the configured LLM provider could not be reached)"

    return BuildResponse(primary=result.primary, alternatives=result.alternatives, explanation=explanation)
