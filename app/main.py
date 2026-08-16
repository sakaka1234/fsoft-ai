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
from typing import Literal

import anyio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.v1 import chat as chat_api
from app.api.v1 import index as index_api
from app.api.v1 import quiz as quiz_api
from app.api.v1 import search as search_api
from app.api.v1 import stats as stats_api
from app.chat.orchestrator import ChatOrchestrator
from app.chat.semantic_cache import SemanticCache
from app.config import Settings, get_settings, resolve_path
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.embedding.encoder import Encoder
from app.llm.budget import TokenBudget
from app.llm.client import LlmClient
from app.llm.registry import PromptRegistry
from app.quiz.generator import QuizGenerator
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.intent import IntentClassifier
from app.retrieval.search_index import SearchIndex
from app.store.card_repo import CardRepo
from app.store.db import Database
from app.store.sync_state_repo import SyncStateRepo
from app.store.usage_repo import UsageRepo
from app.sync.fixture_source import FixtureCardSource
from app.sync.http_source import HttpCardSource
from app.sync.source import CardSource
from app.sync.syncer import Syncer

log = get_logger(__name__)


API_DESCRIPTION = """
Trả lời câu hỏi **chỉ dựa trên bộ thẻ của chính người học**, tìm kiếm ngữ nghĩa,
và sinh câu hỏi ôn tập.

Frontend **không bao giờ** gọi thẳng vào đây — backend Java gọi, và chính backend
chịu trách nhiệm tính `allowed_deck_ids`.

---

### Bắt đầu thử trong 30 giây

1. Bấm **Authorize** ở góc trên bên phải, dán giá trị `AI_INTERNAL_TOKEN` trong `.env`.
2. Mở `GET /internal/v1/index/status`, bấm **Try it out** → **Execute**.
   Thấy `card_count > 0` là index đã sẵn sàng.
3. Mở `POST /internal/v1/search`, giữ nguyên ví dụ có sẵn, bấm **Execute**.

Mọi ví dụ trong trang này dùng dữ liệu mẫu (`AI_SOURCE_MODE=fixture`, 24 thẻ):

| Deck | Nội dung | Thẻ |
|---|---|---|
| 1 | TOEIC — Cảm xúc & Tính cách | 101–109 |
| 2 | TOEIC — Công việc & Văn phòng | 201–208 |
| 3 | IELTS — Môi trường | 301–304 |
| 4 | IELTS — Giáo dục | 401–403 |

Nối vào backend thật (`AI_SOURCE_MODE=http`) thì ID sẽ khác — lấy danh sách thật
bằng `GET /internal/v1/index/status`.

---

### `allowed_deck_ids` là toàn bộ ranh giới bảo mật

Service này **không biết gì về người dùng**: không có `profileId`, không có phiên
đăng nhập, không có bảng phân quyền. Nó chỉ tin vào danh sách deck mà người gọi
truyền vào.

- Danh sách **rỗng** nghĩa là **không được phép gì cả** → `400`.
  Tuyệt đối không hiểu thành "không lọc".
- Thẻ ngoài danh sách sẽ không bao giờ xuất hiện, kể cả trong trích dẫn của câu
  trả lời hay trong đáp án nhiễu của quiz.

---

### Ba nhánh trả lời **0 token**

Mỗi lượt chat đều thử các nhánh rẻ trước. Đọc `answer_source` để biết lượt đó có
tốn tiền không:

| `answer_source` | Token | Khi nào |
|---|---|---|
| `CANNED` | **0** | Câu ngoài chủ đề học tiếng Anh |
| `DIRECT_LOOKUP` | **0** | Tra nghĩa một từ có sẵn trong bộ thẻ |
| `CACHE` | **0** | Câu tương tự đã hỏi trong 24 giờ, cùng phạm vi deck |
| `RAG` | ~570–800 | Cần LLM diễn giải trên ngữ cảnh lấy từ bộ thẻ |
| `LLM_ONLY` | ~500–600 | Không thẻ nào khớp — trả lời kèm cảnh báo |

Hai con số cuối là **đo thật** trên dữ liệu mẫu 24 thẻ với `top_k` mặc định là 3
(tổng `prompt_tokens + completion_tokens`, trung vị ~730). Chúng tăng theo `top_k`
và theo độ dài nội dung thẻ, nên bộ thẻ thật có thẻ đầy đủ ví dụ song ngữ sẽ tốn
hơn. Ngưỡng cần giữ là **trung bình < 1.200 token mỗi lượt chat**, theo dõi bằng
`GET /internal/v1/stats`.

`POST /search` và quiz với `use_ai_context=false` **luôn** 0 token.

---

### Lỗi

Mọi lỗi đều có đúng một hình dạng, không bao giờ lộ traceback:

```json
{ "error": { "code": "INVALID_SCOPE", "message": "allowed_deck_ids không được rỗng." } }
```

Rẽ nhánh theo `code`, đừng khớp theo `message` — `message` là tiếng Việt hiển thị
cho người dùng và có thể đổi câu chữ bất cứ lúc nào.

| HTTP | `code` | Nên làm gì |
|---|---|---|
| 400 | `INVALID_SCOPE`, `INVALID_REQUEST` | Lỗi phía người gọi, sửa request |
| 401 | `UNAUTHORIZED` | Sai `X-Internal-Token` |
| 429 | `BUDGET_EXHAUSTED` | Hạn mức **toàn cục**, không phải của riêng người dùng. Đợi `retry_after_seconds` |
| 503 | `INDEX_NOT_READY` | Đang khởi động, đợi `/readyz` trả 200 |
| 503 | `PROVIDER_UNAVAILABLE` | Groq hỏng. Cân nhắc lùi về `use_ai_context=false` |

---

### Hạn chế đã biết

Câu hỏi thuần tiếng Việt viết **không dấu** trả về rỗng (`"tu nao chi cam giac lo lang"`).
Câu có chứa từ tiếng Anh vẫn chạy nhờ tầng khớp chính xác. Chi tiết ở `docs/SPEC.md`
mục 14.2b.
"""


OPENAPI_TAGS = [
    {
        "name": "Health",
        "description": (
            "Không cần token. `/healthz` trả 200 ngay cả khi model hỏng — dùng cho "
            "liveness probe. `/readyz` chỉ 200 khi model đã nạp và index sẵn sàng — "
            "dùng cho readiness probe. Đừng dùng lẫn hai cái: lấy `/readyz` làm "
            "liveness sẽ khiến hạ tầng giết service trong lúc nó đang nạp model."
        ),
    },
    {
        "name": "Internal - Chat",
        "description": (
            "Hỏi đáp trên bộ thẻ của người học. Có bản chờ-hết-mới-trả và bản streaming SSE."
        ),
    },
    {
        "name": "Internal - Search",
        "description": (
            "Tìm kiếm ngữ nghĩa, **0 token**. Cũng là công cụ tốt nhất để soi xem "
            "retrieval đang nghĩ gì khi chat trả lời lạ."
        ),
    },
    {
        "name": "Internal - Quiz",
        "description": (
            "Sinh câu hỏi ôn tập. Ba trong bốn dạng dựng thẳng từ dữ liệu thẻ, không chạm LLM."
        ),
    },
    {
        "name": "Internal - Index",
        "description": (
            "Quản trị chỉ mục: xem tình trạng đồng bộ và ép đồng bộ ngay. Gọi khi "
            "gỡ lỗi, không phải trong luồng nghiệp vụ — service tự đồng bộ mỗi 120 giây."
        ),
    },
    {
        "name": "Internal - Quan trắc",
        "description": (
            "Số liệu vận hành. Câu hỏi quan trọng nhất nó trả lời: có đang đốt token "
            "vào việc mà dữ liệu cục bộ làm được miễn phí không."
        ),
    },
]


class HealthzResponse(BaseModel):
    """Cố ý chỉ có một trường: endpoint này không được phụ thuộc vào thứ gì."""

    status: Literal["ok"] = "ok"


class ReadyzResponse(BaseModel):
    ready: bool = Field(description="Bằng `encoder_ready AND index_ready`.")
    encoder_ready: bool = Field(description="Model ONNX đã nạp vào RAM chưa.")
    index_ready: bool = Field(description="Đã dựng xong chỉ mục vector và BM25 chưa.")


@dataclass
class Service:
    """Mọi thành phần có trạng thái, gom một chỗ để test dựng lại dễ dàng."""

    settings: Settings
    db: Database
    card_repo: CardRepo
    sync_state_repo: SyncStateRepo
    usage_repo: UsageRepo
    index: SearchIndex
    encoder: Encoder
    source: CardSource
    syncer: Syncer
    retriever: HybridRetriever
    intent_classifier: IntentClassifier
    budget: TokenBudget
    prompts: PromptRegistry
    llm: LlmClient
    cache: SemanticCache
    orchestrator: ChatOrchestrator
    quiz: QuizGenerator

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
    usage_repo = UsageRepo(db)
    index = SearchIndex(settings.ai_embedding_dim)
    encoder = encoder or Encoder(settings)
    source = build_source(settings)
    budget = TokenBudget(settings.ai_global_tokens_per_minute)
    prompts = PromptRegistry()
    llm = LlmClient(settings, budget, usage_repo)
    intent_classifier = IntentClassifier(encoder, min_margin=settings.ai_intent_min_margin)

    retriever = HybridRetriever(
        index,
        rrf_k=settings.ai_rrf_k,
        lexical_candidates=settings.ai_lexical_candidates,
        semantic_candidates=settings.ai_semantic_candidates,
        min_score=settings.ai_min_score,
    )

    cache = SemanticCache(
        # AI_DEMO_MODE hạ ngưỡng để ngày bảo vệ nhiều câu hỏi tương tự cùng
        # trúng cache hơn, giảm rủi ro nghẽn token (SPEC muc 14.1).
        threshold=0.90 if settings.ai_demo_mode else settings.ai_semantic_cache_threshold,
        max_size=settings.ai_semantic_cache_max_size,
        ttl_hours=settings.ai_semantic_cache_ttl_hours,
        enabled=settings.ai_semantic_cache_enabled,
    )

    return Service(
        settings=settings,
        db=db,
        card_repo=card_repo,
        sync_state_repo=sync_state_repo,
        usage_repo=usage_repo,
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
            on_index_changed=cache.invalidate_all,
        ),
        retriever=retriever,
        intent_classifier=intent_classifier,
        budget=budget,
        prompts=prompts,
        llm=llm,
        cache=cache,
        orchestrator=ChatOrchestrator(
            settings=settings,
            encoder=encoder,
            retriever=retriever,
            intent_classifier=intent_classifier,
            llm=llm,
            prompts=prompts,
            cache=cache,
            usage_repo=usage_repo,
        ),
        quiz=QuizGenerator(settings=settings, index=index, llm=llm, prompts=prompts),
    )


async def load_index_from_db(service: Service) -> None:
    """
    Dựng vector index TỪ SQLITE, không gọi nguồn.

    Đây là lý do restart không tốn một request nào tới backend: index đã nằm
    sẵn trong SQLite từ lần chạy trước.
    """

    started = time.perf_counter()

    rows = await service.card_repo.load_all()
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

        # Embed câu mẫu intent một lần, rồi giữ centroid suốt vòng đời.
        await service.intent_classifier.warmup()

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

        await service.llm.aclose()

        await anyio.to_thread.run_sync(service.db.close_sync)

        log.info("shutdown_complete")


def create_app(settings: Settings | None = None, encoder: Encoder | None = None) -> FastAPI:
    settings = settings or get_settings()

    configure_logging(settings.ai_log_level)

    app = FastAPI(
        title="fsoft-ai",
        summary="Service RAG cho nền tảng học từ vựng tiếng Anh",
        description=API_DESCRIPTION,
        version="0.1.0",
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
        swagger_ui_parameters={
            # Mặc định Swagger bung hết mọi endpoint, phải cuộn rất lâu mới thấy
            # cái cần tìm. "list" chỉ hiện tên endpoint, bấm mới mở.
            "docExpansion": "list",
            "defaultModelsExpandDepth": 2,
            "displayRequestDuration": True,
            "tryItOutEnabled": True,
            "persistAuthorization": True,
        },
    )
    app.state.settings = settings
    app.state.encoder = encoder

    register_exception_handlers(app)
    app.include_router(index_api.router)
    app.include_router(search_api.router)
    app.include_router(chat_api.router)
    app.include_router(quiz_api.router)
    app.include_router(stats_api.router)

    @app.get(
        "/healthz",
        tags=["Health"],
        summary="Tiến trình còn sống không",
        response_model=HealthzResponse,
        responses={
            200: {
                "description": (
                    "Luôn luôn 200 nếu tiến trình còn chạy — kể cả khi model hỏng, "
                    "backend Java chết, hay index rỗng."
                )
            }
        },
    )
    async def healthz() -> dict:
        """
        Dùng cho **liveness probe**.

        Cố ý không kiểm gì cả: không kiểm model, không kiểm nguồn dữ liệu, không
        chạm SQLite. Endpoint này chỉ trả lời đúng một câu — tiến trình còn sống
        hay đã treo.

        **Đừng dùng `/readyz` làm liveness probe.** Model mất khoảng 2,6 giây để
        nạp, và trong khoảng đó `/readyz` trả 503. Hạ tầng sẽ hiểu là service
        chết rồi giết đi, khởi động lại, lại nạp model, lại 503 — vòng lặp không
        bao giờ thoát.
        """

        return {"status": "ok"}

    @app.get(
        "/readyz",
        tags=["Health"],
        summary="Model nạp xong và index sẵn sàng chưa",
        response_model=ReadyzResponse,
        responses={
            200: {"description": "Sẵn sàng nhận request nghiệp vụ."},
            503: {
                "model": ReadyzResponse,
                "description": (
                    "Chưa sẵn sàng. Bình thường lúc mới khởi động (~2,6 giây). "
                    "Nếu kẹt ở đây mãi thì xem log `warmup_failed`."
                ),
            },
        },
    )
    async def readyz() -> JSONResponse:
        """
        Dùng cho **readiness probe**, và để biết khi nào bắt đầu gọi được
        `/internal/v1/*`.

        Ba cờ con cho biết đang kẹt ở đâu:

        - `encoder_ready=false` — model ONNX chưa nạp xong, hoặc nạp hỏng.
        - `index_ready=false` — chưa dựng xong chỉ mục trong RAM từ SQLite.

        Gọi endpoint nghiệp vụ trước khi cờ này lên `true` sẽ nhận
        `503 INDEX_NOT_READY`.
        """

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
