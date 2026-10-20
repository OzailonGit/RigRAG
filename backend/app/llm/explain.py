from app.json_utils import to_prompt_json
from app.llm.build_summary import summarize_build_result, summarize_scored_build
from app.llm.prompts.system_prompts import EXPLANATION_SYSTEM, FOLLOWUP_EXPLANATION_SYSTEM
from app.llm.providers.base import LLMProvider
from app.recommendation.engine import BuildResult
from app.schemas import BuildRequest


def explain_build(llm: LLMProvider, result: BuildResult, request: BuildRequest,
                  balanced: BuildResult | None = None) -> str:
    """Turns an already-computed, already-validated BuildResult into a
    natural-language explanation.

    The LLM sees only a compact summary of the build (see build_summary.py),
    never the raw catalog, so every spec it mentions comes from the result.
    `balanced` is the same request without request.favor, for comparison.
    """
    payload = {
        "user_request": request.model_dump(mode="json"),
        "build": summarize_build_result(result),
    }
    if balanced is not None:
        payload["balanced_build"] = summarize_scored_build(balanced.primary)
    messages = [
        {"role": "system", "content": EXPLANATION_SYSTEM},
        {"role": "user", "content": to_prompt_json(payload)},
    ]
    response = llm.chat(messages)
    return response.content or "(the model returned no explanation)"


def explain_followup(
    llm: LLMProvider,
    question: str,
    current: BuildResult,
    request: BuildRequest,
    comparison: BuildResult | None = None,
    comparison_request: BuildRequest | None = None,
) -> str:
    """Answers a follow-up question about a build, e.g. "why not the Ryzen 7
    instead?" or "what if I increase my budget by 10000?".

    For a what-if question, the caller must already have re-run
    RecommendationEngine.recommend() under the modified BuildRequest and pass
    the result as `comparison`, with that request as `comparison_request`, so
    the model can pair each build with the request that produced it. This
    function never re-runs the engine.
    """
    payload = {
        "user_question": question,
        "current_request": request.model_dump(mode="json"),
        "current_build": summarize_build_result(current),
        "comparison_request": comparison_request.model_dump(mode="json") if comparison_request else None,
        "comparison_build": summarize_build_result(comparison) if comparison else None,
    }
    messages = [
        {"role": "system", "content": FOLLOWUP_EXPLANATION_SYSTEM},
        {"role": "user", "content": to_prompt_json(payload)},
    ]
    response = llm.chat(messages)
    return response.content or "(the model returned no explanation)"
