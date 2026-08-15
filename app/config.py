"""
Cấu hình — mọi giá trị lấy từ biến môi trường. Xem SPEC muc 12.

Tên field trùng tên biến môi trường (pydantic-settings không phân biệt hoa
thường), nên `ai_top_k` đọc từ `AI_TOP_K`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# app/config.py -> app/ -> gốc repo
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(path: Path) -> Path:
    """
    Đường dẫn tương đối trong cấu hình được hiểu là tương đối với gốc repo,
    không phải với thư mục hiện hành.

    Nhờ vậy chạy `uvicorn app.main:app` từ thư mục con vẫn dùng đúng một file
    SQLite, không sinh ra file thứ hai.
    """

    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Service ----
    ai_service_port: int = 8000
    ai_internal_token: str = "dev-token"
    ai_log_level: str = "INFO"

    # ---- Lưu trữ ----
    ai_db_path: Path = Path("./data/fsoft-ai.db")

    # ---- Nguồn dữ liệu ----
    ai_source_mode: Literal["fixture", "http"] = "fixture"
    ai_fixture_path: Path = Path("tests/fixtures/cards.json")
    ai_backend_url: str = ""
    ai_backend_token: str = ""
    ai_backend_timeout_seconds: float = 20.0
    ai_backend_page_size: int = 200

    # ---- Đồng bộ ----
    ai_sync_enabled: bool = True
    ai_sync_interval_seconds: int = 120
    ai_sync_overlap_seconds: int = 5
    ai_full_sweep_interval_seconds: int = 3600

    # ---- Embedding ----
    ai_embedding_model: str = "intfloat/multilingual-e5-small"
    ai_embedding_dim: int = 384
    ai_model_version: str = "multilingual-e5-small@t1"
    ai_query_prefix: str = "query: "
    ai_passage_prefix: str = "passage: "
    ai_embed_batch_size: int = 32
    ai_embed_max_tokens: int = 512
    fastembed_cache_path: Path | None = None

    # ---- Retrieval (M2) ----
    ai_top_k: int = 3
    ai_lexical_candidates: int = 20
    ai_semantic_candidates: int = 20
    ai_rrf_k: int = 60
    ai_min_score: float = 0.35
    ai_intent_threshold: float = 0.50

    # ---- LLM (M3) ----
    ai_llm_base_url: str = "https://api.groq.com/openai/v1"
    ai_llm_api_key: str = ""
    ai_model_chat: str = "llama-3.3-70b-versatile"
    ai_model_rewrite: str = "llama-3.1-8b-instant"
    ai_model_quiz: str = "llama-3.3-70b-versatile"
    ai_model_fallback: str = "llama-3.1-8b-instant"
    ai_max_output_tokens: int = 400
    ai_temperature: float = 0.3
    ai_llm_timeout_seconds: float = 30.0
    ai_llm_max_retries: int = 2

    # ---- Ngân sách token (M3) ----
    ai_global_tokens_per_minute: int = 9600
    ai_history_max_messages: int = 6
    ai_context_max_chars_per_field: int = 300

    # ---- Cache (M4) ----
    ai_semantic_cache_enabled: bool = True
    ai_semantic_cache_threshold: float = 0.97
    ai_semantic_cache_max_size: int = 500
    ai_semantic_cache_ttl_hours: int = 24

    # ---- Quiz (M5) ----
    ai_quiz_distractor_max_cosine: float = 0.92
    ai_quiz_llm_batch_size: int = 5

    # ---- Demo mode ----
    ai_demo_mode: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
