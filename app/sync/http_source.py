"""
Nguồn dữ liệu từ backend Java qua HTTP. SPEC muc 6.

Chỉ gọi đúng hai endpoint nội bộ, không bao giờ gọi endpoint nghiệp vụ nào khác.
"""

import asyncio
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger
from app.schemas.card import SourceCard
from app.sync.source import parse_source_card

log = get_logger(__name__)

IDS_PAGE_SIZE = 5000
MAX_ATTEMPTS = 3


class BackendUnauthorized(Exception):
    """Sai hoặc thiếu X-Internal-Token. Không retry — retry cũng vẫn 401."""


class HttpCardSource:
    def __init__(self, base_url: str, token: str, timeout_seconds: float) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-Internal-Token": token},
            timeout=timeout_seconds,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------
    # Gọi có retry
    # ---------------------------------------------------------------

    async def _get(self, path: str, params: dict) -> dict:
        """
        Backoff mũ tối đa 3 lần cho lỗi mạng và 5xx. SPEC muc 6.4.

        4xx thì raise ngay: 401 là sai token, 400 là bug của chính fsoft-ai —
        cả hai đều không tự khỏi khi thử lại.
        """

        last_error: Exception | None = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self._client.get(path, params=params)

                if response.status_code == 401:
                    raise BackendUnauthorized(f"Backend từ chối X-Internal-Token khi gọi {path}")

                if response.status_code == 400:
                    log.error(
                        "backend_bad_request",
                        path=path,
                        params=params,
                        body=response.text[:500],
                    )

                response.raise_for_status()

                return response.json()

            except (BackendUnauthorized, httpx.HTTPStatusError) as exc:
                if isinstance(exc, BackendUnauthorized):
                    raise

                status = exc.response.status_code

                if status < 500:
                    raise

                last_error = exc

            except httpx.HTTPError as exc:
                last_error = exc

            if attempt < MAX_ATTEMPTS - 1:
                delay = 2**attempt
                log.warning(
                    "backend_retry",
                    path=path,
                    attempt=attempt + 1,
                    delay_seconds=delay,
                    error=str(last_error),
                )
                await asyncio.sleep(delay)

        assert last_error is not None
        raise last_error

    # ---------------------------------------------------------------
    # CardSource
    # ---------------------------------------------------------------

    async def fetch_changed_since(
        self, since: datetime | None, page: int, size: int
    ) -> tuple[list[SourceCard], bool]:
        params: dict = {"page": page, "size": size}

        if since is not None:
            # Backend đòi UTC ISO-8601 có hậu tố Z.
            params["since"] = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        payload = await self._get("/internal/cards/changed-since", params)

        data = payload.get("data") or {}
        content = data.get("content") or []

        return [parse_source_card(item) for item in content], bool(data.get("last", True))

    async def fetch_all_ids(self) -> set[int]:
        ids: set[int] = set()
        page = 1

        while True:
            payload = await self._get("/internal/cards/ids", {"page": page, "size": IDS_PAGE_SIZE})

            data = payload.get("data") or {}

            # `content` là mảng số nguyên trần, không phải mảng object.
            ids.update(int(x) for x in (data.get("content") or []))

            if data.get("last", True):
                return ids

            page += 1

    async def health(self) -> bool:
        try:
            await self._get("/internal/cards/ids", {"page": 1, "size": 1})
            return True

        except Exception:  # noqa: BLE001 - health check, mọi lỗi đều là "không tới được"
            return False
