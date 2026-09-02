"""Ba endpoint tu vung: /vocab/extract (8.5b), /vocab/generate (8.5c), /vocab/lookup (8.5d)."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request

from app.api.deps import require_internal_token
from app.core.errors import IndexNotReady
from app.schemas.errors import BUDGET, NOT_READY, UNAUTHORIZED, ErrorResponse
from app.schemas.vocab import (
    VI_DU_VOCAB_EXTRACT,
    VI_DU_VOCAB_GENERATE,
    VI_DU_VOCAB_LOOKUP,
    VocabExtractRequest,
    VocabExtractResponse,
    VocabGenerateRequest,
    VocabGenerateResponse,
    VocabLookupRequest,
    VocabLookupResponse,
)

router = APIRouter(
    prefix="/internal/v1",
    tags=["Internal - Từ vựng"],
    dependencies=[Depends(require_internal_token)],
)

# Dùng lại NOT_READY và BUDGET của bộ chung — ngược với quiz.
#
# Quiz tự định nghĩa bộ riêng vì nó KHÔNG BAO GIỜ để `PROVIDER_UNAVAILABLE` hay
# `BUDGET_EXHAUSTED` lọt ra ngoài: hỏng LLM thì nó lùi về câu deterministic.
# Endpoint này thì ngược lại, cả hai mã đó đều với tới được, nên bộ chung mô tả
# đúng. Đó cũng chính là cái giá của việc không có đường lùi.
#
# Nhưng 400 thì vẫn phải tự viết: `SCOPE_ERRORS` dùng chung lấy ví dụ
# `scope_deck_id`, một trường không tồn tại ở đây, và lỗi 400 hay gặp nhất của
# endpoint này — văn bản quá dài — không có mặt trong bộ chung. Message dưới đây
# chép nguyên văn từ lần chạy thật.
EXTRACT_BAD_REQUEST: dict = {
    400: {
        "model": ErrorResponse,
        "description": (
            "Lỗi của người gọi, **đừng retry**. Ba nguyên nhân, phân biệt bằng "
            "`message` chứ không bằng `code`."
        ),
        "content": {
            "application/json": {
                "examples": {
                    "van_ban_qua_dai": {
                        "summary": "Đoạn văn vượt 4.000 ký tự",
                        "description": (
                            "Thông báo nêu **cả độ dài thật lẫn giới hạn** để backend "
                            "biết phải cắt bớt bao nhiêu. Service không tự cắt và "
                            "không tự chia nhỏ — xem mô tả trường `text`."
                        ),
                        "value": {
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": (
                                    "Đoạn văn dài 12480 ký tự, vượt giới hạn 4000 "
                                    "ký tự. Hãy cắt ngắn rồi gọi nhiều lần."
                                ),
                            }
                        },
                    },
                    "khong_du_tu_tieng_anh": {
                        "summary": "Đoạn văn không phải tiếng Anh",
                        "description": (
                            "Chặn TRƯỚC khi đặt chỗ ngân sách, nên một đoạn thuần "
                            "tiếng Việt hay một khối số không tốn token nào."
                        ),
                        "value": {
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": (
                                    "Đoạn văn chỉ có 1 từ tiếng Anh phân biệt, "
                                    "cần ít nhất 3 để trích từ vựng."
                                ),
                            }
                        },
                    },
                    "pham_vi_rong": {
                        "summary": "allowed_deck_ids rỗng",
                        "description": (
                            "Rỗng nghĩa là KHÔNG ĐƯỢC PHÉP GÌ CẢ, không bao giờ "
                            "hiểu thành 'không lọc'."
                        ),
                        "value": {
                            "error": {
                                "code": "INVALID_SCOPE",
                                "message": "allowed_deck_ids không được rỗng.",
                            }
                        },
                    },
                }
            }
        },
    }
}

EXTRACT_DESCRIPTION = """
Dán một đoạn văn tiếng Anh, nhận về danh sách **thẻ từ vựng ứng viên** đã dựng
sẵn đủ trường — backend chỉ việc lưu những thẻ người dùng chọn.

**fsoft-ai không lưu gì cả.** Nó không có endpoint ghi dữ liệu nghiệp vụ nào.
Thẻ nào vào deck nào là quyết định của backend sau khi người dùng tick chọn.

---

### Endpoint đắt nhất của service

Đây là lời gọi tốn token nhất ở đây, và không có nhánh 0 token nào. Đo thật trên
`gpt-oss-120b`:

| Phần | Token |
|---|---|
| Đoạn văn 4.000 ký tự + hướng dẫn | ~2.000 |
| Đầu ra ở `max_candidates=6` | ~3.500 |
| **Đặt chỗ mỗi lượt** | **~5.500 trên ngân sách 6.400/phút** |

Phần lớn đầu ra là **token suy luận ẩn** — model tiêu 1.100–2.900 token để đọc
và chọn lọc, và số đó không xuất hiện ở đâu trong câu trả lời.

Hệ quả cho backend:

- Gọi **tuần tự**, đừng bắn song song. Service tự giới hạn một lượt tại một
  thời điểm, nên lượt thứ hai sẽ xếp hàng chứ không chạy nhanh hơn.
- Trong lúc một lượt trích xuất đang chạy, `/chat` của người khác chỉ còn khoảng
  một lượt ngân sách. Đừng gọi endpoint này ở đường đi nóng.
- **Không** gọi mỗi lần người dùng gõ hay dán. Chờ họ bấm nút.

### Không thử lại, và cố ý như vậy

Đúng **một** lời gọi LLM mỗi request (`stats.llm_calls` luôn bằng `1`). Quiz thử
lại tới ba lần được vì mỗi lần gửi lại chỉ vài chục token; ở đây gửi lại nghĩa
là gửi lại **toàn bộ đoạn văn**, và ba lần như vậy vượt ngân sách của cả một
phút. Hỏng thì báo lỗi thật thay vì đốt hết phần của người khác.

### Ba tầng lọc sau khi model trả lời

Kết quả ở đây sẽ được **lưu thành thẻ** và thẻ thì chia sẻ được, nên không thứ
gì model nói được tin ngay:

1. **Whitelist trường** — chỉ bảy khoá đã định đi tiếp. Model trả thêm
   `audio_url` hay `card_id` thì rơi hết.
2. **Chốt chặn ký tự** — trường chứa `< > { } [ ] \\`, đường dẫn `http`, hay
   xuống dòng bị loại. Đếm ở `stats.dropped_unsafe`.
3. **Bám văn bản** — `example_sentence` phải có thật trong đoạn văn bạn gửi, so
   khớp sau khi bỏ qua khác biệt hình thức (nháy cong, gạch dài, khoảng trắng,
   hoa thường). Không khớp thì **loại cả ứng viên**. Đếm ở
   `stats.dropped_not_grounded`.

Tầng 3 là tầng quan trọng nhất: `example_sentence` là trường duy nhất model có
thể dùng để đưa nội dung mới vào dữ liệu được lưu trữ.

### Phân biệt "không có gì đáng học" với "model bịa"

Hai tình huống trông giống nhau nếu chỉ nhìn `candidates` rỗng:

| `returned_by_llm` | `candidates` | HTTP | Nghĩa |
|---|---|---|---|
| `0` | `[]` | **200** | Đoạn văn thật sự không có từ nào đáng học |
| `> 0` | `[]` | **503** | Model bịa toàn bộ, đã bị lọc sạch — thử lại có cơ hội |

Đừng coi `200` kèm mảng rỗng là lỗi, và đừng gọi lại.

### Khử trùng: đánh dấu, không xoá

Từ người học đã có **vẫn nằm trong kết quả**, mang `already_in_deck: true` kèm
`existing_card_id`. Giao diện nên bỏ tick sẵn thay vì giấu đi.

Kiểm tra chỉ diễn ra TRONG `allowed_deck_ids` — cùng một từ, phạm vi hẹp lại thì
cờ về `false`. Và khớp theo `word` viết thường, **không có lemma hoá**: bộ thẻ có
`emission` mà đoạn văn cho ra `emissions` thì vẫn báo chưa có.

Luôn kiểm `stats.dedup_checked`. `false` nghĩa là chỉ mục đang rỗng nên mọi cờ
`already_in_deck` đều vô nghĩa — xảy ra trong cửa sổ sau khi container khởi động
lại mà đồng bộ đầu tiên chưa xong.
""".strip()


@router.post(
    "/vocab/extract",
    response_model=VocabExtractResponse,
    summary="Trích từ vựng đáng học từ một đoạn văn",
    description=EXTRACT_DESCRIPTION,
    responses={
        200: {
            "description": (
                "Danh sách ứng viên đã qua lọc. **Mảng rỗng kèm "
                "`stats.returned_by_llm = 0` cũng là 200** — đoạn văn không có gì "
                "đáng học. Số ứng viên có thể ít hơn `max_candidates`, luôn đọc "
                "độ dài mảng."
            )
        },
        **UNAUTHORIZED,
        **EXTRACT_BAD_REQUEST,
        **BUDGET,
        **NOT_READY,
    },
)
async def extract_vocab(
    request: Request,
    body: Annotated[VocabExtractRequest, Body(openapi_examples=VI_DU_VOCAB_EXTRACT)],
) -> VocabExtractResponse:
    service = request.app.state.service

    # Cần chỉ mục sẵn sàng vì bước khử trùng đọc nó. Trích xuất không cần
    # encoder, nhưng `is_ready` gộp cả hai cờ nên kiểm chung là đủ và nhất quán
    # với /search, /quiz.
    if not service.is_ready:
        raise IndexNotReady("Model chưa nạp xong hoặc index chưa sẵn sàng.")

    candidates, stats = await service.vocab.extract(body)

    return VocabExtractResponse(candidates=candidates, stats=stats)


# ---------------------------------------------------------------
# M9 — POST /internal/v1/vocab/generate
# ---------------------------------------------------------------

GENERATE_BAD_REQUEST: dict = {
    400: {
        "model": ErrorResponse,
        "description": (
            'Luật nghiệp vụ, hình dạng `{"error": {...}}`. Khác với 422 của FastAPI '
            '(`{"detail": [...]}`) mà `count` hay `level` sai kiểu sẽ trả về. Parser '
            "phải chịu được cả hai."
        ),
        "content": {
            "application/json": {
                "examples": {
                    "chu_de_qua_dai": {
                        "summary": "Chủ đề vượt 120 ký tự",
                        "description": "Thông báo nêu cả độ dài thật lẫn giới hạn.",
                        "value": {
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": (
                                    "Chủ đề dài 287 ký tự, vượt giới hạn 120 ký tự. "
                                    "Hãy rút ngắn lại."
                                ),
                            }
                        },
                    },
                    "chu_de_khong_co_chu": {
                        "summary": "Chủ đề toàn số hoặc ký hiệu",
                        "description": (
                            "Chặn trước khi đốt token vào một chuỗi không có nghĩa. "
                            "Chú ý: chủ đề TIẾNG VIỆT hoàn toàn hợp lệ, đây là ca "
                            "dùng chính chứ không phải ca lỗi."
                        ),
                        "value": {
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": "Chủ đề phải có ít nhất một chữ cái.",
                            }
                        },
                    },
                    "pham_vi_rong": {
                        "summary": "allowed_deck_ids rỗng",
                        "description": "Rỗng không bao giờ có nghĩa là không lọc.",
                        "value": {
                            "error": {
                                "code": "INVALID_SCOPE",
                                "message": "allowed_deck_ids không được rỗng.",
                            }
                        },
                    },
                }
            }
        },
    }
}

GENERATE_DESCRIPTION = """
Người dùng gõ một chủ đề, nhận về **thẻ từ vựng dựng sẵn đủ trường** — đúng shape
thẻ nên backend chỉ việc lưu những thẻ người dùng tick chọn.

Anh em với `/vocab/extract`, nhưng ngược chiều: `extract` cần sẵn một đoạn văn,
còn ở đây đầu vào chỉ là một chủ đề. **fsoft-ai không lưu gì cả.**

---

### Điều khác biệt lớn nhất so với `/vocab/extract`, và nó không vá được

Ở `/vocab/extract`, câu ví dụ **bắt buộc có thật trong văn bản người dùng dán
vào**. Đó không phải một bộ lọc, đó là một phép kiểm **xuất xứ**: không một chữ
tiếng Anh mới nào lọt vào dữ liệu lưu trữ.

Ở đây không có văn bản gốc. Xuất xứ biến mất và không dựng lại được.

| | `/vocab/extract` | `/vocab/generate` |
|---|---|---|
| Nội dung tiếng Anh mới | model **không thể** đưa vào | model **có thể** |
| Service kiểm được | xuất xứ + hình dạng | **chỉ hình dạng** |
| Ai soát nội dung | văn bản do người dùng tự chọn dán | **chỉ có bước tick chọn** |

Cụ thể, service **không kiểm được và không thể kiểm được**: nghĩa tiếng Việt có
đúng không, phiên âm có phải IPA thật của từ đó không, từ đó có tồn tại trong
tiếng Anh không, có đúng trình độ `level` đã xin không, từ có thật sự thuộc chủ
đề không.

Vì vậy bước người dùng xác nhận **không phải chi tiết giao diện** — nó là lần
soát nội dung duy nhất mà mấy thẻ này sẽ có trước khi thành thẻ chia sẻ được.
Đọc kỹ mục 5.7 của tài liệu tích hợp backend trước khi dựng giao diện.

### Danh sách "tránh" — vì sao lượt gọi thứ hai không ra từ cũ

Trước khi gọi LLM, service embed chủ đề rồi tìm ngữ nghĩa **trong
`allowed_deck_ids`** để lấy những từ người học đã có, đưa vào prompt làm danh
sách cần tránh. Bước này **0 token LLM** — chỉ một lượt suy luận ONNX cục bộ.

Cộng thêm `exclude_words` bạn gửi lên: đó là cách làm nút "thêm từ nữa" mà
service không phải lưu trạng thái nào. Client giữ trạng thái, giống hệt cách
`/chat` làm với `history`.

**Danh sách tránh là lời khuyên, `already_in_deck` mới là luật.** Model có thể
bỏ qua danh sách; khi đó thẻ trùng vẫn quay về, mang cờ `already_in_deck: true`
kèm `existing_card_id`, và **vẫn nằm trong kết quả** để giao diện bỏ tick sẵn.

### Một lời gọi LLM, không thử lại

`stats.llm_calls` luôn bằng 1. Không có cờ kiểu `use_ai_context: false` để chạy
miễn phí — Groq chết là tính năng này chết theo.

Service tự giới hạn **một lượt sinh hoặc trích xuất tại một thời điểm**, dùng
chung hàng đợi với `/vocab/extract`. Chờ quá 10 giây thì trả `429` luôn thay vì
để request treo.

### Mảng rỗng KHÔNG phải câu trả lời hợp lệ ở đây

Đây là chỗ cố ý làm ngược `/vocab/extract`:

| | `/vocab/extract` | `/vocab/generate` |
|---|---|---|
| `returned_by_llm = 0` | **200** — đoạn văn thật sự không có gì đáng học | **503** |

"Chủ đề của bạn không có từ vựng nào" gần như không bao giờ đúng. Số 0 ở đây
nghĩa là model từ chối, hoặc chạm bộ lọc an toàn, hoặc trả rác — trả 200 rỗng
là nói dối. Thử lại, hoặc gợi ý người dùng gõ chủ đề cụ thể hơn.
""".strip()


@router.post(
    "/vocab/generate",
    response_model=VocabGenerateResponse,
    summary="Sinh thẻ từ vựng mới theo một chủ đề",
    description=GENERATE_DESCRIPTION,
    responses={
        200: {
            "description": (
                "Thẻ đã dựng sẵn. Số thẻ có thể ít hơn `count` — model được phép trả "
                "ít hơn nếu chủ đề hẹp, và các bộ lọc tất định cũng có thể loại bớt. "
                "Luôn đọc độ dài mảng, và luôn coi mọi trường là nội dung chưa ai "
                "kiểm chứng."
            )
        },
        **UNAUTHORIZED,
        **GENERATE_BAD_REQUEST,
        **BUDGET,
        **NOT_READY,
    },
)
async def generate_vocab(
    request: Request,
    body: Annotated[VocabGenerateRequest, Body(openapi_examples=VI_DU_VOCAB_GENERATE)],
) -> VocabGenerateResponse:
    service = request.app.state.service

    # Ở đây `is_ready` gánh hai việc THẬT chứ không phải một: encoder dùng để
    # embed chủ đề dựng danh sách tránh, chỉ mục dùng để đánh dấu trùng. Khác
    # `/vocab/extract` vốn chỉ cần vế thứ hai.
    if not service.is_ready:
        raise IndexNotReady("Model chưa nạp xong hoặc index chưa sẵn sàng.")

    topic_understood, cards, stats = await service.vocab_generator.generate(body)

    return VocabGenerateResponse(topic_understood=topic_understood, cards=cards, stats=stats)


# ---------------------------------------------------------------
# M10 — POST /internal/v1/vocab/lookup
# ---------------------------------------------------------------

LOOKUP_BAD_REQUEST: dict = {
    400: {
        "model": ErrorResponse,
        "description": 'Luật nghiệp vụ, hình dạng `{"error": {...}}`.',
        "content": {
            "application/json": {
                "examples": {
                    "khong_phai_mot_tu": {
                        "summary": "Chuỗi gửi lên không thể là một từ",
                        "description": (
                            "Có chữ số, dấu chấm, hay quá ba từ. Chặn ở đây là chặn "
                            "miễn phí — không lời gọi LLM nào cứu được một chuỗi như vậy, "
                            "mà mỗi lượt gọi hụt ăn mất một phần ngân sách của cả hệ thống.\n\n"
                            "Chú ý: từ tiếng Anh **có thật nhưng gõ sai chính tả** KHÔNG rơi "
                            "vào đây — nó đi tiếp và nhận `200` với `found: false`."
                        ),
                        "value": {
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": (
                                    "Chuỗi cần tra phải là một từ hoặc cụm nhiều nhất ba từ "
                                    "tiếng Anh, chỉ gồm chữ cái, dấu nối hoặc dấu nháy đơn."
                                ),
                            }
                        },
                    },
                    "qua_dai": {
                        "summary": "Chuỗi dài hơn 64 ký tự",
                        "value": {
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": "Chuỗi cần tra dài 180 ký tự, vượt giới hạn 64 ký tự.",
                            }
                        },
                    },
                    "pham_vi_rong": {
                        "summary": "allowed_deck_ids rỗng",
                        "value": {
                            "error": {
                                "code": "INVALID_SCOPE",
                                "message": "allowed_deck_ids không được rỗng.",
                            }
                        },
                    },
                }
            }
        },
    }
}

LOOKUP_DESCRIPTION = """
Tra **đúng một từ** và trả về thẻ từ vựng dựng sẵn đủ trường.

Hai ca dùng chung một endpoint: người dùng bấm nút tra trong lúc soạn thẻ, và
người dùng **bôi đen một từ** trong lúc đọc. Ca thứ hai gửi kèm `context` — câu
chứa từ đó — để chọn đúng nghĩa.

---

### Đọc `stats.source` trước khi đọc bất cứ thứ gì khác

| `source` | Token | Nghĩa |
|---|---|---|
| `YOUR_DECK` | **0** | Từ này **đã có** trong bộ thẻ người dùng. `card` là nội dung thẻ thật của họ |
| `CACHE` | **0** | Đã có người tra từ này (cùng ngữ cảnh) gần đây |
| `AI` | ~2.000 | Gọi LLM thật |

`YOUR_DECK` là câu trả lời đáng giá nhất trong ba cái, và không phải vì nó miễn
phí: người dùng đang ở màn hình **soạn thẻ mới**, nên biết mình sắp tạo thẻ
trùng còn hữu ích hơn một thẻ mới. Giao diện nên nói thẳng ra và mời họ mở thẻ
cũ, thay vì lặng lẽ điền vào form.

### Backend chỉ nên gọi tới đây khi từ điển của mình đã trượt

Ngân sách LLM là **6.400 token mỗi phút cho toàn bộ ứng dụng**. Một lượt `AI` ăn
khoảng 2.000, nên nếu mỗi lần bấm nút đều tới thẳng đây thì cả hệ thống chỉ chịu
được chừng ba lượt bấm một phút — và `/chat` của mọi người khác chết theo.

Thứ tự đúng: **từ điển của backend → endpoint này**. Đây là đường lùi cho những
từ từ điển không có, không phải đường chính.

### Gõ sai chính tả trả 200, không phải lỗi

```json
{ "source": "AI", "found": false, "suggestion": "receive", "card": null, "stats": {...} }
```

`found: false` nghĩa là **đây không phải một từ tiếng Anh có thật**. Câu hỏi vẫn
đã được trả lời — câu trả lời là "không có từ này". Giao diện nên hiện đúng như
vậy, kèm gợi ý chính tả nếu có, rồi để người dùng tự điền.

Service **không kiểm chứng được** cờ đó: repo không có từ điển tiếng Anh nào. Nó
là lời của model. Nhưng hỏi thẳng vẫn tốt hơn nhiều so với để model bịa một
nghĩa nghe rất thật cho một từ không tồn tại, rồi người học lưu vào bộ thẻ.

### `card.word` có thể khác chuỗi bạn gửi lên

Gửi `"Donuts"`, nhận về `"donut"`. Thẻ từ vựng cần dạng từ điển, và đó là chủ ý.
Service kiểm rằng hai dạng vẫn là **cùng một từ** — model trả về một từ khác hẳn
thì lượt đó bị loại.

### Mọi trường đều do model sinh ra, trừ `YOUR_DECK`

Service kiểm được hình dạng — độ dài, bộ ký tự, ngôn ngữ, câu ví dụ có dùng đúng
từ không. Service **không kiểm được** nghĩa tiếng Việt có đúng không, phiên âm có
thật không, từ có tồn tại không. Người dùng vẫn phải xem trước khi lưu, và
frontend vẫn phải escape khi render.
""".strip()


@router.post(
    "/vocab/lookup",
    response_model=VocabLookupResponse,
    summary="Tra nghĩa một từ, kèm câu ví dụ",
    description=LOOKUP_DESCRIPTION,
    responses={
        200: {
            "description": (
                "Đọc `stats.source` để biết lượt này có tốn token không, và `found` "
                "để biết `card` có phải `null` không. **`found: false` vẫn là 200.**"
            )
        },
        **UNAUTHORIZED,
        **LOOKUP_BAD_REQUEST,
        **BUDGET,
        **NOT_READY,
    },
)
async def lookup_vocab(
    request: Request,
    body: Annotated[VocabLookupRequest, Body(openapi_examples=VI_DU_VOCAB_LOOKUP)],
) -> VocabLookupResponse:
    service = request.app.state.service

    # Chỉ mục phải sẵn sàng vì đường 0 token đầu tiên đọc nó. Chỉ mục rỗng không
    # làm hỏng gì — chỉ khiến mọi lượt tra đều rơi xuống đường AI, tức đắt hơn
    # hẳn mà không ai biết. Chặn ở đây để cửa sổ đó không âm thầm trôi qua.
    if not service.is_ready:
        raise IndexNotReady("Model chưa nạp xong hoặc index chưa sẵn sàng.")

    source, found, suggestion, card, stats = await service.vocab_lookup.lookup(body)

    return VocabLookupResponse(
        source=source,  # type: ignore[arg-type]
        found=found,
        suggestion=suggestion,
        card=card,
        stats=stats,
    )
