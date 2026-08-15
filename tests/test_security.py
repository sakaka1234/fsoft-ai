"""
Test ranh giới bảo mật và các endpoint HTTP. SPEC muc 8.

fsoft-ai không có public domain, nhưng X-Internal-Token vẫn là lớp phòng thủ
thứ hai và phải chặt.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.embedding.encoder import Encoder
from app.main import create_app

TOKEN = "test-token"
HEADERS = {"X-Internal-Token": TOKEN}


@pytest.fixture
def client(settings: Settings, encoder: Encoder):
    with TestClient(create_app(settings, encoder=encoder)) as test_client:
        yield test_client


@pytest.fixture
def client_backend_chet(settings: Settings, encoder: Encoder):
    """Nguồn HTTP trỏ vào cổng discard — kết nối bị từ chối ngay lập tức."""

    settings.ai_source_mode = "http"
    settings.ai_backend_url = "http://127.0.0.1:9/fsoft"

    with TestClient(create_app(settings, encoder=encoder)) as test_client:
        yield test_client


# ---------------------------------------------------------------------
# Xác thực
# ---------------------------------------------------------------------


def test_thieu_token_thi_401(client: TestClient) -> None:
    response = client.get("/internal/v1/index/status")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_sai_token_thi_401(client: TestClient) -> None:
    response = client.get("/internal/v1/index/status", headers={"X-Internal-Token": "token-bay-ba"})

    assert response.status_code == 401


def test_token_dung_tien_to_van_bi_tu_choi(client: TestClient) -> None:
    """compare_digest so toàn bộ chuỗi, tiền tố đúng không đủ."""

    response = client.get("/internal/v1/index/status", headers={"X-Internal-Token": "test-"})

    assert response.status_code == 401


def test_token_dung_thi_200(client: TestClient) -> None:
    assert client.get("/internal/v1/index/status", headers=HEADERS).status_code == 200


def test_loi_khong_bao_gio_lo_traceback(client: TestClient) -> None:
    body = client.get("/internal/v1/index/status").text

    assert "Traceback" not in body
    assert 'File "' not in body


# ---------------------------------------------------------------------
# Health probe
# ---------------------------------------------------------------------


def test_healthz_khong_can_token(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_200_khi_da_san_sang(client: TestClient) -> None:
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_readyz_503_khi_model_chua_nap(client: TestClient) -> None:
    """
    Acceptance SPEC muc 11.2: /readyz trả 503 khi model chưa nạp xong.

    Khởi động không chặn nên request tới được service trước lúc nó sẵn sàng —
    đó chính là lý do healthz và readyz phải là hai endpoint khác nhau.

    Ép cờ thay vì chờ model nạp thật: bài test phải tất định, không phụ thuộc
    vào việc 2,5 giây nạp model có kịp trôi qua hay không.
    """

    client.app.state.service.encoder_ready = False

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"ready": False, "encoder_ready": False, "index_ready": True}


def test_readyz_503_khi_index_chua_dung_xong(client: TestClient) -> None:
    client.app.state.service.index_ready = False

    assert client.get("/readyz").status_code == 503


def test_backend_chet_van_khoi_dong_duoc(client_backend_chet: TestClient) -> None:
    """
    Acceptance SPEC muc 11.2: AI_SOURCE_MODE=http trỏ URL chết -> service VẪN
    khởi động và /healthz vẫn 200. Đồng bộ hỏng chỉ làm index cũ đi.
    """

    assert client_backend_chet.get("/healthz").status_code == 200
    assert client_backend_chet.get("/readyz").status_code == 200


def test_backend_chet_thi_ghi_last_sync_error(client_backend_chet: TestClient) -> None:
    """Lỗi đồng bộ hiện ra ở index/status chứ không được nuốt im lặng."""

    result = client_backend_chet.post("/internal/v1/index/sync", headers=HEADERS).json()

    assert result["error"] is not None

    status = client_backend_chet.get("/internal/v1/index/status", headers=HEADERS).json()

    assert status["last_sync_error"] is not None
    assert status["backend_reachable"] is False
    assert status["card_count"] == 0


# ---------------------------------------------------------------------
# Endpoint index
# ---------------------------------------------------------------------


def test_status_tra_du_field_theo_spec_8_5(client: TestClient) -> None:
    body = client.get("/internal/v1/index/status", headers=HEADERS).json()

    assert set(body) == {
        "card_count",
        "index_size",
        "model_version",
        "last_sync_ts",
        "last_sync_at",
        "last_sync_duration_ms",
        "last_sync_embedded",
        "last_sync_skipped",
        "last_full_sweep_at",
        "last_sync_error",
        "backend_reachable",
        "source_mode",
    }
    assert body["model_version"] == "multilingual-e5-small@t1"
    assert body["source_mode"] == "fixture"


def test_ep_dong_bo_roi_doc_status(client: TestClient) -> None:
    synced = client.post("/internal/v1/index/sync", headers=HEADERS).json()

    assert synced["embedded"] == 24
    assert synced["error"] is None

    status = client.get("/internal/v1/index/status", headers=HEADERS).json()

    assert status["card_count"] == 24
    assert status["index_size"] == 24
    assert status["last_sync_error"] is None


def test_dong_bo_lan_hai_khong_embed_lai(client: TestClient) -> None:
    client.post("/internal/v1/index/sync", headers=HEADERS)

    second = client.post("/internal/v1/index/sync?full=true", headers=HEADERS).json()

    assert second == {
        "embedded": 0,
        "skipped": 24,
        "deleted": 0,
        "duration_ms": second["duration_ms"],
        "error": None,
    }


def test_timestamp_dung_hau_to_z(client: TestClient) -> None:
    """SPEC muc 8.5 công bố định dạng có Z; Instant.parse phía Java khó tính."""

    client.post("/internal/v1/index/sync", headers=HEADERS)

    body = client.get("/internal/v1/index/status", headers=HEADERS).json()

    assert body["last_sync_ts"].endswith("Z")
    assert body["last_sync_at"].endswith("Z")
    assert "+00:00" not in body["last_sync_ts"]


def test_quet_id_qua_endpoint_xoa_the_da_bi_go(client: TestClient, fixture_file: Path) -> None:
    from tests.conftest import remove_card

    client.post("/internal/v1/index/sync", headers=HEADERS)

    remove_card(fixture_file, 101)

    result = client.post("/internal/v1/index/sync?sweep=true", headers=HEADERS).json()

    assert result["deleted"] == 1
    assert client.get("/internal/v1/index/status", headers=HEADERS).json()["card_count"] == 23
