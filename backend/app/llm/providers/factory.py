from app.config import get_settings
from app.llm.providers.base import LLMProvider

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def get_llm_provider() -> LLMProvider:
    """Builds the configured LLMProvider.

    `llm_provider` is one of:
    - "ollama": default, local.
    - "groq": pre-wired free cloud provider.
    - "custom": any other OpenAI-compatible endpoint (LM Studio, OpenRouter,
      Gemini's OpenAI-compatible endpoint, etc.), configured via
      CUSTOM_LLM_BASE_URL, CUSTOM_LLM_API_KEY, and CUSTOM_LLM_MODEL.

    A single "custom" option avoids a near-duplicate branch for every vendor
    that speaks the OpenAI API.
    """
    settings = get_settings()

    if settings.llm_provider == "ollama":
        from app.llm.providers.ollama_provider import OllamaProvider

        return OllamaProvider()

    if settings.llm_provider == "groq":
        from app.llm.providers.cloud_provider import CloudProvider

        return CloudProvider(base_url=GROQ_BASE_URL, api_key=settings.groq_api_key, model=settings.groq_model)

    if settings.llm_provider == "custom":
        from app.llm.providers.cloud_provider import CloudProvider

        return CloudProvider(
            base_url=settings.custom_llm_base_url,
            api_key=settings.custom_llm_api_key,
            model=settings.custom_llm_model,
        )

    raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")
