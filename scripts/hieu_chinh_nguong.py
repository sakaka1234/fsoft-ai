"""
Hiệu chỉnh lại `AI_MIN_SCORE` cho biến thể ONNX đang cấu hình.

    AI_EMBEDDING_MODEL_FILE=onnx/model_qint8_avx512_vnni.onnx \
        uv run python scripts/hieu_chinh_nguong.py

Vì sao cần: lượng tử hoá làm DỊCH cả phân bố cosine, nên ngưỡng hiệu chỉnh cho
bản fp32 không còn tách được nữa. Đo thật — bản int8 dùng ngưỡng 0.83 của fp32
thì 2 trong 5 case NEGATIVE hỏng, mà không có lỗi nào báo.

Cách làm giống hệt lúc hiệu chỉnh ở M2: lấy điểm semantic THÔ (trước cổng lọc),
so case dương thấp điểm nhất với case âm cao điểm nhất.
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import Settings
from app.embedding.encoder import Encoder
from app.main import build_service, load_index_from_db
from tests.eval.metrics import load_golden


async def main() -> int:
    settings = Settings(ai_source_mode="fixture", ai_sync_enabled=False)

    enc = Encoder(settings)
    enc.load_sync()

    service = build_service(settings, encoder=enc)
    service.db.connect_sync()

    try:
        await service.intent_classifier.warmup()
        await service.syncer.run_incremental()
        await load_index_from_db(service)

        duong: list[tuple[str, float]] = []
        am: list[tuple[str, float]] = []

        for case in load_golden(PROJECT_ROOT / "tests" / "eval" / "retrieval_golden.json"):
            vector = await service.encoder.embed_query(case["query"])

            # Điểm THÔ của tầng semantic, lấy TRƯỚC cổng lọc liên quan — chính
            # cái mà ngưỡng sẽ được so với.
            tho = service.index.semantic_search(vector, case["allowed_deck_ids"], 5)

            if case["category"] == "NEGATIVE":
                am.append((case["id"], max((s for _, s in tho), default=0.0)))
                continue

            mong_doi = set(case["expected_card_ids"])
            diem_dung = [s for cid, s in tho if cid in mong_doi]

            if diem_dung:
                duong.append((case["id"], max(diem_dung)))

        duong.sort(key=lambda x: x[1])
        am.sort(key=lambda x: -x[1])

        print(f"Biến thể   : {settings.ai_embedding_model_file}")
        print(f"Ngưỡng hiện: {settings.ai_min_score}\n")

        print(f"DƯƠNG ({len(duong)} case) — 5 case thấp điểm nhất:")
        for cid, s in duong[:5]:
            print(f"   {cid}  {s:.4f}")

        print(f"\nÂM ({len(am)} case) — cao xuống thấp:")
        for cid, s in am:
            print(f"   {cid}  {s:.4f}")

        thap_duong = duong[0][1] if duong else 0.0
        cao_am = am[0][1] if am else 0.0

        print(f"\nDương thấp nhất : {thap_duong:.4f}")
        print(f"Âm cao nhất     : {cao_am:.4f}")

        if thap_duong > cao_am:
            de_xuat = round((thap_duong + cao_am) / 2, 4)
            print(f"\nHai phân bố TÁCH RỜI. Ngưỡng đề xuất: {de_xuat}")

        else:
            # Chồng lấn là chuyện bình thường với E5 — điểm bị nén vào dải hẹp
            # 0.80-0.95. Ưu tiên loại sạch case âm: một câu trả lời bừa tệ hơn
            # một câu "chưa có trong bộ thẻ".
            de_xuat = round(cao_am + 0.0005, 4)
            mat = [c for c, s in duong if s < de_xuat]

            print("\nHai phân bố CHỒNG LẤN — không ngưỡng nào tách sạch được.")
            print(f"Đặt ngay trên âm cao nhất: {de_xuat}")
            print(
                f"   -> loại hết {len(am)} case âm, đổi lấy việc MẤT {len(mat)} case dương: {mat}"
            )

        print(f"\nCập nhật cả ba chỗ cho khớp {settings.ai_embedding_model_file}:")
        print(f"   .env / .env.example : AI_MIN_SCORE={de_xuat}")
        print(f"   app/config.py       : ai_min_score = {de_xuat}")
        print(f"   encoder.py          : MIN_SCORE_THEO_MODEL[...] = {de_xuat}")

        return 0

    finally:
        service.db.close_sync()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
