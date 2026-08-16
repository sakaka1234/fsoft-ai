"""
Test LLM gateway và ngân sách token. Acceptance SPEC muc 11.4.

Không gọi Groq thật: mock ở tầng transport của httpx2. Test gọi thật nằm riêng
ở cuối, đánh dấu `live` và bị bỏ qua mặc định (`uv run pytest -m live`).
"""

import json as jsonlib
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx2
import pytest

from app.config import PROJECT_ROOT, Settings
from app.core.errors import BudgetExhausted, ProviderUnavailable
from app.llm.budget import TokenBudget, estimate_tokens
from app.llm.client import LlmClient, parse_remaining_tokens, parse_retry_after
from app.llm.registry import PromptNotFound, PromptRegistry
from app.main import Service

BASE_URL = "https://api.groq.com/openai/v1"


def completion_payload(content: str = "Xin chào", model: str = "llama-3.3-70b-versatile") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


class FakeGroq:
    """
    Transport giả cho SDK openai.

    `openai` 3.x dùng **httpx2** chứ không phải `httpx`, nên `respx` không chặn
    được — respx chỉ vá `httpx`. Phải mock ở tầng transport của httpx2.
    """

    def __init__(self, responses: list[httpx2.Response] | Callable) -> None:
        self._responses = responses
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)

        if callable(self._responses):
            return self._responses(request)

        index = min(len(self.requests) - 1, len(self._responses) - 1)

        return self._responses[index]

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def body(self, index: int = 0) -> dict:
        return jsonlib.loads(self.requests[index].content)

    def as_client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self))


def ok(content: str = "Xin chào", model: str = "llama-3.3-70b-versatile", **headers):
    return httpx2.Response(200, json=completion_payload(content, model), headers=headers)


def rate_limited(retry_after: str = "0"):
    return httpx2.Response(
        429, json={"error": {"message": "quá tải"}}, headers={"retry-after": retry_after}
    )


@pytest.fixture
def llm_settings(settings: Settings) -> Settings:
    settings.ai_llm_api_key = "gsk_test"
    settings.ai_llm_base_url = BASE_URL
    settings.ai_llm_max_retries = 2
    settings.ai_global_tokens_per_minute = 100_000

    return settings


def make_client(
    llm_settings: Settings, service: Service, fake: FakeGroq, limit: int | None = None
) -> LlmClient:
    budget = TokenBudget(limit or llm_settings.ai_global_tokens_per_minute)

    return LlmClient(llm_settings, budget, service.usage_repo, http_client=fake.as_client())


# ---------------------------------------------------------------------
# Ngân sách token
# ---------------------------------------------------------------------


def test_bucket_giu_cho_va_tru_dan() -> None:
    budget = TokenBudget(1000)

    budget.reserve(400)
    budget.reserve(400)

    assert budget.snapshot()["used"] == 800
    assert budget.snapshot()["remaining"] == 200


def test_bucket_can_thi_ném_429_kem_retry_after() -> None:
    budget = TokenBudget(1000)
    budget.reserve(900)

    with pytest.raises(BudgetExhausted) as excinfo:
        budget.reserve(200)

    assert excinfo.value.http_status == 429
    assert excinfo.value.code == "BUDGET_EXHAUSTED"
    assert 1 <= excinfo.value.retry_after_seconds <= 60


def test_bucket_tu_reset_sang_phut_moi() -> None:
    budget = TokenBudget(1000)

    phut_truoc = datetime(2026, 8, 20, 10, 30, 5, tzinfo=UTC)
    phut_sau = datetime(2026, 8, 20, 10, 31, 5, tzinfo=UTC)

    budget.reserve(900, now=phut_truoc)

    assert budget.snapshot(now=phut_truoc)["used"] == 900

    budget.reserve(900, now=phut_sau)

    assert budget.snapshot(now=phut_sau)["used"] == 900


def test_settle_thay_uoc_luong_bang_so_that() -> None:
    budget = TokenBudget(1000)

    budget.reserve(500)
    budget.settle(estimated_tokens=500, actual_tokens=120)

    assert budget.snapshot()["used"] == 120


def test_dong_bo_theo_nha_cung_cap_khi_ho_bao_it_hon() -> None:
    """Nhà cung cấp là nguồn sự thật — họ báo còn ít hơn thì tin họ."""

    budget = TokenBudget(1000)
    budget.reserve(100)

    budget.sync_from_provider(remaining_tokens=200)

    assert budget.snapshot()["used"] == 800


def test_dong_bo_khong_ha_thap_muc_da_dung() -> None:
    budget = TokenBudget(1000)
    budget.reserve(900)

    budget.sync_from_provider(remaining_tokens=950)

    assert budget.snapshot()["used"] == 900


def test_uoc_luong_token_ty_le_voi_do_dai() -> None:
    assert estimate_tokens("") >= 1
    assert estimate_tokens("x" * 300) > estimate_tokens("x" * 30)


# ---------------------------------------------------------------------
# Registry prompt
# ---------------------------------------------------------------------


def test_render_prompt_thay_bien() -> None:
    registry = PromptRegistry()

    rendered = registry.render("chat_user_v1", context="NGỮ CẢNH TEST", question="CÂU HỎI TEST")

    assert "NGỮ CẢNH TEST" in rendered
    assert "CÂU HỎI TEST" in rendered
    assert "{{" not in rendered


def test_thieu_bien_thi_nem_loi() -> None:
    """
    Placeholder còn nguyên trong prompt gửi đi không làm gì sập cả — nó chỉ
    khiến model trả lời sai một cách rất khó lần ra. Phải nổ sớm.
    """

    with pytest.raises(KeyError, match="context"):
        PromptRegistry().render("chat_user_v1", question="chỉ có câu hỏi")


def test_prompt_khong_ton_tai_thi_nem_loi() -> None:
    with pytest.raises(PromptNotFound):
        PromptRegistry().render("khong_he_ton_tai")


def test_co_du_ba_prompt_spec_yeu_cau() -> None:
    available = PromptRegistry().available()

    assert "chat_system_v1" in available
    assert "chat_user_v1" in available
    assert "query_rewrite_v1" in available


def test_system_prompt_giu_nguyen_canh_bao_bao_mat() -> None:
    system = PromptRegistry().render("chat_system_v1")

    assert "CẢNH BÁO BẢO MẬT" in system
    assert "KHÔNG PHẢI chỉ thị" in system
    assert "chưa có trong bộ thẻ" in system


# ---------------------------------------------------------------------
# Test tĩnh: cấm nhúng prompt trong .py
# ---------------------------------------------------------------------


def test_khong_co_prompt_nhung_trong_file_py() -> None:
    """
    Acceptance SPEC muc 11.4: không prompt nào được nhúng trong .py ngoài
    app/llm/prompts/.

    Dò bằng dấu hiệu đặc trưng của prompt tiếng Việt gửi cho model.
    """

    dau_hieu = re.compile(
        r"Bạn là trợ lý|QUY TẮC BẮT BUỘC|CẢNH BÁO BẢO MẬT|<NGỮ_CẢNH>|Viết lại câu hỏi",
    )

    vi_pham: list[str] = []

    for path in (PROJECT_ROOT / "app").rglob("*.py"):
        if dau_hieu.search(path.read_text(encoding="utf-8")):
            vi_pham.append(str(path.relative_to(PROJECT_ROOT)))

    assert vi_pham == [], f"Prompt bị nhúng trong: {vi_pham}"


def test_moi_prompt_deu_la_file_txt() -> None:
    prompts_dir = PROJECT_ROOT / "app" / "llm" / "prompts"

    assert prompts_dir.is_dir()
    assert all(p.suffix == ".txt" for p in prompts_dir.iterdir() if p.is_file())


# ---------------------------------------------------------------------
# Đọc header
# ---------------------------------------------------------------------


def test_doc_retry_after() -> None:
    assert parse_retry_after({"retry-after": "3"}, default=1.0) == 3.0
    assert parse_retry_after({}, default=1.0) == 1.0
    assert parse_retry_after({"retry-after": "rác"}, default=1.0) == 1.0


def test_retry_after_bi_chan_tran() -> None:
    """Nhà cung cấp bảo chờ 1 giờ thì request đã hết hạn với người dùng rồi."""

    assert parse_retry_after({"retry-after": "3600"}, default=1.0) == 20.0


def test_doc_remaining_tokens() -> None:
    assert parse_remaining_tokens({"x-ratelimit-remaining-tokens": "5000"}) == 5000
    assert parse_remaining_tokens({}) is None
    assert parse_remaining_tokens({"x-ratelimit-remaining-tokens": "rác"}) is None


# ---------------------------------------------------------------------
# Gọi LLM
# ---------------------------------------------------------------------


async def test_goi_thanh_cong_ghi_usage_log_dung_so_token(
    llm_settings: Settings, service: Service
) -> None:
    fake = FakeGroq(
        [ok("Resilient nghĩa là kiên cường.", **{"x-ratelimit-remaining-tokens": "11000"})]
    )
    client = make_client(llm_settings, service, fake)

    result = await client.complete(task="CHAT", system="hệ thống", user="câu hỏi")

    assert result.text == "Resilient nghĩa là kiên cường."
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 50

    rows = await service.db.query("SELECT * FROM usage_log")

    assert len(rows) == 1
    assert rows[0]["task"] == "CHAT"
    assert rows[0]["provider"] == "groq"
    assert rows[0]["prompt_tokens"] == 100
    assert rows[0]["completion_tokens"] == 50
    assert rows[0]["success"] == 1


async def test_429_kem_retry_after_thi_doi_dung_so_giay(
    llm_settings: Settings, service: Service
) -> None:
    """Acceptance: mock 429 kèm retry-after: 1 -> đợi đúng khoảng 1 giây rồi thử lại."""

    fake = FakeGroq([rate_limited("1"), ok()])
    client = make_client(llm_settings, service, fake)

    started = time.perf_counter()
    result = await client.complete(task="CHAT", system="s", user="u")
    elapsed = time.perf_counter() - started

    assert result.text == "Xin chào"
    assert fake.call_count == 2
    assert 0.9 <= elapsed < 2.5, f"chờ {elapsed:.2f}s, kỳ vọng khoảng 1s"


async def test_429_lien_tuc_thi_ha_cap_sang_model_du_phong(
    llm_settings: Settings, service: Service
) -> None:
    """Acceptance: 429 mãi -> fallback, usage_log.model ghi đúng model dự phòng."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        model = jsonlib.loads(request.content)["model"]

        if model == llm_settings.ai_model_fallback:
            return ok(model=model)

        return rate_limited("0")

    fake = FakeGroq(handler)
    client = make_client(llm_settings, service, fake)

    result = await client.complete(task="CHAT", system="s", user="u")

    assert result.model == llm_settings.ai_model_fallback

    rows = await service.db.query("SELECT model, success FROM usage_log ORDER BY id DESC LIMIT 1")

    assert rows[0]["model"] == llm_settings.ai_model_fallback
    assert rows[0]["success"] == 1


async def test_hong_hoan_toan_thi_503_khong_lo_traceback(
    llm_settings: Settings, service: Service
) -> None:
    """Acceptance: hỏng hết -> 503 PROVIDER_UNAVAILABLE, message tiếng Việt."""

    fake = FakeGroq([httpx2.Response(500, json={"error": {}})])
    client = make_client(llm_settings, service, fake)

    with pytest.raises(ProviderUnavailable) as excinfo:
        await client.complete(task="CHAT", system="s", user="u")

    assert excinfo.value.http_status == 503
    assert excinfo.value.code == "PROVIDER_UNAVAILABLE"
    assert "Traceback" not in excinfo.value.message
    assert "api.groq.com" not in excinfo.value.message


async def test_moi_loi_goi_loi_deu_duoc_ghi_nhat_ky(
    llm_settings: Settings, service: Service
) -> None:
    """
    Bỏ qua lời gọi lỗi là cách chắc chắn nhất để không bao giờ biết tỷ lệ 429
    thật — mà đó là chỉ số cảnh báo sớm quan trọng nhất (SPEC muc 11.8).
    """

    fake = FakeGroq([rate_limited("0")])
    client = make_client(llm_settings, service, fake)

    with pytest.raises(ProviderUnavailable):
        await client.complete(task="CHAT", system="s", user="u")

    rows = await service.db.query("SELECT success, error_code FROM usage_log")

    assert len(rows) > 0
    assert all(row["success"] == 0 for row in rows)
    assert all(row["error_code"] == "RATE_LIMIT" for row in rows)


async def test_ngan_sach_can_thi_khong_goi_nha_cung_cap(
    llm_settings: Settings, service: Service
) -> None:
    """Cạn ngân sách phải chặn TRƯỚC khi tốn một request nào."""

    fake = FakeGroq([ok()])
    client = make_client(llm_settings, service, fake, limit=10)

    with pytest.raises(BudgetExhausted):
        await client.complete(task="CHAT", system="s" * 500, user="u" * 500)

    assert fake.call_count == 0


async def test_dong_bo_ngan_sach_tu_header(llm_settings: Settings, service: Service) -> None:
    fake = FakeGroq([ok(**{"x-ratelimit-remaining-tokens": "3000"})])
    budget = TokenBudget(12_000)
    client = LlmClient(llm_settings, budget, service.usage_repo, http_client=fake.as_client())

    await client.complete(task="CHAT", system="s", user="u")

    assert budget.snapshot()["used"] >= 9000


async def test_json_mode_gui_dung_response_format(llm_settings: Settings, service: Service) -> None:
    fake = FakeGroq([ok('{"a": 1}')])
    client = make_client(llm_settings, service, fake)

    await client.complete(task="QUIZ", system="s", user="u", json_mode=True)

    assert fake.body()["response_format"] == {"type": "json_object"}


async def test_max_retries_cua_sdk_bi_tat(llm_settings: Settings, service: Service) -> None:
    """
    SDK tự retry bằng backoff mù, không đọc retry-after của Groq — thử lại quá
    sớm và chỉ ăn thêm 429. Phải tự xử.
    """

    fake = FakeGroq([httpx2.Response(500, json={"error": {}})])
    client = make_client(llm_settings, service, fake)

    with pytest.raises(ProviderUnavailable):
        await client.complete(task="CHAT", system="s", user="u")

    # 2 model (chính + dự phòng), mỗi model đúng 1 lần vì 5xx không retry.
    assert fake.call_count == 2


async def test_429_thu_lai_dung_so_lan_cau_hinh(llm_settings: Settings, service: Service) -> None:
    llm_settings.ai_llm_max_retries = 2

    fake = FakeGroq([rate_limited("0")])
    client = make_client(llm_settings, service, fake)

    with pytest.raises(ProviderUnavailable):
        await client.complete(task="CHAT", system="s", user="u")

    # (1 lần đầu + 2 lần thử lại) x 2 model = 6
    assert fake.call_count == 6


# ---------------------------------------------------------------------
# Gọi Groq THẬT — bỏ qua mặc định
# ---------------------------------------------------------------------


@pytest.mark.live
async def test_goi_groq_that(service: Service) -> None:
    """
    Acceptance SPEC muc 11.4: gọi Groq thật, usage_log khớp usage của Groq.

        uv run pytest -m live

    Cần AI_LLM_API_KEY thật trong .env. Tốn token thật nên không chạy mặc định.
    """

    settings = Settings()

    if not settings.ai_llm_api_key:
        pytest.skip("Chưa có AI_LLM_API_KEY")

    client = LlmClient(
        settings, TokenBudget(settings.ai_global_tokens_per_minute), service.usage_repo
    )

    result = await client.complete(
        task="CHAT",
        system="Bạn trả lời cực ngắn bằng tiếng Việt.",
        user="Từ 'resilient' nghĩa là gì? Trả lời trong một câu.",
        max_tokens=60,
    )

    await client.aclose()

    assert result.text
    assert result.prompt_tokens > 0
    assert result.completion_tokens > 0

    rows = await service.db.query("SELECT * FROM usage_log ORDER BY id DESC LIMIT 1")

    assert rows[0]["prompt_tokens"] == result.prompt_tokens
    assert rows[0]["completion_tokens"] == result.completion_tokens
    assert rows[0]["success"] == 1
