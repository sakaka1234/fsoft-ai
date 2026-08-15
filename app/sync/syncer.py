"""
Vòng lặp đồng bộ. SPEC muc 5.4.

fsoft-ai CHỦ ĐỘNG kéo dữ liệu từ backend. Backend không cần biết fsoft-ai tồn tại.

Hai nhịp:
  - Gia tăng mỗi 120 giây, có chồng lấn 5 giây
  - Quét toàn bộ ID mỗi 60 phút để phát hiện thẻ bị xoá

Nguyên tắc bất di bất dịch: lỗi đồng bộ KHÔNG BAO GIỜ được làm sập service.
Đồng bộ hỏng nghĩa là index cũ đi, chat vẫn chạy trên dữ liệu cũ.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.core.logging import get_logger
from app.embedding.encoder import Encoder
from app.embedding.text_builder import build_text_and_hash
from app.embedding.vector_index import VectorIndex
from app.schemas.card import CardToStore, IndexRow, SourceCard
from app.store.card_repo import CardFingerprint, CardRepo
from app.store.sync_state_repo import (
    KEY_LAST_FULL_SWEEP_AT,
    KEY_LAST_SYNC_ERROR,
    KEY_LAST_SYNC_TS,
    SyncStateRepo,
)
from app.sync.source import CardSource

log = get_logger(__name__)

# Chặn vòng lặp vô hạn nếu nguồn trả `last=false` mãi. 1000 trang x 200 thẻ =
# 200.000 thẻ, gấp bốn lần giả định 50.000 ở SPEC muc 4.3.
MAX_PAGES = 1000


@dataclass
class SyncStats:
    embedded: int = 0
    skipped: int = 0
    deleted: int = 0
    duration_ms: int = 0
    error: str | None = None


@dataclass
class SyncerState:
    """Chỉ số phù du cho /internal/v1/index/status — mất khi restart cũng không sao."""

    last_sync_at: str | None = None
    last_sync_duration_ms: int | None = None
    last_sync_embedded: int = 0
    last_sync_skipped: int = 0
    backend_reachable: bool | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Syncer:
    def __init__(
        self,
        *,
        source: CardSource,
        card_repo: CardRepo,
        sync_state_repo: SyncStateRepo,
        index: VectorIndex,
        encoder: Encoder,
        settings: Settings,
    ) -> None:
        self._source = source
        self._cards = card_repo
        self._state_repo = sync_state_repo
        self._index = index
        self._encoder = encoder
        self._settings = settings
        self.state = SyncerState()

    # ---------------------------------------------------------------
    # Đồng bộ gia tăng
    # ---------------------------------------------------------------

    async def run_incremental(self, *, force_full: bool = False) -> SyncStats:
        async with self.state.lock:
            return await self._run_incremental_unlocked(force_full=force_full)

    async def _run_incremental_unlocked(self, *, force_full: bool = False) -> SyncStats:
        started = time.perf_counter()
        stats = SyncStats()

        # force_full bỏ qua con trỏ và kéo lại toàn bộ. Không embed lại gì cả
        # nếu content_hash không đổi — nên rẻ, và là cách duy nhất để chữa index
        # khi con trỏ bị lệch hoặc khi đổi AI_MODEL_VERSION.
        since = None if force_full else await self._compute_since()

        fingerprints = await self._cards.get_fingerprints()
        model_version = self._settings.ai_model_version

        max_seen: datetime | None = None
        page = 1

        while page <= MAX_PAGES:
            cards, is_last = await self._source.fetch_changed_since(
                since, page, self._settings.ai_backend_page_size
            )

            if cards:
                page_max = max(c.source_updated_at for c in cards)
                max_seen = page_max if max_seen is None else max(max_seen, page_max)

                embedded = await self._process_page(cards, fingerprints, model_version)

                stats.embedded += embedded
                stats.skipped += len(cards) - embedded

            if is_last:
                break

            page += 1

        else:
            log.warning("sync_page_limit_reached", max_pages=MAX_PAGES)

        # Mốc thời gian lấy TỪ DỮ LIỆU, không lấy từ đồng hồ máy. Nhờ vậy lệch
        # đồng hồ giữa hai container không gây mất dữ liệu.
        if max_seen is not None:
            await self._state_repo.set(KEY_LAST_SYNC_TS, max_seen.isoformat())

        stats.duration_ms = int((time.perf_counter() - started) * 1000)

        self.state.last_sync_at = datetime.now(UTC).isoformat()
        self.state.last_sync_duration_ms = stats.duration_ms
        self.state.last_sync_embedded = stats.embedded
        self.state.last_sync_skipped = stats.skipped

        log.info(
            "sync_incremental_done",
            embedded=stats.embedded,
            skipped=stats.skipped,
            duration_ms=stats.duration_ms,
            since=since.isoformat() if since else None,
        )

        return stats

    async def _compute_since(self) -> datetime | None:
        """
        Lùi mốc 5 giây rồi mới hỏi nguồn.

        Nếu nhiều thẻ có cùng updated_at và nằm vắt qua ranh giới trang, dùng
        mốc chính xác sẽ BỎ SÓT bản ghi. Chồng lấn khiến kéo trùng, và
        content_hash khiến kéo trùng thành no-op. Cả vòng lặp thành idempotent.
        """

        raw = await self._state_repo.get(KEY_LAST_SYNC_TS)

        if raw is None:
            return None

        return datetime.fromisoformat(raw) - timedelta(
            seconds=self._settings.ai_sync_overlap_seconds
        )

    async def _process_page(
        self,
        cards: list[SourceCard],
        fingerprints: dict[int, CardFingerprint],
        model_version: str,
    ) -> int:
        """
        Trả về số thẻ thật sự phải EMBED lại.

        Ba nhánh, vì "phải embed lại" và "phải ghi lại" là hai câu hỏi khác nhau:

          1. Nội dung đổi (content_hash khác) hoặc đổi model  -> embed lại
          2. Nội dung y nguyên nhưng deck/tiêu đề/audio đổi   -> ghi lại, DÙNG
             LẠI vector cũ, tốn 0 chi phí embedding
          3. Không đổi gì                                     -> bỏ qua hẳn

        Thiếu nhánh 2 thì thẻ chuyển deck sẽ mắc kẹt ở deck cũ trong index —
        vừa sai kết quả, vừa là lỗ hổng phạm vi.
        """

        needs_embed: list[tuple[SourceCard, str]] = []
        metadata_only: list[tuple[SourceCard, str]] = []

        for card in cards:
            _, content_hash = build_text_and_hash(card)

            known = fingerprints.get(card.card_id)

            if known is None or known.content_hash != content_hash:
                needs_embed.append((card, content_hash))
                continue

            if known.model_version != model_version:
                needs_embed.append((card, content_hash))
                continue

            if (known.deck_id, known.deck_title, known.audio_url) != (
                card.deck_id,
                card.deck_title,
                card.audio_url,
            ):
                metadata_only.append((card, content_hash))

        to_store: list[CardToStore] = []

        if needs_embed:
            texts = [build_text_and_hash(card)[0] for card, _ in needs_embed]
            vectors = await self._encoder.embed_passages(texts)

            to_store.extend(
                CardToStore(
                    source=card,
                    content_hash=content_hash,
                    model_version=model_version,
                    vector=vector,
                )
                for (card, content_hash), vector in zip(needs_embed, vectors)
            )

        if metadata_only:
            stored_vectors = await self._cards.get_vectors(
                [card.card_id for card, _ in metadata_only]
            )

            to_store.extend(
                CardToStore(
                    source=card,
                    content_hash=content_hash,
                    model_version=model_version,
                    vector=stored_vectors[card.card_id],
                )
                for card, content_hash in metadata_only
                if card.card_id in stored_vectors
            )

            log.info(
                "sync_metadata_only_update",
                count=len(metadata_only),
                card_ids=[card.card_id for card, _ in metadata_only],
            )

        if not to_store:
            return 0

        await self._cards.upsert_many(to_store)

        self._index.upsert(
            [
                IndexRow(
                    card_id=item.source.card_id,
                    deck_id=item.source.deck_id,
                    word=item.source.word,
                    vector=item.vector,
                )
                for item in to_store
            ]
        )

        for item in to_store:
            fingerprints[item.source.card_id] = CardFingerprint(
                content_hash=item.content_hash,
                model_version=model_version,
                deck_id=item.source.deck_id,
                deck_title=item.source.deck_title,
                audio_url=item.source.audio_url,
            )

        return len(needs_embed)

    # ---------------------------------------------------------------
    # Quét toàn bộ ID để phát hiện thẻ bị xoá
    # ---------------------------------------------------------------

    async def run_full_sweep(self) -> int:
        async with self.state.lock:
            return await self._run_full_sweep_unlocked()

    async def _run_full_sweep_unlocked(self) -> int:
        remote_ids = await self._source.fetch_all_ids()
        local_ids = await self._cards.all_ids()

        if not remote_ids and local_ids:
            # Nguồn trả rỗng trong khi local có dữ liệu gần như chắc chắn là
            # backend đang hỏng chứ không phải mọi thẻ vừa bị xoá thật. Xoá sạch
            # index vì một lần backend hắt hơi là cái giá quá đắt.
            log.warning("sweep_skipped_empty_source", local_count=len(local_ids))
            return 0

        to_delete = local_ids - remote_ids

        if to_delete:
            await self._cards.delete_many(to_delete)
            self._index.delete(to_delete)

            log.info("sweep_deleted", count=len(to_delete), card_ids=sorted(to_delete))

        await self._state_repo.set(KEY_LAST_FULL_SWEEP_AT, datetime.now(UTC).isoformat())

        return len(to_delete)

    # ---------------------------------------------------------------
    # Một chu kỳ đầy đủ, đã bọc chống lỗi
    # ---------------------------------------------------------------

    async def run_cycle(self, *, with_sweep: bool = False, force_full: bool = False) -> SyncStats:
        """
        Không bao giờ raise. Lỗi được ghi vào sync_state.last_sync_error và
        trả về trong SyncStats.
        """

        try:
            stats = await self.run_incremental(force_full=force_full)

            if with_sweep:
                stats.deleted = await self.run_full_sweep()

            self.state.backend_reachable = True
            await self._state_repo.clear(KEY_LAST_SYNC_ERROR)

            return stats

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"

            log.exception("sync_failed", error=message)

            self.state.backend_reachable = False

            try:
                await self._state_repo.set(KEY_LAST_SYNC_ERROR, message)

            except Exception:
                log.exception("sync_error_not_persisted")

            return SyncStats(error=message)

    # ---------------------------------------------------------------
    # Task nền
    # ---------------------------------------------------------------

    async def run_forever(self) -> None:
        interval = self._settings.ai_sync_interval_seconds
        sweep_interval = self._settings.ai_full_sweep_interval_seconds

        last_sweep = time.monotonic()

        # Chu kỳ đầu tiên chạy ngay, kèm quét ID: sau khi restart, thẻ bị xoá
        # lúc service đang tắt sẽ không được feed gia tăng báo cho biết.
        await self.run_cycle(with_sweep=True)

        while True:
            await asyncio.sleep(interval)

            now = time.monotonic()
            with_sweep = (now - last_sweep) >= sweep_interval

            if with_sweep:
                last_sweep = now

            await self.run_cycle(with_sweep=with_sweep)
