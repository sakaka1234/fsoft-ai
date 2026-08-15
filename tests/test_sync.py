"""
Test vòng lặp đồng bộ. Acceptance SPEC muc 11.2.

Toàn bộ chạy bằng AI_SOURCE_MODE=fixture — không cần backend Java.
"""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.config import Settings
from app.embedding.encoder import Encoder
from app.main import Service, build_service, load_index_from_db
from app.store.sync_state_repo import KEY_LAST_SYNC_ERROR, KEY_LAST_SYNC_TS
from app.sync.http_source import BackendUnauthorized, HttpCardSource
from tests.conftest import CountingSource, edit_card, read_fixture, remove_card

FIXTURE_CARD_COUNT = 24
BACKEND = "http://backend.test/fsoft"


# ---------------------------------------------------------------------
# Đồng bộ gia tăng
# ---------------------------------------------------------------------


async def test_dong_bo_dau_tien_nap_du_24_the(service: Service) -> None:
    stats = await service.syncer.run_incremental()

    assert stats.embedded == FIXTURE_CARD_COUNT
    assert stats.skipped == 0
    assert await service.card_repo.count() == FIXTURE_CARD_COUNT
    assert service.index.size == FIXTURE_CARD_COUNT


async def test_dong_bo_lan_hai_khong_embed_lai_gi(synced_service: Service) -> None:
    """
    Acceptance: chạy lại -> embedded = 0, skipped = 24. content_hash hoạt động.

    Dùng force_full để kéo lại đủ 24 thẻ; chu kỳ gia tăng bình thường chỉ hỏi
    lại vài thẻ mới nhất nên không chứng minh được gì nhiều.
    """

    stats = await synced_service.syncer.run_incremental(force_full=True)

    assert stats.embedded == 0
    assert stats.skipped == FIXTURE_CARD_COUNT


async def test_last_sync_skipped_bang_0_lien_tuc_la_dau_hieu_hong(
    synced_service: Service,
) -> None:
    """
    SPEC muc 8.5: nếu last_sync_skipped luôn bằng 0 thì content_hash đang chết
    và service embed lại toàn bộ mỗi 2 phút.
    """

    await synced_service.syncer.run_incremental(force_full=True)

    assert synced_service.syncer.state.last_sync_skipped > 0
    assert synced_service.syncer.state.last_sync_embedded == 0


async def test_sua_noi_dung_the_thi_embed_lai_dung_the_do(
    synced_service: Service, fixture_file: Path
) -> None:
    edit_card(fixture_file, 101, meaning="nghĩa đã bị sửa để test")

    stats = await synced_service.syncer.run_incremental(force_full=True)

    assert stats.embedded == 1
    assert stats.skipped == FIXTURE_CARD_COUNT - 1

    rows = await synced_service.db.query("SELECT meaning FROM card WHERE card_id = ?", (101,))
    assert rows[0]["meaning"] == "nghĩa đã bị sửa để test"


async def test_doi_deck_id_cap_nhat_ca_sqlite_lan_index(
    synced_service: Service, fixture_file: Path
) -> None:
    edit_card(fixture_file, 101, deckId=2)

    await synced_service.syncer.run_incremental(force_full=True)

    rows = await synced_service.db.query("SELECT deck_id FROM card WHERE card_id = ?", (101,))
    assert rows[0]["deck_id"] == 2

    assert synced_service.index.lookup_word("resilient", [1]) == []
    assert synced_service.index.lookup_word("resilient", [2]) == [101]


async def test_doi_deck_khong_ton_chi_phi_embedding(
    synced_service: Service, fixture_file: Path
) -> None:
    """
    deck_id không nằm trong text đem đi embed, nên đổi deck phải ghi lại thẻ mà
    KHÔNG embed lại — vector cũ vẫn đúng nguyên.
    """

    edit_card(fixture_file, 101, deckId=2)

    stats = await synced_service.syncer.run_incremental(force_full=True)

    assert stats.embedded == 0
    assert synced_service.index.lookup_word("resilient", [2]) == [101]


async def test_doi_audio_url_va_deck_title_cung_duoc_cap_nhat(
    synced_service: Service, fixture_file: Path
) -> None:
    """
    audio_url nuôi quiz dạng LISTENING ở M5, deck_title hiện trong citation ở
    M4. Cả hai đều ngoài text embed nên dễ bị bỏ quên.
    """

    edit_card(
        fixture_file,
        101,
        audioUrl="https://cdn.example.com/moi.mp3",
        deckTitle="Tiêu đề deck đã đổi",
    )

    stats = await synced_service.syncer.run_incremental(force_full=True)

    rows = await synced_service.db.query(
        "SELECT audio_url, deck_title FROM card WHERE card_id = ?", (101,)
    )

    assert stats.embedded == 0
    assert rows[0]["audio_url"] == "https://cdn.example.com/moi.mp3"
    assert rows[0]["deck_title"] == "Tiêu đề deck đã đổi"


async def test_con_tro_lay_tu_du_lieu_khong_lay_tu_dong_ho(synced_service: Service) -> None:
    """
    Acceptance ngầm của SPEC muc 5.4: last_sync_ts = max(updatedAt) THẤY TRONG
    PHẢN HỒI, không phải datetime.now(). Nhờ vậy lệch đồng hồ giữa hai container
    không gây mất dữ liệu.
    """

    stored = await synced_service.sync_state_repo.get(KEY_LAST_SYNC_TS)
    max_in_fixture = max(
        datetime.fromisoformat(c["updatedAt"])
        for c in read_fixture(synced_service.settings.ai_fixture_path)
    )

    assert datetime.fromisoformat(stored) == max_in_fixture

    # Fixture cố ý dùng ngày trong tương lai so với hôm nay, nên nếu con trỏ
    # bị lấy từ datetime.now() thì nó phải nhỏ hơn mốc trong dữ liệu.
    assert abs((datetime.fromisoformat(stored) - datetime.now(UTC)).total_seconds()) > 60


async def test_the_thieu_audio_url_van_nap_binh_thuong(synced_service: Service) -> None:
    """Thẻ 402 (tuition) có audioUrl = null — quiz dạng LISTENING ở M5 cần biết."""

    rows = await synced_service.db.query(
        "SELECT word, audio_url FROM card WHERE card_id = ?", (402,)
    )

    assert rows[0]["word"] == "tuition"
    assert rows[0]["audio_url"] is None


async def test_phan_trang_khi_size_nho_hon_tong_so_the(service: Service) -> None:
    """Nguồn phải trả hết 24 thẻ dù mỗi trang chỉ 5 thẻ, không trùng không sót."""

    service.settings.ai_backend_page_size = 5

    stats = await service.syncer.run_incremental()

    assert stats.embedded == FIXTURE_CARD_COUNT
    assert await service.card_repo.count() == FIXTURE_CARD_COUNT


# ---------------------------------------------------------------------
# Quét ID phát hiện thẻ bị xoá
# ---------------------------------------------------------------------


async def test_quet_id_xoa_the_khong_con_o_nguon(
    synced_service: Service, fixture_file: Path
) -> None:
    remove_card(fixture_file, 101)

    deleted = await synced_service.syncer.run_full_sweep()

    assert deleted == 1
    assert await synced_service.card_repo.count() == FIXTURE_CARD_COUNT - 1
    assert synced_service.index.size == FIXTURE_CARD_COUNT - 1
    assert synced_service.index.lookup_word("resilient", [1]) == []


async def test_quet_id_khong_xoa_gi_khi_nguon_khong_doi(synced_service: Service) -> None:
    assert await synced_service.syncer.run_full_sweep() == 0
    assert await synced_service.card_repo.count() == FIXTURE_CARD_COUNT


async def test_nguon_tra_rong_thi_khong_xoa_sach_index(
    synced_service: Service, fixture_file: Path
) -> None:
    """
    Nguồn rỗng trong khi local có dữ liệu gần như chắc chắn là backend đang hỏng.
    Xoá sạch index vì một lần backend hắt hơi là cái giá quá đắt.
    """

    fixture_file.write_text("[]", encoding="utf-8")

    assert await synced_service.syncer.run_full_sweep() == 0
    assert await synced_service.card_repo.count() == FIXTURE_CARD_COUNT


# ---------------------------------------------------------------------
# Khởi động lại
# ---------------------------------------------------------------------


async def test_restart_nap_index_tu_sqlite_khong_goi_nguon(
    settings: Settings, encoder: Encoder
) -> None:
    """Acceptance: restart -> index nạp từ SQLite, KHÔNG gọi lại nguồn."""

    first = build_service(settings, encoder=encoder)
    first.db.connect_sync()
    await first.syncer.run_incremental()
    first.db.close_sync()

    second = build_service(settings, encoder=encoder)
    spy = CountingSource(second.source)
    second.source = spy
    second.syncer._source = spy
    second.db.connect_sync()

    await load_index_from_db(second)

    assert second.index.size == FIXTURE_CARD_COUNT
    assert spy.fetch_calls == 0
    assert spy.ids_calls == 0

    second.db.close_sync()


async def test_mat_file_sqlite_thi_tu_dong_bo_lai_toan_bo(
    settings: Settings, encoder: Encoder
) -> None:
    """Embedding là dữ liệu dẫn xuất — mất thì tính lại, đó là tính tự chữa lành."""

    first = build_service(settings, encoder=encoder)
    first.db.connect_sync()
    await first.syncer.run_incremental()
    first.db.close_sync()

    for path in Path(settings.ai_db_path).parent.glob("test.db*"):
        path.unlink()

    second = build_service(settings, encoder=encoder)
    second.db.connect_sync()
    await load_index_from_db(second)

    assert second.index.size == 0

    stats = await second.syncer.run_incremental()

    assert stats.embedded == FIXTURE_CARD_COUNT
    assert second.index.size == FIXTURE_CARD_COUNT

    second.db.close_sync()


# ---------------------------------------------------------------------
# Chống sập khi nguồn hỏng
# ---------------------------------------------------------------------


async def test_nguon_hong_khong_lam_sap_service(service: Service) -> None:
    """
    Acceptance: nguồn chết -> run_cycle không raise, ghi last_sync_error,
    và service vẫn sống.
    """

    class DeadSource:
        async def fetch_changed_since(self, since, page, size):
            raise ConnectionError("backend chết")

        async def fetch_all_ids(self):
            raise ConnectionError("backend chết")

        async def health(self):
            return False

    service.syncer._source = DeadSource()

    stats = await service.syncer.run_cycle()

    assert stats.error is not None
    assert "backend chết" in stats.error
    assert service.syncer.state.backend_reachable is False
    assert "backend chết" in (await service.sync_state_repo.get(KEY_LAST_SYNC_ERROR))


async def test_dong_bo_thanh_cong_xoa_loi_cu(service: Service) -> None:
    await service.sync_state_repo.set(KEY_LAST_SYNC_ERROR, "lỗi từ chu kỳ trước")

    await service.syncer.run_cycle()

    assert await service.sync_state_repo.get(KEY_LAST_SYNC_ERROR) is None
    assert service.syncer.state.backend_reachable is True


# ---------------------------------------------------------------------
# HttpCardSource — mock bằng respx, không chạm mạng thật
# ---------------------------------------------------------------------


def page_payload(cards: list[dict], *, last: bool, page: int = 1) -> dict:
    return {
        "status": 200,
        "message": "Success",
        "data": {
            "content": cards,
            "pageNo": page,
            "pageSize": 2,
            "totalElements": len(cards),
            "totalPages": 1,
            "last": last,
        },
    }


def backend_card(card_id: int) -> dict:
    return {
        "cardId": card_id,
        "deckId": 1,
        "deckTitle": "TOEIC - Cảm xúc & Tính cách",
        "word": f"word{card_id}",
        "phonetic": "/test/",
        "partOfSpeech": "adj",
        "meaning": "nghĩa tiếng Việt",
        "definitionEn": "english definition",
        "exampleSentence": "An example sentence.",
        "exampleMeaning": "Một câu ví dụ.",
        "audioUrl": "https://cdn.example.com/a.mp3",
        "note": None,
        "updatedAt": "2026-08-20T03:00:00Z",
    }


@respx.mock
async def test_http_source_phan_trang_3_trang() -> None:
    route = respx.get(f"{BACKEND}/internal/cards/changed-since")
    route.side_effect = [
        httpx.Response(200, json=page_payload([backend_card(1), backend_card(2)], last=False)),
        httpx.Response(200, json=page_payload([backend_card(3), backend_card(4)], last=False)),
        httpx.Response(200, json=page_payload([backend_card(5)], last=True)),
    ]

    source = HttpCardSource(BACKEND, "tok", 5.0)

    collected = []
    page = 1

    while True:
        cards, is_last = await source.fetch_changed_since(None, page, 2)
        collected.extend(cards)

        if is_last:
            break

        page += 1

    await source.aclose()

    assert [c.card_id for c in collected] == [1, 2, 3, 4, 5]
    assert route.call_count == 3


@respx.mock
async def test_http_source_chuyen_het_camel_case_sang_snake_case() -> None:
    respx.get(f"{BACKEND}/internal/cards/changed-since").mock(
        return_value=httpx.Response(200, json=page_payload([backend_card(101)], last=True))
    )

    source = HttpCardSource(BACKEND, "tok", 5.0)
    cards, _ = await source.fetch_changed_since(None, 1, 200)
    await source.aclose()

    card = cards[0]

    assert card.card_id == 101
    assert card.deck_id == 1
    assert card.deck_title == "TOEIC - Cảm xúc & Tính cách"
    assert card.part_of_speech == "adj"
    assert card.definition_en == "english definition"
    assert card.example_sentence == "An example sentence."
    assert card.example_meaning == "Một câu ví dụ."
    assert card.audio_url == "https://cdn.example.com/a.mp3"
    assert card.source_updated_at == datetime(2026, 8, 20, 3, 0, tzinfo=UTC)


@respx.mock
async def test_http_source_gui_dung_header_va_since_dang_z() -> None:
    route = respx.get(f"{BACKEND}/internal/cards/changed-since").mock(
        return_value=httpx.Response(200, json=page_payload([], last=True))
    )

    source = HttpCardSource(BACKEND, "token-bi-mat", 5.0)
    await source.fetch_changed_since(datetime(2026, 8, 20, 3, 0, tzinfo=UTC), 1, 200)
    await source.aclose()

    request = route.calls[0].request

    assert request.headers["X-Internal-Token"] == "token-bi-mat"
    assert "since=2026-08-20T03%3A00%3A00Z" in str(request.url)


@respx.mock
async def test_http_source_ids_tra_mang_so_nguyen_tran() -> None:
    respx.get(f"{BACKEND}/internal/cards/ids").mock(
        return_value=httpx.Response(200, json=page_payload([101, 102, 103], last=True))
    )

    source = HttpCardSource(BACKEND, "tok", 5.0)
    ids = await source.fetch_all_ids()
    await source.aclose()

    assert ids == {101, 102, 103}


@respx.mock
async def test_http_source_401_khong_retry() -> None:
    """Sai token thì thử lại bao nhiêu lần cũng vẫn 401 — đừng phí thời gian."""

    route = respx.get(f"{BACKEND}/internal/cards/changed-since").mock(
        return_value=httpx.Response(401)
    )

    source = HttpCardSource(BACKEND, "sai-token", 5.0)

    with pytest.raises(BackendUnauthorized):
        await source.fetch_changed_since(None, 1, 200)

    await source.aclose()

    assert route.call_count == 1


@respx.mock
async def test_http_source_5xx_thi_retry_roi_moi_bo_cuoc() -> None:
    route = respx.get(f"{BACKEND}/internal/cards/changed-since").mock(
        return_value=httpx.Response(503)
    )

    source = HttpCardSource(BACKEND, "tok", 5.0)

    with pytest.raises(httpx.HTTPStatusError):
        await source.fetch_changed_since(None, 1, 200)

    await source.aclose()

    assert route.call_count == 3


@respx.mock
async def test_http_source_5xx_roi_thanh_cong_thi_tra_du_lieu() -> None:
    route = respx.get(f"{BACKEND}/internal/cards/changed-since")
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(200, json=page_payload([backend_card(101)], last=True)),
    ]

    source = HttpCardSource(BACKEND, "tok", 5.0)
    cards, _ = await source.fetch_changed_since(None, 1, 200)
    await source.aclose()

    assert [c.card_id for c in cards] == [101]
    assert route.call_count == 2
