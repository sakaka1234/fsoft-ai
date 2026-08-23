"""
Cấu hình — mọi giá trị lấy từ biến môi trường. Xem SPEC muc 12.

Tên field trùng tên biến môi trường (pydantic-settings không phân biệt hoa
thường), nên `ai_top_k` đọc từ `AI_TOP_K`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
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
    # Không được đọc từ trong code Python. `CMD` của Dockerfile mới là chỗ dùng
    # nó, theo thứ tự `${PORT:-${AI_SERVICE_PORT:-8000}}`: các nền tảng PaaS tự
    # đặt `PORT` và biến đó phải thắng, còn `AI_SERVICE_PORT` để đổi cổng khi tự
    # chạy container. Chạy `uvicorn` trực tiếp ngoài Docker thì phải tự truyền
    # `--port`, biến này không có tác dụng.
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
    # Biến thể ONNX. Mặc định là bản lượng tử 8 bit ĐÃ TỈA TỪ VỰNG: đỉnh RSS
    # 317 MB thay vì 936 MB của bản fp32, cùng Recall@5/MRR, nạp nhanh hơn ba
    # lần. Bảng so sánh đầy đủ và lý do ở `app/embedding/encoder.py`.
    #
    # File này KHÔNG có trên Hugging Face — `scripts/tia_vocab.py` sinh ra nó,
    # và Dockerfile chạy script đó lúc build.
    #
    # ĐỔI GIÁ TRỊ NÀY LÀ PHẢI ĐỔI KÈM `ai_min_score` VÀ `ai_model_version`:
    # mỗi biến thể có phân bố cosine riêng, và vector cũ không dùng lại được.
    # Bảng ngưỡng ở `app/embedding/encoder.py`; sai cặp thì retrieval kém đi âm
    # thầm chứ không báo lỗi. `Settings` tự kiểm cặp này lúc khởi tạo.
    ai_embedding_model_file: str = "onnx/model_tia113k.onnx"
    ai_embedding_dim: int = 384
    ai_model_version: str = "e5-small-q8-tia113k@t1"
    # Bộ cấp phát arena của ONNX Runtime. Tắt tiết kiệm ~47 MB RSS mà độ trễ
    # không đổi — batch ở đây quá nhỏ để arena có ích.
    ai_onnx_cpu_arena: bool = False
    # Số luồng ONNX Runtime dùng cho một phép suy luận. `0` nghĩa là để ORT tự
    # quyết, và ORT quyết bằng số nhân của MÁY CHỦ — không phải theo giới hạn CPU
    # của container. Trên một host 16 nhân bị bóp còn 0,1 vCPU, nó vẫn mở 16 luồng
    # rồi tranh nhau, chậm hơn hẳn chạy 1 luồng.
    #
    # Vì vậy Dockerfile đặt tường minh `AI_ORT_INTRA_OP_THREADS=1`, còn máy dev
    # để `0` cho nhanh (đo được: 1 luồng chậm hơn 2,1 lần trên máy 16 nhân).
    #
    # LƯU Ý: `OMP_NUM_THREADS` và `ORT_NUM_THREADS` KHÔNG điều khiển việc này.
    # ONNX Runtime không đọc hai biến đó; nó chỉ nhận qua `SessionOptions`. Niềm
    # tin ngược lại từng được ghi ở 9 chỗ trong repo này và đều đã sửa.
    ai_ort_intra_op_threads: int = 0
    ai_query_prefix: str = "query: "
    ai_passage_prefix: str = "passage: "
    ai_embed_batch_size: int = 32
    # CHƯA ĐƯỢC ĐỌC Ở ĐÂU. Tokenizer của E5 tự cắt ở 512 token, nên hiện giới
    # hạn này có hiệu lực ngầm chứ không phải do code ta áp. Thẻ dài hơn bị cắt
    # âm thầm — muốn cảnh báo thì phải tự đếm token trong text_builder.
    ai_embed_max_tokens: int = 512
    fastembed_cache_path: Path | None = None

    # ---- Retrieval (M2) ----
    ai_top_k: int = 3
    ai_lexical_candidates: int = 20
    ai_semantic_candidates: int = 20
    ai_rrf_k: int = 60
    # Cổng lọc liên quan trên điểm cosine của tầng semantic. Hiệu chỉnh từ số
    # đo thật trên bộ 40 case, KHÔNG phải con số 0.35 phỏng đoán ban đầu —
    # E5 nén điểm vào dải 0.80-0.95 nên 0.35 không lọc được gì.
    ai_min_score: float = 0.8344
    ai_intent_threshold: float = 0.50
    # Biên độ tối thiểu giữa hạng nhất và hạng nhì khi phân loại intent bằng
    # centroid. KHÔNG phải ngưỡng tuyệt đối — xem docs/M0_FINDINGS.md muc 2.5.
    ai_intent_min_margin: float = 0.01

    # ---- LLM (M3) ----
    ai_llm_base_url: str = "https://api.groq.com/openai/v1"
    ai_llm_api_key: str = ""
    # Bốn model của M0 (`llama-3.3-70b-versatile`, `llama-3.1-8b-instant`) đã bị
    # Groq KHAI TỬ. Gọi vào trả `404 model_not_found`, mà `LlmClient` quy mọi lỗi
    # nhà cung cấp về `PROVIDER_UNAVAILABLE` nên triệu chứng là "Trợ lý AI đang
    # quá tải" — không ai đoán được là do model không còn tồn tại.
    #
    # Bộ thay thế đo ngày 17/08/2026 trên chính khoá đang dùng. Ba ứng viên còn
    # lại đều bị loại vì lý do cụ thể:
    #
    #   openai/gpt-oss-120b   CHỌN cho chat+quiz. Tiếng Việt tốt, trích dẫn [#id]
    #                         đúng, JSON mode 4/4. TPM 8.000.
    #   openai/gpt-oss-20b    CHỌN cho rewrite+fallback. Nhanh, cùng TPM, JSON
    #                         mode cũng đạt — quan trọng vì fallback dùng cho MỌI
    #                         task, kể cả quiz cần JSON.
    #   qwen/qwen3.6-27b      LOẠI: rò khối suy luận `<think>...` thẳng vào
    #                         `content`, tức là vào câu trả lời của người dùng.
    #   groq/compound-mini    LOẠI dù TPM 70.000: là hệ thống agentic, tự thêm
    #                         khung công cụ (727 token prompt so với 219 của
    #                         gpt-oss cho cùng đầu vào) và có thể tự tra web —
    #                         phá hợp đồng "chỉ trả lời từ bộ thẻ".
    ai_model_chat: str = "openai/gpt-oss-120b"
    ai_model_rewrite: str = "openai/gpt-oss-20b"
    ai_model_quiz: str = "openai/gpt-oss-120b"
    ai_model_fallback: str = "openai/gpt-oss-20b"
    # 400 là con số của thời llama và nó KHÔNG còn an toàn.
    #
    # Họ `gpt-oss` sinh `reasoning_tokens` ẩn và TRỪ VÀO chính hạn mức này: đo
    # được 79-296 token suy luận cho một yêu cầu JSON ngắn. Hệ quả đo được, ba
    # lần mỗi mức:
    #
    #   max_tokens=120  ->  0/3   max_tokens=300  ->  3/3
    #   max_tokens=200  ->  0/3   max_tokens=500  ->  3/3
    #
    # Và cách nó hỏng là thứ tệ nhất: `400 json_validate_failed` với
    # `failed_generation` RỖNG — không có gì cho biết là hết hạn mức. Đặt 700 để
    # phần suy luận tệ nhất (296) vẫn còn ~400 token cho câu trả lời thật.
    ai_max_output_tokens: int = 700
    ai_temperature: float = 0.3
    ai_llm_timeout_seconds: float = 30.0
    ai_llm_max_retries: int = 2

    # ---- Ngân sách token (M3) ----
    # 80% của 8.000 TPM. Con số 9.600 cũ là 80% của 12.000 TPM mà M0 đo được trên
    # `llama-3.3-70b-versatile` — model đó không còn, và trần thật của `gpt-oss`
    # là 8.000 (đọc từ header `x-ratelimit-limit-tokens`, kèm
    # `x-ratelimit-reset-tokens` dưới 1 giây nên đúng là cửa sổ mỗi phút).
    #
    # Nghĩa là 9.600 KHÔNG phải "chừa 20% biên" nữa mà là VƯỢT trần 20%: ngân
    # sách nội bộ không bao giờ chặn trước, Groq mới là chỗ chặn, và lúc đó lỗi
    # trả về là 429 của nhà cung cấp chứ không phải `BUDGET_EXHAUSTED` có kèm
    # `retry_after_seconds` cho client.
    ai_global_tokens_per_minute: int = 6400
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

    # ---- Trích xuất từ vựng (M8) ----
    # Cần JSON mode, và cần đọc hiểu đủ tốt để CHỌN LỌC từ đáng học chứ không
    # chỉ dịch. Đây là tác vụ khó nhất trong service, nên dùng bản 120b.
    ai_model_vocab: str = "openai/gpt-oss-120b"
    # Trần độ dài đoạn văn. Vượt là 400 chứ KHÔNG cắt bớt: cắt im lặng làm người
    # dùng mất phần cuối bài đọc mà không biết, còn tự chia nhỏ thì vi phạm SPEC
    # mục 4.2 ("không chunking / text splitter") — chia nhỏ là việc của backend.
    #
    # Nâng con số này là nâng thẳng phần ngân sách token mà MỘT request chiếm
    # của cả hệ thống. Ở 4.000 ký tự với `max_candidates=6`, một lượt gọi đã đặt
    # chỗ 87% ngân sách mỗi phút; `tests/test_vocab.py` có test khoá lại điều đó
    # và sẽ đỏ nếu ai nâng lên mà không tính lại.
    ai_vocab_max_text_chars: int = 4000

    # ---- Demo mode ----
    ai_demo_mode: bool = False

    @model_validator(mode="after")
    def _canh_cap_model_va_nguong(self) -> "Settings":
        """
        Cảnh báo khi biến thể ONNX và `AI_MIN_SCORE` không đi cùng nhau.

        Đây là kiểu hỏng tệ nhất của hệ thống này: không exception, không log
        lỗi, service vẫn trả 200 — chỉ là cổng lọc liên quan không còn tách được
        và những câu lẽ ra trả rỗng bắt đầu trả về thẻ bừa. Đo thật: dùng bản
        lượng tử với ngưỡng 0.83 của fp32 làm 2 trong 5 case NEGATIVE hỏng.

        Chỉ CẢNH BÁO chứ không chặn: ngưỡng là thứ được phép chỉnh tay khi hiệu
        chỉnh lại trên bộ thẻ thật, không nên khoá cứng.
        """

        from app.embedding.encoder import MIN_SCORE_THEO_MODEL

        mong_doi = MIN_SCORE_THEO_MODEL.get(self.ai_embedding_model_file)

        if mong_doi is not None and abs(self.ai_min_score - mong_doi) > 1e-6:
            import warnings

            warnings.warn(
                f"AI_MIN_SCORE={self.ai_min_score} không phải ngưỡng đã hiệu chỉnh cho "
                f"{self.ai_embedding_model_file} (mong đợi {mong_doi}). Nếu đây là cố ý "
                "thì bỏ qua; nếu không, cổng lọc liên quan sẽ sai âm thầm — xem "
                "MIN_SCORE_THEO_MODEL trong app/embedding/encoder.py.",
                stacklevel=2,
            )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
