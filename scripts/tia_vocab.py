"""
Tỉa bảng embedding của XLM-R xuống còn tiếng Việt + tiếng Anh.

    uv run python scripts/tia_vocab.py --ngan-sach 32000 --ten tia32k

VÌ SAO. `intfloat/multilingual-e5-small` mang bộ từ vựng XLM-R 250.002 token cho
100 ngôn ngữ. Service này chỉ cần vi + en. Số đo cho thấy cái vocab đó ăn RAM ở
HAI chỗ, không phải một:

    tokenizers nạp tokenizer.json (Unigram 250k)   +250 MB   <-- chỗ tốn nhất
    ONNX Runtime nạp session (bảng uint8 96 MB)    +131 MB

Đo trên Xeon E5-2680, `scripts/do_moc.py`-style checkpoint. Con số +250 MB của
tokenizer trước đây bị gộp vào mốc "nạp model" nên tưởng là do model.

RAM của tokenizers tỷ lệ tuyến tính với số token (~1 KB/token, là cây trie
Unigram):

    vocab   5.001 ->  +4,3 MB
    vocab  32.001 -> +22,4 MB
    vocab 250.002 -> +250,2 MB

VÌ SAO TỈA ĐƯỢC MÀ KHÔNG CẦN TORCH, VÀ KHÔNG MẤT CHẤT LƯỢNG. Bản lượng tử dùng
scale/zero_point VÔ HƯỚNG cho cả bảng (per-tensor, không per-row):

    embeddings.word_embeddings.weight_scale       float32 ()   0.01054688
    embeddings.word_embeddings.weight_zero_point  uint8   ()   128
    embeddings.word_embeddings.weight_quantized   uint8   (250037, 384)

và graph là `Gather(bảng_uint8, input_ids)` RỒI MỚI `DequantizeLinear`. Nên tỉa
hàng chỉ là phép chọn hàng trên mảng uint8: token nào được giữ thì vector của nó
GIỐNG TỪNG BIT sau khi tỉa. Không cần torch, không cần dequantize, không cần
huấn luyện lại. `numpy` + `onnx` (chỉ lúc offline) là đủ.

BẪY LỚN — TỪ NGOÀI TẬP. Tokenizer này KHÔNG có `byte_fallback`. Cắt bừa thì chữ
lạ thành `<unk>` và retrieval hỏng với thẻ thật của người dùng. Chốt chặn ở đây
là TẦNG 2 bên dưới: giữ TOÀN BỘ piece đơn ký tự thuộc bảng chữ Latin/Việt/IPA
(838 piece, ~0,3 MB bảng). Nhờ vậy từ lạ tệ nhất cũng rã thành từng ký tự chứ
KHÔNG BAO GIỜ thành `<unk>`. Chạy với `--kiem-tra` để thấy tỷ lệ unk thực đo.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Script in tiếng Việt có dấu. Trên Windows, stdout chuyển hướng ra file hoặc
# pipe dùng bảng mã cp1252 và `print` ném UnicodeEncodeError — đúng lúc người ta
# cần lưu lại kết quả nhất.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODEL_GOC = "intfloat/multilingual-e5-small"
FILE_GOC = "onnx/model_qint8_avx512_vnni.onnx"
TEN_BANG = "embeddings.word_embeddings.weight_quantized"

# Ký tự được coi là "thuộc vi + en". Khoảng chọn theo đúng thứ dữ liệu thật có:
#   ascii            chữ Anh, số, dấu câu
#   latin1 / latinA  À-ÿ, Đđ (0110/0111), ă (0103), ĩ ũ
#   latinB           ơ ư Ơ Ư (01A0-01B0)
#   ipa + modifier   phiên âm /rɪˈzɪliənt/ — trường `phonetic` CÓ vào text embed
#   combining        dấu thanh rời, phòng khi normalizer nhả dạng tổ hợp
#   latinExtAdd      ạ ả ấ ầ ậ ắ ẹ ẽ ế ệ ị ọ ố ộ ớ ợ ụ ứ ự ỳ ỹ ... (1E00-1EFF)
#   punct            – — ' ' " " … (text_builder dùng "—" nối ví dụ)
#   currency         ₫ (20AB)
_KHOANG_CHO_PHEP = [
    (0x20, 0x7E),
    (0xA0, 0xFF),
    (0x100, 0x17F),
    (0x180, 0x24F),
    (0x250, 0x2AF),
    (0x2B0, 0x2FF),
    (0x300, 0x36F),
    (0x1E00, 0x1EFF),
    (0x2000, 0x206F),
    (0x20A0, 0x20BF),
]
CHO_PHEP: set[str] = {chr(c) for lo, hi in _KHOANG_CHO_PHEP for c in range(lo, hi + 1)}
CHO_PHEP.add("▁")  # ▁ metaspace, pre_tokenizer nào cũng sinh ra

# BỐN CHỮ HY LẠP MÀ IPA VAY MƯỢN. Đây là một cái bẫy đã thực sự sập: token duy
# nhất trong toàn bộ ngữ liệu thật KHÔNG lọt qua bộ lọc ký tự ở trên là `θ`
# (U+03B8), đến từ phiên âm /ˌfəʊtəʊˈsɪnθəsɪs/. Nó chỉ sống sót nhờ TẦNG 3 (ngữ
# liệu thật) tình cờ có một thẻ chứa nó.
#
# Không có bốn ký tự này thì thẻ tương lai nào có `θ` trong phiên âm sẽ ra `<unk>`
# — im lặng, và chỉ hỏng với đúng những thẻ ta không có trong sàn để mà thấy.
# KHÔNG mở cả khối Hy Lạp (0370-03FF): làm vậy sẽ lôi toàn bộ piece tiếng Hy Lạp
# vào tập "sạch" và phình vô ích. Chỉ đúng bốn chữ IPA dùng.
CHO_PHEP |= set("θβγχ")

# Ký tự CHỈ tiếng Việt có. Piece nào chứa nó gần như chắc chắn là tiếng Việt ->
# giữ hết, không tính vào ngân sách xếp hạng.
DAU_VIET: set[str] = (
    set("ăâđêôơưĂÂĐÊÔƠƯ")
    | {chr(c) for c in range(0x1EA0, 0x1EFA)}
    | set("áàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
)


def sach(piece: str) -> bool:
    """Piece chỉ gồm ký tự trong tập cho phép."""

    return bool(piece) and all(c in CHO_PHEP for c in piece)


def than(piece: str) -> str:
    """Bỏ tiền tố metaspace để đếm độ dài thật."""

    return piece[1:] if piece.startswith("▁") and len(piece) > 1 else piece


# ---------------------------------------------------------------------
# Sàn dữ liệu thật
# ---------------------------------------------------------------------


def gom_ngu_lieu() -> list[str]:
    """
    Toàn bộ text mà service THẬT SỰ đưa qua tokenizer.

    Ba nguồn, đúng ba nguồn đang có trong repo. Cộng thêm prefix vì
    `query: ` / `passage: ` cũng đi qua tokenizer.
    """

    from app.embedding.text_builder import build_text
    from app.retrieval.intent_examples import INTENT_EXAMPLES
    from app.schemas.card import SourceCard

    texts: list[str] = []

    # 1. Thẻ từ vựng — đúng text mà encoder embed, dựng qua build_text.
    cards = json.loads((PROJECT_ROOT / "tests" / "fixtures" / "cards.json").read_text("utf-8"))
    for c in cards:
        card = SourceCard(
            card_id=c["cardId"],
            deck_id=c["deckId"],
            deck_title=c.get("deckTitle"),
            word=c["word"],
            phonetic=c.get("phonetic"),
            part_of_speech=c.get("partOfSpeech"),
            meaning=c["meaning"],
            definition_en=c.get("definitionEn"),
            example_sentence=c.get("exampleSentence"),
            example_meaning=c.get("exampleMeaning"),
            note=c.get("note"),
            source_updated_at=c["updatedAt"],
        )
        texts.append("passage: " + build_text(card))
        if card.deck_title:
            texts.append(card.deck_title)

    # 2. 40 case của bộ đo.
    golden = json.loads(
        (PROJECT_ROOT / "tests" / "eval" / "retrieval_golden.json").read_text("utf-8")
    )
    texts += ["query: " + c["query"] for c in golden]

    # 3. Câu mẫu intent — warmup embed hết, nên chúng là dữ liệu nóng.
    for mau in INTENT_EXAMPLES.values():
        texts += ["query: " + s for s in mau]

    return texts


# Từ CỐ Ý không nằm trong sàn dữ liệu — dùng để đo tỷ lệ unk của thẻ tương lai.
HOLDOUT = [
    "passage: ubiquitous (adj) /juːˈbɪkwɪtəs/\nNghĩa: phổ biến khắp mọi nơi",
    "passage: quintessential (adj)\nNghĩa: tinh tuý, điển hình nhất",
    "query: từ nào nói về việc thoả thuận lại hợp đồng thuê nhà",
    "query: giải thích giúp mình từ serendipity với ạ",
    "query: cách phát âm từ entrepreneurship chuẩn nhất",
    "passage: khuếch trương (v)\nNghĩa: mở rộng quy mô kinh doanh",
    "query: nghĩa của cụm từ due diligence trong tài chính",
    "passage: photosynthesis\nNghĩa: quá trình quang hợp của thực vật",
    "query: xin lỗi cho mình hỏi từ obfuscate nghĩa là gì nhỉ",
    "passage: bươn chải, tất bật, chật vật mưu sinh giữa phố phường",
]


# ---------------------------------------------------------------------
# Chọn tập giữ
# ---------------------------------------------------------------------


def chon_tap_giu(
    vocab: list[list[Any]], ngan_sach: int, texts: list[str], tok: Any
) -> tuple[list[int], dict[str, int]]:
    """
    Bốn tầng, tầng sau chỉ lấp phần ngân sách còn lại.

    Thứ tự KHÔNG được đổi: tầng 1 và 2 là chốt chặn đúng đắn, tầng 3-4 là tối ưu.
    """

    n = len(vocab)
    giu: set[int] = set()
    thong_ke: dict[str, int] = {}

    # Tầng 1 — token đặc biệt. `unk_id=3` và `post_processor` ghim id 0/2, nên
    # 0..3 PHẢI giữ nguyên chỗ. Sắp xếp tăng dần ở cuối lo việc đó.
    giu |= {0, 1, 2, 3, n - 1}  # n-1 là <mask>
    thong_ke["1_dac_biet"] = len(giu)

    # Tầng 2 — CHỐT CHẶN: mọi piece đơn ký tự thuộc bảng chữ ta cần. Đây là thứ
    # khiến từ ngoài tập rã thành ký tự chứ không thành <unk>.
    truoc = len(giu)
    for i, (piece, _s) in enumerate(vocab):
        if len(than(piece)) == 1 and sach(piece):
            giu.add(i)
    thong_ke["2_don_ky_tu"] = len(giu) - truoc

    # Tầng 3 — token do NGỮ LIỆU THẬT sinh ra. Giữ hết, bất kể ngân sách: đây là
    # thứ quyết định 40 case và 24 thẻ chạy đúng.
    truoc = len(giu)
    for enc in tok.encode_batch(texts):
        giu.update(enc.ids)
    thong_ke["3_ngu_lieu"] = len(giu) - truoc

    # Tầng 4a — mọi piece mang dấu tiếng Việt. Bảng chữ Việt hữu hạn, và không
    # có ngôn ngữ nào khác trong XLM-R dùng ơ/ư/ạ/ệ, nên đây là "toàn bộ tiếng
    # Việt của vocab" với giá rất rẻ.
    truoc = len(giu)
    for i, (piece, _s) in enumerate(vocab):
        if sach(piece) and any(c in DAU_VIET for c in piece):
            giu.add(i)
    thong_ke["4a_dau_viet"] = len(giu) - truoc

    # Tầng 4b — phổ thông: piece sạch còn lại, xếp theo score Unigram (log-prob,
    # tức tần suất) giảm dần, lấy cho tới hết ngân sách. Đây là phần tiếng Anh
    # và số/dấu câu thông dụng.
    truoc = len(giu)
    con_lai = [(s, i) for i, (p, s) in enumerate(vocab) if i not in giu and sach(p)]
    con_lai.sort(reverse=True)
    for _s, i in con_lai:
        if len(giu) >= ngan_sach:
            break
        giu.add(i)
    thong_ke["4b_pho_thong"] = len(giu) - truoc

    thong_ke["tong"] = len(giu)

    return sorted(giu), thong_ke


# ---------------------------------------------------------------------
# Dựng biến thể
# ---------------------------------------------------------------------


def dung(snap_goc: Path, dich: Path, ten: str, ngan_sach: int, kiem_tra: bool) -> dict[str, Any]:
    import onnx
    from onnx import numpy_helper
    from tokenizers import Tokenizer

    goc_onnx = snap_goc / "onnx"

    tok_goc_json = json.loads((goc_onnx / "tokenizer.json").read_text("utf-8"))
    vocab: list[list[Any]] = tok_goc_json["model"]["vocab"]
    tok_goc = Tokenizer.from_file(str(goc_onnx / "tokenizer.json"))

    texts = gom_ngu_lieu()
    print(f"ngữ liệu thật: {len(texts)} đoạn text")

    giu, thong_ke = chon_tap_giu(vocab, ngan_sach, texts, tok_goc)
    print("tập giữ theo tầng:", thong_ke)

    # cũ -> mới. `giu` đã sắp tăng dần nên 0..3 vẫn là 0..3.
    anh_xa = {cu: moi for moi, cu in enumerate(giu)}
    assert [anh_xa[i] for i in (0, 1, 2, 3)] == [0, 1, 2, 3], "token đặc biệt bị dịch chỗ"

    # ---- tokenizer.json mới ----
    moi = dict(tok_goc_json)
    moi["model"] = dict(tok_goc_json["model"])
    moi["model"]["vocab"] = [vocab[i] for i in giu]
    moi["model"]["unk_id"] = anh_xa[3]
    moi["added_tokens"] = [
        {**at, "id": anh_xa[at["id"]]} for at in tok_goc_json["added_tokens"] if at["id"] in anh_xa
    ]

    dich_onnx = dich / "onnx"
    dich_onnx.mkdir(parents=True, exist_ok=True)

    cfg = json.loads((goc_onnx / "config.json").read_text("utf-8"))
    cfg["vocab_size"] = len(giu)

    # HAI BẢN, cả gốc snapshot lẫn onnx/. fastembed đọc tokenizer.json ở GỐC
    # snapshot (`load_tokenizer(model_dir=specific_model_path)`), còn
    # `_ADDITIONAL_FILES` của encoder khai báo chúng dưới `onnx/` cho nhánh tải
    # từ Hugging Face. Snapshot gốc của HF cũng có ở cả hai chỗ — giữ y hệt để
    # không phải sửa dòng nào trong app/.
    for thu_muc in (dich, dich_onnx):
        (thu_muc / "tokenizer.json").write_text(
            json.dumps(moi, ensure_ascii=False), encoding="utf-8"
        )
        (thu_muc / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        for phu in ("tokenizer_config.json", "special_tokens_map.json"):
            shutil.copy2(goc_onnx / phu, thu_muc / phu)

    # sentencepiece.bpe.model là bản gốc 250k, KHÔNG khớp vocab đã tỉa nữa.
    # fastembed không đọc nó (nó dùng tokenizer.json), nên cố tình KHÔNG copy:
    # để lại một file lệch pha ở đây chỉ chờ ai đó nạp bằng transformers rồi
    # nhận id sai mà không có lỗi nào báo.

    # ---- model onnx mới: chọn hàng trên mảng uint8, KHÔNG dequantize ----
    # FILE_GOC đã mang tiền tố "onnx/" nên nối vào SNAPSHOT, không nối vào goc_onnx.
    m = onnx.load(str(snap_goc / FILE_GOC))
    bang = None
    for init in m.graph.initializer:
        if init.name == TEN_BANG:
            bang = init
            break
    if bang is None:
        raise SystemExit(f"không tìm thấy initializer {TEN_BANG}")

    cu = numpy_helper.to_array(bang)
    assert cu.dtype == np.uint8, f"bảng không phải uint8 mà là {cu.dtype}"
    bang_moi = np.ascontiguousarray(cu[np.asarray(giu, dtype=np.int64)])

    init_moi = numpy_helper.from_array(bang_moi, TEN_BANG)
    bang.CopyFrom(init_moi)

    # Tên file mang luôn tên biến thể. `find_local_snapshot` chọn snapshot theo
    # việc CÓ ĐÚNG file này, nên hai biến thể trùng tên file sẽ tranh nhau và
    # cái nào mới hơn theo mtime thì thắng — im lặng và rất khó lần.
    ra_file = dich_onnx / f"model_{ten}.onnx"
    onnx.save(m, str(ra_file))

    kq: dict[str, Any] = {
        "model_file": f"onnx/model_{ten}.onnx",
        "vocab_cu": len(vocab),
        "vocab_moi": len(giu),
        "bang_cu_MB": round(cu.nbytes / 1e6, 1),
        "bang_moi_MB": round(bang_moi.nbytes / 1e6, 1),
        "file_cu_MB": round((snap_goc / FILE_GOC).stat().st_size / 1e6, 1),
        "file_moi_MB": round(ra_file.stat().st_size / 1e6, 1),
        "tokenizer_cu_MB": round((goc_onnx / "tokenizer.json").stat().st_size / 1e6, 1),
        "tokenizer_moi_MB": round((dich / "tokenizer.json").stat().st_size / 1e6, 1),
        "tang": thong_ke,
    }

    if kiem_tra:
        kq.update(_kiem_tra(tok_goc, dich / "tokenizer.json", anh_xa, texts))

    return kq


def _kiem_tra(
    tok_goc: Any, duong_moi: Path, anh_xa: dict[int, int], texts: list[str]
) -> dict[str, Any]:
    """
    Hai câu hỏi, hai số đo.

    1. Trên ngữ liệu thật, chuỗi token có GIỐNG HỆT sau khi ánh xạ lại không?
       Giống hệt + scale vô hướng => vector giống từng bit => chất lượng KHÔNG
       ĐỔI, không cần tin vào lập luận nào nữa.
    2. Trên holdout (từ cố ý không có trong sàn), tỷ lệ <unk> là bao nhiêu?
    """

    from tokenizers import Tokenizer

    tok_moi = Tokenizer.from_file(str(duong_moi))
    unk_moi = tok_moi.token_to_id("<unk>")

    lech = 0
    for t, e_cu in zip(texts, tok_goc.encode_batch(texts)):
        mong_doi = [anh_xa[i] for i in e_cu.ids]
        if tok_moi.encode(t).ids != mong_doi:
            lech += 1

    def do_unk(ds: list[str]) -> tuple[float, float]:
        tong = unk = 0
        phinh = []
        for t in ds:
            a = tok_goc.encode(t).ids
            b = tok_moi.encode(t).ids
            tong += len(b)
            unk += sum(1 for i in b if i == unk_moi)
            phinh.append(len(b) / max(len(a), 1))
        return unk / max(tong, 1), sum(phinh) / len(phinh)

    unk_san, phinh_san = do_unk(texts)
    unk_hold, phinh_hold = do_unk(HOLDOUT)

    return {
        "case_lech_chuoi_token": lech,
        "unk_ty_le_ngu_lieu": round(unk_san, 5),
        "unk_ty_le_holdout": round(unk_hold, 5),
        "phinh_token_ngu_lieu": round(phinh_san, 3),
        "phinh_token_holdout": round(phinh_hold, 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ngan-sach", type=int, default=32000)
    ap.add_argument("--ten", default="tia32k")
    ap.add_argument("--kiem-tra", action="store_true", default=True)
    args = ap.parse_args()

    from app.embedding.encoder import find_local_snapshot

    cache = Path(
        os.environ.get("FASTEMBED_CACHE_PATH", str(PROJECT_ROOT / ".cache" / "fastembed"))
    ).resolve()
    snap = find_local_snapshot(cache, MODEL_GOC, FILE_GOC)
    if snap is None:
        raise SystemExit(f"không thấy snapshot gốc trong {cache}")

    # Đặt biến thể thành MỘT SNAPSHOT MỚI cạnh snapshot gốc. `find_local_snapshot`
    # chỉ nhận snapshot CHỨA ĐÚNG model_file, nên đặt `model_tia.onnx` ở đây là
    # đủ để chọn — không phải sửa dòng code nào trong app/.
    dich = snap.parent / args.ten
    if dich.exists():
        shutil.rmtree(dich)

    kq = dung(snap, dich, args.ten, args.ngan_sach, args.kiem_tra)

    print()
    print(f"snapshot mới: {dich}")
    print(json.dumps(kq, ensure_ascii=False, indent=2))
    print()
    print("Đo RSS + chất lượng (ngưỡng PHẢI hiệu chỉnh lại, xem hieu_chinh_nguong.py):")
    print(f"  AI_EMBEDDING_MODEL_FILE={kq['model_file']} \\")
    print(f"    AI_MODEL_VERSION=e5-small-q8-{args.ten}@t1 \\")
    print(f"    AI_DB_PATH=./data/{args.ten}.db uv run python scripts/run_eval.py")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
