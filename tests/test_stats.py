"""Quan trắc. Acceptance SPEC muc 11.8."""

from datetime import UTC, datetime, timedelta

import pytest

from app.api.v1.stats import build_response
from app.main import Service
from app.store.usage_repo import UsageStats


async def _log(service: Service, **kwargs) -> None:
    base = {
        "task": "CHAT",
        "provider": "local",
        "model": "-",
        "latency_ms": 10,
        "success": True,
    }

    await service.usage_repo.insert(**(base | kwargs))


async def _stats_now(service: Service) -> UsageStats:
    now = datetime.now(UTC)

    return await service.usage_repo.stats(now - timedelta(hours=1), now + timedelta(minutes=1))


# ---------------------------------------------------------------------
# Cái bẫy đo lường: nếu nhánh 0 token không ghi log thì chỉ số quan trọng
# nhất của M7 luôn bằng 0 dù hệ thống chạy hoàn hảo.
# ---------------------------------------------------------------------


async def test_luot_mien_phi_co_dong_trong_usage_log(retrieval_service: Service) -> None:
    """
    Ba nhánh 0 token không gọi LLM, nên `LlmClient` không ghi gì cho chúng.
    `ChatOrchestrator` phải tự ghi, nếu không thì mẫu số của `free_ratio` chỉ
    chứa những lượt TỐN tiền.
    """

    await retrieval_service.orchestrator.chat(query="hôm nay trời đẹp không", allowed_deck_ids=[1])
    await retrieval_service.orchestrator.chat(query="resilient nghĩa là gì", allowed_deck_ids=[1])

    stats = await _stats_now(retrieval_service)

    assert stats.chat_turns == 2
    assert stats.by_answer_source == {"CANNED": 1, "DIRECT_LOOKUP": 1}
    assert stats.free_turns == 2
    assert stats.free_ratio == 1.0
    assert stats.prompt_tokens == 0


async def test_free_ratio_tinh_dung_khi_co_ca_hai_loai(service: Service) -> None:
    await _log(service, answer_source="DIRECT_LOOKUP")
    await _log(service, answer_source="CACHE")
    await _log(service, answer_source="CANNED")
    await _log(
        service, answer_source="RAG", provider="groq", prompt_tokens=900, completion_tokens=100
    )
    await _log(
        service, answer_source="RAG", provider="groq", prompt_tokens=1100, completion_tokens=100
    )

    stats = await _stats_now(service)

    assert stats.chat_turns == 5
    assert stats.free_turns == 3
    assert stats.free_ratio == pytest.approx(0.6)
    assert stats.avg_tokens_per_chat == pytest.approx(2200 / 5)


async def test_task_khong_phai_chat_khong_lam_lech_free_ratio(service: Service) -> None:
    """QUIZ và REWRITE cũng tốn token nhưng không phải "lượt chat"."""

    await _log(service, answer_source="CANNED")
    await _log(service, task="QUIZ", provider="groq", prompt_tokens=500)
    await _log(service, task="REWRITE", provider="groq", prompt_tokens=150)

    stats = await _stats_now(service)

    assert stats.calls == 3
    assert stats.chat_turns == 1
    assert stats.free_ratio == 1.0
    assert stats.by_task == {"CHAT": 1, "QUIZ": 1, "REWRITE": 1}
    assert stats.prompt_tokens == 650


async def test_ty_le_loi_dem_ca_moi_task(service: Service) -> None:
    await _log(service, success=False, error_code="PROVIDER_UNAVAILABLE")
    await _log(service, success=True, answer_source="CANNED")
    await _log(service, success=True, answer_source="CANNED")
    await _log(service, success=True, answer_source="CANNED")

    stats = await _stats_now(service)

    assert stats.failed_calls == 1
    assert stats.error_rate == pytest.approx(0.25)


async def test_p95_do_tre(service: Service) -> None:
    for ms in (10, 20, 30, 40, 50, 60, 70, 80, 90, 5000):
        await _log(service, latency_ms=ms, answer_source="RAG")

    stats = await _stats_now(service)

    # 10 mẫu, offset = int(10 * 0.95) = 9 -> phần tử lớn nhất.
    assert stats.latency_p95_ms == 5000


async def test_cua_so_thoi_gian_khong_dem_dong_ngoai_khoang(service: Service) -> None:
    await _log(service, answer_source="CANNED")

    now = datetime.now(UTC)

    trong = await service.usage_repo.stats(now - timedelta(hours=1), now + timedelta(minutes=1))
    ngoai = await service.usage_repo.stats(now - timedelta(days=9), now - timedelta(days=8))

    assert trong.calls == 1
    assert ngoai.calls == 0
    assert ngoai.free_ratio == 0.0
    assert ngoai.latency_p95_ms == 0


# ---------------------------------------------------------------------
# So với ngưỡng SPEC muc 11.8
# ---------------------------------------------------------------------


def _stats(**kwargs) -> UsageStats:
    now = datetime.now(UTC)

    return UsageStats(since=now - timedelta(hours=1), until=now, **kwargs)


def test_bang_stats_bao_dat_khi_moi_chi_so_trong_nguong() -> None:
    body = build_response(
        _stats(calls=10, chat_turns=10, free_turns=5, chat_tokens=8000, latency_p95_ms=1500)
    )

    assert body.free_ratio.value == 0.5
    assert body.free_ratio.target == 0.40
    assert body.free_ratio.ok
    assert body.avg_tokens_per_chat.value == 800.0
    assert body.all_targets_met


def test_bang_stats_bao_truot_khi_dot_token_qua_nhieu() -> None:
    """Dưới 40% miễn phí là dấu hiệu đang trả tiền cho việc làm được miễn phí."""

    body = build_response(
        _stats(calls=10, chat_turns=10, free_turns=1, chat_tokens=20000, latency_p95_ms=1500)
    )

    assert not body.free_ratio.ok
    assert not body.avg_tokens_per_chat.ok
    assert body.latency_p95_ms.ok
    assert not body.all_targets_met


def test_khong_co_luot_nao_thi_khong_chia_cho_khong() -> None:
    """
    Cửa sổ rỗng phải trả 0 chứ không nổ ZeroDivisionError.

    Hệ quả cố ý: `all_targets_met` khi đó là `false` — "chưa có dữ liệu", không
    phải "đang hỏng". Luật cảnh báo phải kiểm `chat_turns > 0` trước khi đọc cờ
    này, đúng như README ghi. Khoá lại ở đây để hành vi không âm thầm đổi.
    """

    body = build_response(_stats())

    assert body.free_ratio.value == 0.0
    assert body.avg_tokens_per_chat.value == 0.0
    assert body.error_rate.value == 0.0
    assert body.chat_turns == 0
    assert not body.all_targets_met
