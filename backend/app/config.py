from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", env_file_encoding="utf-8", extra="ignore")

    # Structured DB
    database_url: str = "postgresql+psycopg://pcbuilder:pcbuilder@localhost:5432/pcbuilder"

    # Vector DB
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "pc_parts"

    # LLM
    llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    custom_llm_base_url: str = ""
    custom_llm_api_key: str = ""
    custom_llm_model: str = ""

    # Embeddings
    embedding_provider: str = "local"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    # Catalog: Open Icecat. The demo account works for single-product lookups;
    # downloading index files needs a registered (free) account and password.
    icecat_username: str = "openIcecat-live"
    icecat_password: str = ""
    icecat_cache_dir: str = ""  # empty: <repo>/data/raw/icecat
    icecat_request_interval_s: float = 0.25

    # Prices: Shopee Affiliate Open API
    shopee_app_id: str = ""
    shopee_secret: str = ""
    shopee_api_url: str = "https://open-api.affiliate.shopee.ph/graphql"

    # Prices: Lazada Open Platform (affiliate)
    lazada_app_key: str = ""
    lazada_app_secret: str = ""
    lazada_access_token: str = ""
    lazada_api_url: str = "https://api.lazada.com.ph/rest"
    lazada_product_search_path: str = "/marketing/product/feed"

    price_request_interval_s: float = 1.0
    price_max_age_days: int = 14

    # App
    app_env: str = "development"
    default_currency: str = "PHP"


@lru_cache
def get_settings() -> Settings:
    return Settings()
