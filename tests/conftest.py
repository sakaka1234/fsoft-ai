"""
Fixture dùng chung cho toàn bộ test M1.

Nguyên tắc: mọi test chạy hoàn toàn ngoại tuyến bằng `AI_SOURCE_MODE=fixture`.
Không cần backend Java, không cần database server, không gọi Groq.
"""

import json
import shutil
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from app.config import PROJECT_ROOT, Settings
from app.embedding.encoder import Encoder
from app.main import Service, build_service, load_index_from_db
from app.schemas.card import SourceCard
from app.sync.fixture_source import FixtureCardSource

ORIGINAL_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "cards.json"


@pytest.fixture(scope="session")
def encoder() -> Encoder:
    """
    Model dùng chung cho cả phiên test.

    Nạp mất khoảng 2,5 giây và tốn ~700 MB RAM — nạp lại cho từng test là không
    khả thi. Encoder không có trạng thái thay đổi nên chia sẻ được an toàn.
    """

    enc = Encoder(Settings())
    enc.load_sync()

    return enc


@pytest.fixture
def fixture_file(tmp_path: Path) -> Path:
    """Bản sao cards.json để test tự do sửa/xoá mà không đụng file gốc."""

    target = tmp_path / "cards.json"
    shutil.copyfile(ORIGINAL_FIXTURE, target)

    return target


@pytest.fixture
def settings(tmp_path: Path, fixture_file: Path) -> Settings:
    return Settings(
        ai_db_path=tmp_path / "test.db",
        ai_fixture_path=fixture_file,
        ai_source_mode="fixture",
        ai_internal_token="test-token",
        ai_sync_enabled=False,  # test tự gọi từng chu kỳ, không chạy nền
        ai_backend_page_size=200,
    )


@pytest.fixture
def service(settings: Settings, encoder: Encoder) -> Iterator[Service]:
    svc = build_service(settings, encoder=encoder)
    svc.db.connect_sync()

    yield svc

    svc.db.close_sync()


# ---------------------------------------------------------------------
# Tiện ích thao tác fixture
# ---------------------------------------------------------------------


def read_fixture(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_fixture(path: Path, cards: list[dict]) -> None:
    path.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")


def edit_card(path: Path, card_id: int, **changes) -> None:
    cards = read_fixture(path)

    for card in cards:
        if card["cardId"] == card_id:
            card.update(changes)

    write_fixture(path, cards)


def remove_card(path: Path, card_id: int) -> None:
    write_fixture(path, [c for c in read_fixture(path) if c["cardId"] != card_id])


def make_card(card_id: int = 1, deck_id: int = 1, **overrides) -> SourceCard:
    data = {
        "card_id": card_id,
        "deck_id": deck_id,
        "deck_title": "Deck test",
        "word": "resilient",
        "phonetic": "/rɪˈzɪliənt/",
        "part_of_speech": "adj",
        "meaning": "kiên cường, có khả năng phục hồi nhanh",
        "definition_en": "able to recover quickly from difficult conditions",
        "example_sentence": "She remained resilient despite repeated setbacks.",
        "example_meaning": "Cô ấy vẫn kiên cường dù liên tục gặp thất bại.",
        "audio_url": None,
        "note": None,
        "source_updated_at": datetime.fromisoformat("2026-08-20T03:00:00+00:00"),
    }
    data.update(overrides)

    return SourceCard(**data)


class CountingSource:
    """Bọc FixtureCardSource và đếm số lần bị gọi — dùng cho test restart."""

    def __init__(self, inner: FixtureCardSource) -> None:
        self._inner = inner
        self.fetch_calls = 0
        self.ids_calls = 0

    async def fetch_changed_since(self, since, page, size):
        self.fetch_calls += 1

        return await self._inner.fetch_changed_since(since, page, size)

    async def fetch_all_ids(self):
        self.ids_calls += 1

        return await self._inner.fetch_all_ids()

    async def health(self) -> bool:
        return await self._inner.health()


@pytest.fixture
async def synced_service(service: Service) -> Service:
    """Service đã đồng bộ xong 24 thẻ từ fixture."""

    await service.syncer.run_incremental()
    await load_index_from_db(service)

    return service


@pytest.fixture
async def retrieval_service(synced_service: Service) -> Service:
    """Như `synced_service`, cộng thêm centroid intent đã warmup."""

    await synced_service.intent_classifier.warmup()

    return synced_service
