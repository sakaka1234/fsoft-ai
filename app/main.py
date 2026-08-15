"""
FastAPI app: lifespan, nạp model, khởi động syncer. SPEC muc 11.2 mục 12.

Khởi động KHÔNG chặn. Model mất vài giây để nạp; nếu chặn lifespan thì /healthz
cũng câm trong lúc đó — mà healthz và readyz chỉ khác nhau khi service nhận
được request TRƯỚC lúc sẵn sàng.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import anyio
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.v1 import index as index_api
from app.config import Settings, get_settings, resolve_path
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.embedding.encoder import Encoder
from app.embedding.vector_index import VectorIndex
from app.store.card_repo import CardRepo
from app.store.db import Database
from app.store.sync_state_repo import SyncStateRepo
from app.store.usage_repo import UsageRepo
from app.sync.fixture_source import FixtureCardSource
from app.sync.http_source import HttpCardSource
from app.sync.source import CardSource
from app.sync.syncer import Syncer

log = get_logger(__name__)


@dataclass
class Service:
    """Mọi thành phần có trạng thái, gom một chỗ để test dựng lại dễ dàng."""

    settings: Settings
    db: Database
    card_repo: CardRepo
    sync_state_repo: SyncStateRepo
    usage_repo: UsageRepo
    index: VectorIndex
    encoder: Encoder
    source: CardSource
    syncer: Syncer

    encoder_ready: bool = False
    index_ready: bool = False
    background: set[asyncio.Task] = field(default_factory=set)

    @property
    def is_ready(self) -> bool:
        return self.encoder_ready and self.index_ready


def build_source(settings: Settings) -> CardSource:
    if settings.ai_source_mode == "fixture":
        return FixtureCardSource(resolve_path(settings.ai_fixture_path))

    return HttpCardSource(
        base_url=settings.ai_backend_url,
        token=settings.ai_backend_token,
        timeout_seconds=settings.ai_backend_timeout_seconds,
    )


def build_service(settings: Settings, encoder: Encoder | None = None) -> Service:
    """
    `encoder` cho phép test dùng chung một model đã nạp sẵn — nạp lại mất 2,5
    giây mỗi lần, nhân với vài chục test là không chấp nhận được.
    """

    db = Database(resolve_path(settings.ai_db_path))

    card_repo = CardRepo(db)
    sync_state_repo = SyncStateRepo(db)
    index = VectorIndex(settings.ai_embedding_dim)
    encoder = encoder or Encoder(settings)
    source = build_source(settings)

    return Service(
        settings=settings,
        db=db,
        card_repo=card_repo,
        sync_state_repo=sync_state_repo,
        usage_repo=UsageRepo(db),
        index=index,
        encoder=encoder,
        source=source,
        syncer=Syncer(
            source=source,
            card_repo=card_repo,
            sync_state_repo=sync_state_repo,
            index=index,
            encoder=encoder,
            settings=settings,
        ),
    )


async def load_index_from_db(service: Service) -> None:
    """
    Dựng vector index TỪ SQLITE, không gọi nguồn.

    Đây là lý do restart không tốn một request nào tới backend: index đã nằm
    sẵn trong SQLite từ lần chạy trước.
    """

    started = time.perf_counter()

    rows = await service.card_repo.load_index_rows()
    service.index.rebuild(rows)
    service.index_ready = True

    log.info(
        "index_loaded_from_sqlite",
        vectors=len(rows),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


async def warmup(service: Service) -> None:
    """Nạp model -> dựng index từ SQLite -> chạy syncer. Chạy nền, không chặn."""

    try:
        if not service.encoder.is_loaded:
            await service.encoder.load()

        service.encoder_ready = True

        await load_index_from_db(service)

        if not service.settings.ai_sync_enabled:
            log.warning("sync_disabled", reason="AI_SYNC_ENABLED=false")
            return

        await service.syncer.run_forever()

    except asyncio.CancelledError:
        raise

    except Exception:
        # Model hỏng thì /readyz đứng ở 503 mãi — đúng ý đồ. Nhưng /healthz vẫn
        # 200 và service không được sập.
        log.exception("warmup_failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings

    service = build_service(settings, encoder=app.state.encoder)
    app.state.service = service

    await anyio.to_thread.run_sync(service.db.connect_sync)

    task = asyncio.create_task(warmup(service), name="warmup")
    service.background.add(task)

    try:
        yield

    finally:
        for background_task in service.background:
            background_task.cancel()

        await asyncio.gather(*service.background, return_exceptions=True)

        if isinstance(service.source, HttpCardSource):
            await service.source.aclose()

        await anyio.to_thread.run_sync(service.db.close_sync)

        log.info("shutdown_complete")


def create_app(settings: Settings | None = None, encoder: Encoder | None = None) -> FastAPI:
    settings = settings or get_settings()

    configure_logging(settings.ai_log_level)

    app = FastAPI(
        title="fsoft-ai",
        description="Service RAG cho hệ thống học từ vựng tiếng Anh",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.encoder = encoder

    register_exception_handlers(app)
    app.include_router(index_api.router)

    @app.get("/healthz", tags=["Health"])
    async def healthz() -> dict:
        """Sống chưa. Không cần token, không phụ thuộc model hay nguồn dữ liệu."""

        return {"status": "ok"}

    @app.get("/readyz", tags=["Health"])
    async def readyz() -> JSONResponse:
        """Model nạp xong và index sẵn sàng chưa."""

        service: Service = app.state.service

        return JSONResponse(
            status_code=200 if service.is_ready else 503,
            content={
                "ready": service.is_ready,
                "encoder_ready": service.encoder_ready,
                "index_ready": service.index_ready,
            },
        )

    return app


app = create_app()
