"""
Client LLM. SPEC muc 5.8 và muc 11.4.

Dùng SDK `openai` trỏ vào Groq qua `base_url` — Groq tương thích chuẩn OpenAI,
nên đổi nhà cung cấp chỉ là đổi biến môi trường.

BỐN LỚP xử lý rate limit, theo đúng thứ tự:
  1. Đọc header x-ratelimit-* mỗi lần gọi, đồng bộ vào TokenBudget
  2. Gặp 429 -> đọc `retry-after`, đợi ĐÚNG số giây đó, thử lại
  3. Vẫn 429 -> hạ cấp sang model dự phòng (có hạn mức TPM riêng)
  4. Vẫn hỏng -> 503 PROVIDER_UNAVAILABLE, không để traceback lọt ra ngoài
"""

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import openai
from openai import AsyncOpenAI

from app.config import Settings
from app.core.errors import ProviderUnavailable
from app.core.logging import get_logger
from app.llm.budget import TokenBudget, estimate_tokens
from app.store.usage_repo import UsageRepo

log = get_logger(__name__)

PROVIDER = "groq"

# Trần chờ khi nhà cung cấp trả retry-after vô lý. Chờ lâu hơn thế thì request
# đã hết hạn với người dùng rồi, thà báo lỗi sớm.
MAX_RETRY_AFTER_SECONDS = 20.0


@dataclass(slots=True)
class LlmResult:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


@dataclass(slots=True)
class StreamChunk:
    """Một mẩu token, hoặc mẩu cuối mang theo số liệu sử dụng."""

    text: str
    done: bool = False
    usage: LlmResult | None = None


def parse_retry_after(headers, default: float) -> float:
    raw = headers.get("retry-after") if headers else None

    if raw is None:
        return default

    try:
        return min(float(raw), MAX_RETRY_AFTER_SECONDS)

    except (TypeError, ValueError):
        return default


def parse_remaining_tokens(headers) -> int | None:
    raw = headers.get("x-ratelimit-remaining-tokens") if headers else None

    try:
        return int(raw) if raw is not None else None

    except (TypeError, ValueError):
        return None


class LlmClient:
    def __init__(
        self,
        settings: Settings,
        budget: TokenBudget,
        usage_repo: UsageRepo,
        http_client=None,
    ) -> None:
        """
        `http_client` để test tiêm transport giả.

        LƯU Ý: `openai` 3.x dùng **httpx2**, không phải `httpx`. Vì vậy `respx`
        KHÔNG chặn được lời gọi của SDK này — nó chỉ vá `httpx`. Test phải
        dùng `httpx2.MockTransport`. (respx vẫn dùng bình thường cho
        `app/sync/http_source.py` vì chỗ đó gọi `httpx` trực tiếp.)
        """

        self._settings = settings
        self._budget = budget
        self._usage = usage_repo

        self._client = AsyncOpenAI(
            api_key=settings.ai_llm_api_key or "missing",
            base_url=settings.ai_llm_base_url,
            # BẮT BUỘC. Retry mặc định của SDK dùng backoff mù, không đọc header
            # retry-after của Groq, nên nó thử lại quá sớm và ăn thêm 429.
            max_retries=0,
            timeout=settings.ai_llm_timeout_seconds,
            http_client=http_client,
        )

    async def aclose(self) -> None:
        await self._client.close()

    # ---------------------------------------------------------------

    async def complete(
        self,
        *,
        task: str,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
        intent: str | None = None,
        answer_source: str | None = None,
    ) -> LlmResult:
        primary = model or self._settings.ai_model_chat
        fallback = self._settings.ai_model_fallback

        estimated = estimate_tokens(system) + estimate_tokens(user)
        estimated += max_tokens or self._settings.ai_max_output_tokens

        # Ném BudgetExhausted -> 429 ngay, chưa tốn một request nào.
        self._budget.reserve(estimated)

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        attempt_models = [primary]

        if fallback and fallback != primary:
            attempt_models.append(fallback)

        last_error: Exception | None = None

        for candidate in attempt_models:
            try:
                return await self._call_with_retry(
                    task=task,
                    model=candidate,
                    messages=messages,
                    max_tokens=max_tokens or self._settings.ai_max_output_tokens,
                    temperature=(
                        temperature if temperature is not None else self._settings.ai_temperature
                    ),
                    json_mode=json_mode,
                    estimated=estimated,
                    intent=intent,
                    answer_source=answer_source,
                )

            except openai.RateLimitError as exc:
                last_error = exc

                log.warning("llm_downgrade", from_model=candidate, to_model=fallback)

            except openai.APIError as exc:
                last_error = exc

                log.warning("llm_provider_error", model=candidate, error=str(exc))

        log.error("llm_unavailable", error=str(last_error))

        raise ProviderUnavailable(
            "Trợ lý AI đang quá tải, vui lòng thử lại sau ít phút."
        ) from last_error

    # ---------------------------------------------------------------

    async def _call_with_retry(
        self,
        *,
        task: str,
        model: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
        estimated: int,
        intent: str | None,
        answer_source: str | None,
    ) -> LlmResult:
        attempts = self._settings.ai_llm_max_retries + 1

        for attempt in range(attempts):
            started = time.perf_counter()

            try:
                kwargs: dict = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }

                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}

                raw = await self._client.chat.completions.with_raw_response.create(**kwargs)

                completion = raw.parse()
                latency_ms = int((time.perf_counter() - started) * 1000)

                usage = completion.usage
                prompt_tokens = usage.prompt_tokens if usage else 0
                completion_tokens = usage.completion_tokens if usage else 0

                # THỨ TỰ QUAN TRỌNG: settle trước, sync sau.
                #
                # settle thay ước lượng bằng số thật của riêng lời gọi này.
                # sync lấy con số của nhà cung cấp làm chuẩn cho cả cửa sổ phút.
                # Làm ngược lại thì settle sẽ trừ ước lượng ra khỏi con số đã
                # chuẩn của nhà cung cấp, tức là trừ nhầm hai lần.
                self._budget.settle(estimated, prompt_tokens + completion_tokens)
                self._budget.sync_from_provider(parse_remaining_tokens(raw.headers))

                await self._log_usage(
                    task=task,
                    model=model,
                    latency_ms=latency_ms,
                    success=True,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    intent=intent,
                    answer_source=answer_source,
                )

                return LlmResult(
                    text=(completion.choices[0].message.content or "").strip(),
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=latency_ms,
                )

            except openai.RateLimitError as exc:
                await self._log_usage(
                    task=task,
                    model=model,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    success=False,
                    intent=intent,
                    answer_source=answer_source,
                    error_code="RATE_LIMIT",
                )

                if attempt == attempts - 1:
                    raise

                # Đợi ĐÚNG số giây nhà cung cấp bảo. Backoff mù thử lại quá
                # sớm và chỉ ăn thêm một cái 429 nữa.
                delay = parse_retry_after(
                    getattr(exc, "response", None) and exc.response.headers, 1.0
                )

                log.warning("llm_rate_limited", model=model, retry_after=delay, attempt=attempt + 1)

                await asyncio.sleep(delay)

            except openai.APIError as exc:
                await self._log_usage(
                    task=task,
                    model=model,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    success=False,
                    intent=intent,
                    answer_source=answer_source,
                    error_code=type(exc).__name__,
                )

                raise

        raise ProviderUnavailable("Hết số lần thử lại.")

    # ---------------------------------------------------------------
    # Streaming
    # ---------------------------------------------------------------

    async def stream(
        self,
        *,
        task: str,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        intent: str | None = None,
        answer_source: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """
        Phát từng token một. SPEC muc 8.2.

        Khác `complete()` ở chỗ KHÔNG hạ cấp model và KHÔNG thử lại: một khi đã
        bắt đầu phát token cho người dùng thì không thể quay lại từ đầu. Hỏng
        giữa chừng thì phát sự kiện `error`.
        """

        chosen = model or self._settings.ai_model_chat

        estimated = estimate_tokens(system) + estimate_tokens(user)
        estimated += max_tokens or self._settings.ai_max_output_tokens

        self._budget.reserve(estimated)

        started = time.perf_counter()
        prompt_tokens = 0
        completion_tokens = 0
        text_len = 0

        try:
            stream = await self._client.chat.completions.create(
                model=chosen,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens or self._settings.ai_max_output_tokens,
                temperature=(
                    temperature if temperature is not None else self._settings.ai_temperature
                ),
                stream=True,
                stream_options={"include_usage": True},
            )

            async for event in stream:
                if event.usage:
                    prompt_tokens = event.usage.prompt_tokens
                    completion_tokens = event.usage.completion_tokens

                if not event.choices:
                    continue

                piece = event.choices[0].delta.content

                if piece:
                    text_len += len(piece)
                    yield StreamChunk(text=piece)

        except openai.APIError as exc:
            await self._log_usage(
                task=task,
                model=chosen,
                latency_ms=int((time.perf_counter() - started) * 1000),
                success=False,
                intent=intent,
                answer_source=answer_source,
                error_code=type(exc).__name__,
            )

            log.warning("llm_stream_failed", model=chosen, error=str(exc))

            raise ProviderUnavailable(
                "Trợ lý AI đang quá tải, vui lòng thử lại sau ít phút."
            ) from exc

        finally:
            # Chạy cả khi client ngắt kết nối giữa chừng (GeneratorExit) — token
            # đã tiêu rồi thì phải ghi nhận, nếu không ngân sách sẽ lệch dần.
            actual = prompt_tokens + completion_tokens

            if actual == 0:
                # Groq đôi khi không gửi usage khi stream bị cắt.
                actual = estimate_tokens(system) + estimate_tokens(user) + text_len // 3

            self._budget.settle(estimated, actual)

        await self._log_usage(
            task=task,
            model=chosen,
            latency_ms=int((time.perf_counter() - started) * 1000),
            success=True,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            intent=intent,
            answer_source=answer_source,
        )

        yield StreamChunk(
            text="",
            done=True,
            usage=LlmResult(
                text="",
                model=chosen,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=int((time.perf_counter() - started) * 1000),
            ),
        )

    async def _log_usage(
        self,
        *,
        task: str,
        model: str,
        latency_ms: int,
        success: bool,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        intent: str | None = None,
        answer_source: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """
        Ghi usage_log cho MỌI lời gọi, kể cả lời gọi lỗi.

        Bỏ qua lời gọi lỗi là cách chắc chắn nhất để không bao giờ biết tỷ lệ
        429 thật của mình — mà đó là chỉ số cảnh báo sớm quan trọng nhất
        (SPEC muc 11.8).
        """

        try:
            await self._usage.insert(
                task=task,
                provider=PROVIDER,
                model=model,
                latency_ms=latency_ms,
                success=success,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                intent=intent,
                answer_source=answer_source,
                error_code=error_code,
            )

        except Exception:
            # Ghi nhật ký hỏng thì tiếc, nhưng không được phép làm hỏng câu
            # trả lời đã lấy được cho người dùng.
            log.exception("usage_log_failed", task=task, model=model)
