"""
Schema cho ba endpoint từ vựng:

  - `/internal/v1/vocab/extract`   dán một đoạn văn      SPEC muc 8.5b
  - `/internal/v1/vocab/generate`  gõ một chủ đề         SPEC muc 8.5c
  - `/internal/v1/vocab/lookup`    tra đúng một từ       SPEC muc 8.5d

Cả ba dùng CHUNG một `VocabCandidate`. Đó là chủ ý: backend đã bind vào shape
thẻ ấy từ M8, tách ra thành ba lớp giống hệt nhau chỉ đẻ thêm ba type Java, ba
Jackson binding và ba mapper, đổi lại con số không.

Chúng khác nhau ở XUẤT XỨ của `example_sentence`, không khác ở hình dạng thẻ:
`extract` bảo đảm câu ấy có thật trong văn bản người dùng dán vào; `generate` và
`lookup` thì không có văn bản nào để bảo đảm. Xem mô tả của chính trường đó.

Quy tắc viết mô tả ở đây giống mọi file schema khác: nói HẬU QUẢ CỦA VIỆC HIỂU
SAI, đừng nói lại tên trường. Viết "trường này là id của deck" thì không cứu
được ai.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Ví dụ REQUEST — dùng ở hai chỗ: `Body(openapi_examples=...)` trong router và
# `model_config` của `VocabExtractRequest`, để menu thả xuống và tab "Schema"
# không bao giờ lệch nhau.
#
# Cả ba đoạn văn đều CỐ Ý chứa từ có trong bộ thẻ mẫu (`resilient` = thẻ 101,
# `deadline` = thẻ 201) để bấm Execute là thấy ngay cờ khử trùng hoạt động.
#
# KHÔNG có ví dụ "văn bản quá dài" trong danh sách này: nó cần 4.001 ký tự thật,
# và nhét ngần ấy vào file schema chỉ để minh hoạ một lỗi 400 là đánh đổi tồi.
# Hành vi đó được mô tả trong `description` của `text` và có test riêng.
VI_DU_VOCAB_EXTRACT: dict = {
    "doan_van_cong_so": {
        "summary": "Đoạn văn công sở — mọi từ đều mới",
        "description": (
            "Bấm Execute: trả về các từ như `procurement`, `contingency`, "
            "`stipulate`. Không từ nào có trong bộ thẻ mẫu nên `already_in_deck` "
            "là `false` ở mọi ứng viên."
        ),
        "value": {
            "text": (
                "The procurement team must stipulate delivery terms before the "
                "vendor signs. Without a contingency clause, a delayed shipment "
                "could jeopardize the entire quarter. Legal reviewed the draft "
                "and flagged two ambiguous provisions that warrant revision."
            ),
            "allowed_deck_ids": [1, 2, 3, 4],
            "max_candidates": 5,
        },
    },
    "co_tu_da_co_trong_the": {
        "summary": "Có từ người học ĐÃ CÓ — xem cờ already_in_deck",
        "description": (
            "Đoạn văn chứa `resilient` (thẻ 101, deck 1) và `deadline` (thẻ 201, "
            "deck 2). Hai từ này **vẫn nằm trong kết quả**, nhưng mang "
            "`already_in_deck: true` kèm `existing_card_id`. Giao diện nên bỏ "
            "tick sẵn chứ đừng giấu đi — người học cần thấy là mình đã có."
        ),
        "value": {
            "text": (
                "The team stayed resilient after missing the first deadline. "
                "Their meticulous retrospective uncovered a bottleneck nobody "
                "had anticipated, and the revised schedule proved viable."
            ),
            "allowed_deck_ids": [1, 2, 3, 4],
            "max_candidates": 5,
        },
    },
    "thu_hep_pham_vi": {
        "summary": "Cùng đoạn văn, phạm vi hẹp — cờ khử trùng tắt",
        "description": (
            "Y hệt ví dụ trên nhưng `allowed_deck_ids` chỉ có deck 3. `resilient` "
            "và `deadline` nằm ở deck 1 và 2, ngoài phạm vi, nên chúng quay về "
            "`already_in_deck: false`. Đây là cách kiểm ranh giới phạm vi trong "
            "mười giây: **fsoft-ai không bao giờ nhìn ra ngoài `allowed_deck_ids`**, "
            "kể cả để trả lời câu hỏi 'thẻ này đã tồn tại chưa'."
        ),
        "value": {
            "text": (
                "The team stayed resilient after missing the first deadline. "
                "Their meticulous retrospective uncovered a bottleneck nobody "
                "had anticipated, and the revised schedule proved viable."
            ),
            "allowed_deck_ids": [3],
            "max_candidates": 5,
        },
    },
}


class VocabCandidate(BaseModel):
    word: str = Field(
        description=(
            "Dạng từ điển, chữ thường.\n\n"
            "Ở `/vocab/extract`, CÓ THỂ khác dạng xuất hiện trong đoạn văn: văn bản "
            "có `running` thì thẻ ghi `run`, vì thẻ từ vựng cần dạng gốc. Đó là chủ "
            "ý, không phải lỗi."
        ),
        examples=["resilient"],
    )
    phonetic: str | None = Field(
        default=None,
        description="IPA kèm hai dấu gạch chéo. `null` khi model không chắc.",
        examples=["/rɪˈzɪliənt/"],
    )
    part_of_speech: str | None = Field(
        default=None,
        description=(
            "Một trong `noun`, `verb`, `adj`, `adv`, `prep`, `conj`. Giá trị ngoài "
            "tập này bị đổi thành `null` chứ không làm hỏng cả thẻ — mất một nhãn "
            "còn hơn mất một từ đáng học."
        ),
        examples=["adj"],
    )
    meaning: str = Field(
        description=(
            "Nghĩa tiếng Việt. Trường BẮT BUỘC: ứng viên không có nghĩa thì bị bỏ "
            "hẳn, vì một thẻ không nghĩa thì lưu vào cũng vô dụng."
        ),
        examples=["kiên cường, có khả năng phục hồi nhanh"],
    )
    definition_en: str | None = Field(
        default=None,
        description="Định nghĩa tiếng Anh, một câu ngắn.",
        examples=["able to recover quickly from difficult conditions"],
    )
    example_sentence: str = Field(
        description=(
            "**Bảo đảm của trường này KHÁC NHAU giữa hai endpoint. Đừng render "
            "chung một câu giải thích.**\n\n"
            "`/vocab/extract` — câu này **có thật trong đoạn văn bạn gửi lên**. "
            "Service so khớp lại với văn bản gốc sau khi bỏ qua khác biệt hình thức "
            "(nháy cong so với nháy thẳng, gạch dài so với gạch ngắn, khoảng trắng, "
            "hoa thường); không khớp thì **cả ứng viên bị loại**. Đây là trường duy "
            "nhất LLM có thể dùng để đưa nội dung mới vào dữ liệu lưu trữ, nên bắt "
            "buộc nó có sẵn trong văn bản chính là cách đóng đường đó lại.\n\n"
            "`/vocab/generate` và `/vocab/lookup` — **không có văn bản nguồn nào để "
            "so khớp.** Câu do model tự viết. Service chỉ kiểm được hình dạng: độ "
            "dài, bộ ký tự, và việc câu có thật sự chứa chính từ đó. Không có phép "
            "kiểm nào nói được câu ấy đúng ngữ pháp hay đúng nghĩa.\n\n"
            "Ngoại lệ duy nhất: `/vocab/lookup` với `source: YOUR_DECK` trả về nội "
            "dung **thẻ có thật của người dùng**, không phải chữ model sinh ra.\n\n"
            "Cờ phân biệt máy đọc được là **`stats.dropped_not_grounded`**: có ở "
            "`extract`, KHÔNG có ở hai endpoint kia. Backend nào cần rẽ nhánh theo "
            "xuất xứ thì rẽ theo trường đó, đừng rẽ theo URL đã gọi."
        ),
        examples=["The team stayed resilient after missing the first deadline."],
    )
    example_meaning: str | None = Field(
        default=None,
        description="Bản dịch tiếng Việt của câu ví dụ.",
        examples=["Cả nhóm vẫn kiên cường sau khi lỡ hạn chót đầu tiên."],
    )
    already_in_deck: bool = Field(
        description=(
            "Từ này đã có trong `allowed_deck_ids` chưa. **Ứng viên vẫn được trả "
            "về khi `true`** — giao diện nên bỏ tick sẵn thay vì giấu, để người học "
            "biết mình đã có từ đó.\n\n"
            "Khớp theo `word` viết thường, KHÔNG có lemma hoá: bộ thẻ có `emission` "
            "mà đoạn văn cho ra `emissions` thì cờ này vẫn `false`. Chấp nhận trùng "
            "còn hơn tự ý bỏ một từ mà người dùng không hiểu vì sao nó biến mất."
        ),
        examples=[False],
    )
    existing_card_id: int | None = Field(
        default=None,
        description=(
            "Thẻ đã có, chỉ khác `null` khi `already_in_deck` là `true`. Luôn nằm "
            "TRONG `allowed_deck_ids` — service không bao giờ tiết lộ thẻ ngoài "
            "phạm vi, kể cả qua trường này. Từ nằm ở nhiều deck được phép thì lấy "
            "`card_id` nhỏ nhất, để hai lượt gọi giống nhau cho cùng kết quả."
        ),
        examples=[101],
    )


class VocabExtractStats(BaseModel):
    text_chars: int = Field(description="Độ dài đoạn văn đã nhận, tính bằng ký tự.", examples=[214])
    distinct_english_tokens: int = Field(
        description=(
            "Số từ tiếng Anh phân biệt tìm thấy. Đây cũng là TRẦN THỰC TẾ của số "
            "ứng viên: xin 10 từ từ một đoạn văn 6 từ thì chỉ hỏi model 6."
        ),
        examples=[31],
    )
    returned_by_llm: int = Field(
        description=(
            "Số ứng viên model trả về TRƯỚC khi lọc. Đây là trường phân biệt hai "
            "tình huống trông giống nhau: `0` nghĩa là đoạn văn thật sự không có gì "
            "đáng học; lớn hơn 0 mà `candidates` rỗng nghĩa là model bịa toàn bộ và "
            "bị lọc sạch — khi đó service trả 503 chứ không trả 200 rỗng."
        ),
        examples=[5],
    )
    dropped_not_grounded: int = Field(
        description=(
            "Số ứng viên bị loại vì câu ví dụ không có trong đoạn văn, hoặc từ không "
            "xuất phát từ đoạn văn. Lớn hơn 0 là bình thường; lớn liên tục là dấu "
            "hiệu prompt cần chỉnh."
        ),
        examples=[1],
    )
    dropped_unsafe: int = Field(
        description=(
            "Số ứng viên bị loại vì trường có ký tự cấm (`< > { } [ ] \\`, đường dẫn "
            "`http`, xuống dòng) hoặc thiếu `meaning`. Khác `dropped_not_grounded` ở "
            "chỗ đây là chốt chặn an toàn, không phải chốt chặn nội dung."
        ),
        examples=[0],
    )
    already_in_deck_count: int = Field(
        description="Trong số ứng viên trả về, bao nhiêu từ người học đã có.",
        examples=[2],
    )
    dedup_checked: bool = Field(
        description=(
            "Cờ khử trùng có ĐÁNG TIN không. `false` nghĩa là chỉ mục đang rỗng nên "
            "mọi `already_in_deck` đều là `false` một cách vô nghĩa.\n\n"
            "Vì sao cần: gói hosting không có đĩa bền thì mỗi lần container khởi "
            "động lại là SQLite trắng, và `/readyz` trả 200 TRONG KHI chỉ mục còn "
            "rỗng — cửa sổ đó kéo dài tới lúc đồng bộ đầu tiên xong. Không có cờ này "
            "thì lời nói dối 'không trùng gì cả' hoàn toàn im lặng."
        ),
        examples=[True],
    )
    llm_calls: int = Field(
        description=(
            "Luôn bằng `1`. Endpoint này KHÔNG thử lại: gửi lại nghĩa là gửi lại "
            "toàn bộ đoạn văn, mà ba lần như vậy đã vượt ngân sách token của cả một "
            "phút. Hỏng thì báo lỗi thật thay vì đốt hết ngân sách của người khác."
        ),
        examples=[1],
    )
    prompt_tokens: int = Field(description="Token đầu vào của lời gọi.", examples=[1324])
    completion_tokens: int = Field(
        description=(
            "Token đầu ra, **đã gồm cả token suy luận ẩn** của họ `gpt-oss` — phần "
            "này chiếm phần lớn và không xuất hiện trong câu trả lời."
        ),
        examples=[1762],
    )
    latency_ms: int = Field(description="Thời gian của riêng lời gọi LLM.", examples=[4820])


class VocabExtractRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [v["value"] for v in VI_DU_VOCAB_EXTRACT.values()]}
    )

    text: str = Field(
        description=(
            "Đoạn văn tiếng Anh cần trích từ vựng. Dán thẳng bài đọc, email hay "
            "đoạn tin đều được.\n\n"
            "**Giới hạn 4.000 ký tự, và vượt là 400 `INVALID_REQUEST`** kèm cả độ "
            "dài thật lẫn giới hạn trong thông báo. Service KHÔNG tự cắt và KHÔNG tự "
            "chia nhỏ — chia nhỏ văn bản là việc backend làm, có chủ ý (SPEC mục "
            "4.2). Cắt im lặng sẽ khiến người dùng mất phần cuối bài đọc mà không "
            "biết.\n\n"
            "Lưu ý hình dạng lỗi: giới hạn này là luật nghiệp vụ nên trả "
            '`{"error": {...}}` với mã 400, KHÁC với `{"detail": [...]}` mã 422 mà '
            "FastAPI trả khi sai kiểu dữ liệu. Parser phía backend phải chịu được "
            "cả hai.\n\n"
            "Cần ít nhất 3 từ tiếng Anh phân biệt, nếu không cũng là 400 — chặn "
            "việc đốt vài nghìn token vào một đoạn thuần tiếng Việt hay một khối số."
        ),
        examples=["The team stayed resilient after missing the first deadline."],
    )
    allowed_deck_ids: list[int] = Field(
        description=(
            "TOÀN BỘ ranh giới bảo mật nằm ở trường này. fsoft-ai không biết người "
            "dùng là ai — không `profileId`, không phiên đăng nhập, không bảng phân "
            "quyền. Nó chỉ tin danh sách deck mà backend Java truyền vào.\n\n"
            "Ở endpoint này danh sách dùng cho ĐÚNG MỘT việc: quyết định từ nào bị "
            "đánh dấu `already_in_deck`. Nó không hề lọc đầu ra — mọi từ trích được "
            "đều trả về.\n\n"
            "Danh sách RỖNG nghĩa là KHÔNG ĐƯỢC PHÉP GÌ CẢ → 400 `INVALID_SCOPE`, "
            "không bao giờ hiểu thành 'không lọc'. Đừng lấy giá trị này từ request "
            "của frontend — backend phải tự tính từ quyền sở hữu deck."
        ),
        examples=[[1, 2, 3, 4]],
    )
    max_candidates: int = Field(
        default=5,
        ge=1,
        le=6,
        description=(
            "TRẦN số từ, không phải lời hứa. Con số thật còn bị kẹp thêm hai lần: "
            "theo số từ tiếng Anh phân biệt trong đoạn văn, rồi theo số ứng viên "
            "sống sót qua bộ lọc. Luôn đọc độ dài mảng thay vì giả định.\n\n"
            "**Trần cứng là 6 vì ngân sách token, không phải vì kỹ thuật.** Đo thật "
            "trên `gpt-oss-120b`: mỗi từ xin thêm tốn khoảng 350 token đầu ra, và "
            "phần lớn trong đó là token SUY LUẬN ẨN không hề xuất hiện trong câu "
            "trả lời. Với đoạn văn dài nhất, một lượt gọi đặt chỗ:\n\n"
            "| `max_candidates` | phần của ngân sách 6.400/phút |\n"
            "|---|---|\n"
            "| 5 | 81% |\n"
            "| 6 | 87% |\n"
            "| 10 | **108% — tự 429 chính mình, luôn luôn** |\n\n"
            "Nghĩa là một lượt trích xuất chiếm gần trọn ngân sách một phút của "
            "TOÀN hệ thống. Backend nên gọi endpoint này **tuần tự**, đừng bắn "
            "song song, và đừng gọi khi người dùng vừa dán xong đã gọi ngay.\n\n"
            "Ngoài khoảng 1–6 bị FastAPI chặn ở tầng validate và trả 422 với hình "
            'dạng `{"detail": [...]}`.'
        ),
        examples=[5],
    )


class VocabExtractResponse(BaseModel):
    # Output CHẠY THẬT của ví dụ request `co_tu_da_co_trong_the` trên dữ liệu mẫu
    # 24 thẻ, chép nguyên văn chứ không bịa. Nhờ vậy nó minh hoạ đúng ba luật:
    #   - `resilient` (thẻ 101) và `meticulous` (thẻ 103) VẪN nằm trong danh sách,
    #     chỉ mang thêm cờ — đây là quyết định "đánh dấu, không xoá";
    #   - mọi `example_sentence` đều là câu có thật trong đoạn văn gửi lên;
    #   - `completion_tokens` 1.433 cho 5 từ, phần lớn là token suy luận ẩn.
    # Rút gọn còn ba ứng viên cho vừa trang; lần chạy thật trả về năm.
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "candidates": [
                        {
                            "word": "resilient",
                            "phonetic": "/rɪˈzɪliənt/",
                            "part_of_speech": "adj",
                            "meaning": "kiên cường, có khả năng phục hồi nhanh",
                            "definition_en": "able to recover quickly from difficulties",
                            "example_sentence": (
                                "The team stayed resilient after missing the first deadline."
                            ),
                            "example_meaning": (
                                "Cả nhóm vẫn kiên cường sau khi lỡ hạn chót đầu tiên."
                            ),
                            "already_in_deck": True,
                            "existing_card_id": 101,
                        },
                        {
                            "word": "meticulous",
                            "phonetic": "/məˈtɪkjələs/",
                            "part_of_speech": "adj",
                            "meaning": "tỉ mỉ, cẩn thận đến từng chi tiết",
                            "definition_en": "showing great attention to detail",
                            "example_sentence": (
                                "Their meticulous retrospective uncovered a bottleneck "
                                "nobody had anticipated."
                            ),
                            "example_meaning": (
                                "Buổi tổng kết tỉ mỉ của họ đã phát hiện một điểm nghẽn "
                                "mà không ai lường trước."
                            ),
                            "already_in_deck": True,
                            "existing_card_id": 103,
                        },
                        {
                            "word": "bottleneck",
                            "phonetic": "/ˈbɒtlnek/",
                            "part_of_speech": "noun",
                            "meaning": "điểm nghẽn, nút thắt cổ chai",
                            "definition_en": "a point of congestion that slows a process",
                            "example_sentence": (
                                "Their meticulous retrospective uncovered a bottleneck "
                                "nobody had anticipated."
                            ),
                            "example_meaning": (
                                "Buổi tổng kết tỉ mỉ của họ đã phát hiện một điểm nghẽn "
                                "mà không ai lường trước."
                            ),
                            "already_in_deck": False,
                            "existing_card_id": None,
                        },
                    ],
                    "stats": {
                        "text_chars": 177,
                        "distinct_english_tokens": 21,
                        "returned_by_llm": 5,
                        "dropped_not_grounded": 0,
                        "dropped_unsafe": 0,
                        "already_in_deck_count": 2,
                        "dedup_checked": True,
                        "llm_calls": 1,
                        "prompt_tokens": 821,
                        "completion_tokens": 1433,
                        "latency_ms": 5005,
                    },
                }
            ]
        }
    )

    candidates: list[VocabCandidate] = Field(
        description=(
            "Ứng viên đã qua lọc, đúng shape thẻ nên backend chỉ việc lưu. "
            "**fsoft-ai không lưu gì cả** — nó không có endpoint ghi dữ liệu nghiệp "
            "vụ nào, và việc thẻ nào được thêm vào deck nào là quyết định của backend "
            "sau khi người dùng chọn.\n\n"
            "Mảng RỖNG kèm `stats.returned_by_llm = 0` là câu trả lời hợp lệ: đoạn "
            "văn không có gì đáng học. Đừng coi là lỗi, đừng gọi lại."
        )
    )
    stats: VocabExtractStats


# ---------------------------------------------------------------
# M9 — POST /internal/v1/vocab/generate
# ---------------------------------------------------------------

# Ba ví dụ, cùng vai trò với `VI_DU_VOCAB_EXTRACT`: menu thả xuống của Swagger
# và tab "Schema" lấy chung một nguồn nên không lệch nhau được.
#
# Ví dụ thứ hai CỐ Ý dùng chủ đề công việc với `allowed_deck_ids` chứa deck 2 —
# bộ thẻ mẫu ở deck đó toàn từ công sở (`deadline`, `procurement`,
# `stakeholder`...), nên bấm Execute là thấy ngay danh sách tránh hoạt động:
# kết quả sẽ KHÔNG lặp lại mấy từ ấy.
VI_DU_VOCAB_GENERATE: dict = {
    "chu_de_tieng_anh": {
        "summary": "Chủ đề gõ bằng tiếng Anh",
        "description": "Dạng ngắn gọn nhất. `level` bỏ trống nghĩa là không ràng buộc trình độ.",
        "value": {
            "topic": "air travel",
            "allowed_deck_ids": [1, 2, 3, 4],
            "count": 5,
        },
    },
    "chu_de_tieng_viet_co_tranh": {
        "summary": "Chủ đề tiếng Việt — xem danh sách tránh hoạt động",
        "description": (
            "Deck 2 của bộ thẻ mẫu toàn từ công sở. Kết quả sẽ tránh chính những "
            "từ đó, vì service tìm ngữ nghĩa trong phạm vi rồi đưa vào prompt."
        ),
        "value": {
            "topic": "tôi muốn học từ về công việc",
            "allowed_deck_ids": [1, 2, 3, 4],
            "level": "B2",
            "count": 6,
        },
    },
    "xin_them_tu": {
        "summary": "Lượt thứ hai — xin thêm từ, không lặp lại lượt đầu",
        "description": (
            "`exclude_words` là những từ giao diện VỪA hiện cho người dùng ở lượt "
            "trước. Client giữ trạng thái, service không lưu gì — đúng cách `/chat` "
            "làm với `history`."
        ),
        "value": {
            "topic": "tôi muốn học từ về công việc",
            "allowed_deck_ids": [1, 2, 3, 4],
            "level": "B2",
            "count": 6,
            "exclude_words": ["itinerary", "layover", "boarding pass"],
        },
    },
}


class VocabGenerateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [vi_du["value"] for vi_du in VI_DU_VOCAB_GENERATE.values()]}
    )

    topic: str = Field(
        description=(
            'Chủ đề muốn học, gõ tự do. Nhận cả `"work"` lẫn cả câu '
            '`"tôi muốn học từ về công việc"` — model tự hiểu và trả lại cách nó '
            "hiểu ở `topic_understood`.\n\n"
            "Tối đa **120 ký tự**. Vượt quá trả **400 `INVALID_REQUEST`** với hình "
            'dạng `{"error": {...}}`, KHÔNG phải 422 với `{"detail": [...]}`. '
            "Hai hình dạng lỗi khác nhau là cố ý: 400 cho luật nghiệp vụ, 422 cho "
            "sai kiểu. Parser phải chịu được cả hai.\n\n"
            "Đây là chỗ DUY NHẤT người dùng gõ chữ tự do vào prompt của endpoint "
            "này. Service làm sạch nó và bọc trong thẻ có nonce trước khi gửi đi, "
            "nhưng vẫn nên coi mọi thứ đi ra là nội dung người dùng gây ảnh hưởng "
            "được."
        ),
        examples=["tôi muốn học từ về công việc"],
    )
    allowed_deck_ids: list[int] = Field(
        description=(
            "Phạm vi bộ thẻ. Rỗng → **400 `INVALID_SCOPE`**, không bao giờ có nghĩa "
            '"không lọc".\n\n'
            "Ở đây nó làm HAI việc, khác `/vocab/extract` chỉ làm một: (1) giới hạn "
            "những thẻ mà service đọc để dựng danh sách tránh, (2) quyết định cờ "
            "`already_in_deck`. Service không bao giờ đọc thẻ ngoài danh sách này, "
            "kể cả chỉ để quyết định KHÔNG sinh ra từ gì.\n\n"
            "Nó **không** lọc đầu ra: kết quả là từ mới, không phải thẻ có sẵn."
        ),
        examples=[[1, 2, 3, 4]],
    )
    level: Literal["A1", "A2", "B1", "B2", "C1", "C2"] | None = Field(
        default=None,
        description=(
            "Trình độ nhắm tới theo khung CEFR. Bỏ trống là không ràng buộc.\n\n"
            "**Đây là gợi ý cho model, KHÔNG phải bảo đảm.** Service không có danh "
            "sách từ theo CEFR nên không kiểm lại được. Đừng dựng giao diện hứa hẹn "
            '"từ vựng trình độ A1" như một sự thật đã kiểm chứng.\n\n'
            "Giá trị ngoài sáu mức trả **422** — đó là lỗi kiểu, khác hình dạng với "
            "400 của `topic` quá dài."
        ),
        examples=["B2"],
    )
    count: int = Field(
        default=5,
        ge=1,
        le=6,
        description=(
            "Số thẻ muốn sinh. Trần là ràng buộc NGÂN SÁCH TOKEN, không phải con số "
            "tuỳ tiện — xem mô tả endpoint.\n\n"
            "Model có thể trả ít hơn nếu chủ đề quá hẹp, và đó là hành vi đúng: thà "
            "bốn từ đúng chủ đề còn hơn tám từ gượng ép. Ngoài khoảng trả **422**."
        ),
        examples=[6],
    )
    exclude_words: list[str] = Field(
        default_factory=list,
        description=(
            'Những từ ĐỪNG sinh lại. Đây là cách làm nút "thêm từ nữa" mà không '
            "cần service lưu trạng thái: giao diện gửi lại chính những từ nó vừa "
            "hiện cho người dùng, và lượt sau sẽ ra từ khác.\n\n"
            "Client giữ trạng thái, service không lưu gì — đúng cách `/chat` làm với "
            "`history`, và đúng ranh giới SPEC mục 5.1.\n\n"
            "Được ưu tiên hơn danh sách tránh mà service tự tìm được, vì đây là thứ "
            "người dùng VỪA nhìn thấy. Tổng hai nguồn bị cắt ở 40 mục; từ nào sai "
            "định dạng bị bỏ lặng lẽ và đếm ở `stats.avoid_list_dropped`."
        ),
        examples=[["itinerary", "layover"]],
    )


class VocabGenerateStats(BaseModel):
    topic_chars: int = Field(examples=[27])
    level: str | None = Field(
        description="Trình độ đã gửi, `null` nếu bỏ trống. Ghi lại để đối chiếu log.",
        examples=["B2"],
    )
    requested: int = Field(
        description="Số thẻ đã xin. So với `len(cards)` để biết model trả thiếu bao nhiêu.",
        examples=[6],
    )
    avoid_list_size: int = Field(
        description=(
            "Số từ đã đưa vào prompt để model tránh, gộp cả `exclude_words` lẫn phần "
            "service tự tìm trong phạm vi.\n\n"
            "Bằng `0` nghĩa là model không được cảnh báo gì — hoặc chỉ mục còn rỗng, "
            "hoặc phạm vi deck không có thẻ nào. Khi đó khả năng ra từ trùng cao hẳn "
            "lên, dù cờ `already_in_deck` vẫn đúng."
        ),
        examples=[18],
    )
    avoid_list_dropped: int = Field(
        description=(
            "Số mục bị loại khỏi danh sách tránh vì sai định dạng.\n\n"
            "Khác `0` ở môi trường thật nghĩa là một trong hai: dữ liệu đồng bộ về "
            "có `word` không phải là từ (lỗi chất lượng dữ liệu đáng biết), hoặc có "
            "người cố tình dựng một thẻ để tấn công prompt này. Cả hai đều nên nhìn "
            "thấy được."
        ),
        examples=[0],
    )
    returned_by_llm: int = Field(
        description=(
            "Số mục model trả về TRƯỚC mọi bộ lọc.\n\n"
            "Khác `/vocab/extract`: ở đây `0` **không** phải câu trả lời hợp lệ. "
            '"Chủ đề của bạn không có từ vựng nào" gần như không bao giờ đúng, nên '
            "`0` nghĩa là model từ chối hoặc trả rác — service trả **503**, không "
            "trả 200 rỗng."
        ),
        examples=[6],
    )
    dropped_unsafe: int = Field(
        description=(
            "Bị loại vì độ dài, bộ ký tự, ký tự vô hình, hay sai ngôn ngữ (nghĩa "
            "tiếng Việt mà không có dấu, câu tiếng Anh mà lại có dấu tiếng Việt)."
        ),
        examples=[0],
    )
    dropped_incoherent: int = Field(
        description=(
            "Bị loại vì câu ví dụ không thật sự dùng chính từ đó, hoặc không phải "
            "một câu hoàn chỉnh.\n\n"
            "Đây là phép kiểm CHẤT LƯỢNG, không phải phép kiểm an toàn. Nó bắt model "
            "cẩu thả, không bắt được kẻ tấn công. Cũng không nhận ra động từ bất quy "
            "tắc (`give` trong câu chia thành `gave`), nên thỉnh thoảng nó bỏ nhầm "
            "một thẻ đúng — hậu quả là mất một từ, không phải lọt một thẻ sai."
        ),
        examples=[1],
    )
    dropped_duplicate_in_batch: int = Field(
        description=(
            "Model trả cùng một từ hai lần trong một lượt. Với chủ đề hẹp đây là lỗi "
            "thường gặp, khác hẳn `/vocab/extract` nơi hai ứng viên lấy từ một đoạn "
            "văn tự nhiên đã khác nhau.\n\n"
            "Chỉ khử theo `word` đã chuẩn hoá, KHÔNG khử theo gốc từ: `manage`, "
            "`manager`, `management` là ba thẻ chính đáng."
        ),
        examples=[0],
    )
    already_in_deck_count: int = Field(
        description=(
            "Số thẻ trả về mà người học đã có. Khác `0` nghĩa là model bỏ qua danh "
            "sách tránh — vẫn đúng, không phải lỗi: danh sách tránh là lời khuyên, "
            "`already_in_deck` mới là luật."
        ),
        examples=[1],
    )
    dedup_checked: bool = Field(
        description=(
            "`false` nghĩa là chỉ mục đang RỖNG nên mọi cờ `already_in_deck` đều vô "
            "nghĩa, và danh sách tránh cũng rỗng theo. Xảy ra trong cửa sổ sau khi "
            "container khởi động lại mà đồng bộ đầu tiên chưa xong — `/readyz` đã "
            "trả 200 từ trước đó. Luôn kiểm trường này trước khi tin các cờ."
        ),
        examples=[True],
    )
    llm_calls: int = Field(
        description="Luôn bằng 1. Endpoint này không thử lại — xem mô tả endpoint.",
        examples=[1],
    )
    prompt_tokens: int = Field(examples=[1042])
    completion_tokens: int = Field(examples=[1876])
    latency_ms: int = Field(examples=[6210])


class VocabGenerateResponse(BaseModel):
    topic_understood: str | None = Field(
        description=(
            "Chủ đề mà model hiểu, viết bằng tiếng Việt. Hiện lại cho người dùng để "
            "họ biết ngay là mình bị hiểu sai, thay vì phải đọc hết sáu thẻ mới nhận "
            "ra.\n\n"
            "`null` khi model không trả trường này hoặc trả một giá trị không qua "
            "được chốt chặn ký tự. Khi đó cứ hiện lại nguyên chủ đề người dùng gõ."
        ),
        examples=["Công việc và nơi làm việc"],
    )
    cards: list[VocabCandidate] = Field(
        description=(
            "Thẻ đã dựng sẵn đủ trường, đúng shape để backend lưu thẳng.\n\n"
            "**Mọi trường ở đây đều do model bịa ra.** Service kiểm được hình dạng — "
            "độ dài, bộ ký tự, ngôn ngữ, câu ví dụ có dùng đúng từ không — nhưng "
            "KHÔNG kiểm được và không thể kiểm được: nghĩa tiếng Việt có đúng không, "
            "phiên âm có thật không, từ đó có tồn tại trong tiếng Anh không, có đúng "
            "trình độ đã xin không.\n\n"
            "Đừng trình bày mấy thẻ này như đã được kiểm chứng. Bước người dùng tick "
            "chọn là lần soát nội dung duy nhất mà chúng sẽ có."
        )
    )
    stats: VocabGenerateStats


# ---------------------------------------------------------------
# M10 — POST /internal/v1/vocab/lookup
# ---------------------------------------------------------------

VI_DU_VOCAB_LOOKUP: dict = {
    "tu_moi": {
        "summary": "Từ mới — đi đường AI",
        "description": (
            "`donut` không có trong bộ thẻ mẫu nên đi đường `AI`. Đây là ca hay gặp "
            "nhất khi người dùng bấm nút tra lúc soạn thẻ."
        ),
        "value": {"word": "donut", "allowed_deck_ids": [1, 2, 3, 4]},
    },
    "tu_da_co": {
        "summary": "Từ đã có trong bộ thẻ — 0 token",
        "description": (
            "`resilient` là thẻ 101 của bộ thẻ mẫu. Trả về `source: YOUR_DECK`, "
            "**không gọi LLM**, và cảnh báo luôn là người dùng sắp tạo thẻ trùng."
        ),
        "value": {"word": "resilient", "allowed_deck_ids": [1, 2, 3, 4]},
    },
    "boi_den_co_ngu_canh": {
        "summary": "Bôi đen một từ trong câu — có ngữ cảnh",
        "description": (
            "`context` là câu người dùng đang đọc. Nó quyết định lấy nghĩa nào của "
            "một từ nhiều nghĩa: `bank` ở đây là bờ sông, không phải ngân hàng."
        ),
        "value": {
            "word": "bank",
            "context": "They sat on the river bank and watched the boats go by.",
            "allowed_deck_ids": [1, 2, 3, 4],
        },
    },
    "go_sai_chinh_ta": {
        "summary": "Gõ sai chính tả — found: false kèm gợi ý",
        "description": (
            "Trả **200** với `found: false`, `card: null`, và `suggestion` là từ "
            "đúng nếu đoán được. KHÔNG bịa nghĩa cho một từ không tồn tại."
        ),
        "value": {"word": "recieve", "allowed_deck_ids": [1, 2, 3, 4]},
    },
    "khong_gui_pham_vi": {
        "summary": "Không gửi allowed_deck_ids — vẫn tra được",
        "description": (
            "`allowed_deck_ids` là TUỲ CHỌN ở endpoint này. Bỏ nó thì lượt tra vẫn "
            "chạy qua cache/AI bình thường, chỉ mất hai việc phụ thuộc phạm vi: "
            "không bao giờ trả `source: YOUR_DECK` và `already_in_deck` luôn là "
            "`false`. Hợp lệ khi tra từ ở màn hình không gắn với deck nào."
        ),
        "value": {"word": "donut"},
    },
}


class VocabLookupRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [vi_du["value"] for vi_du in VI_DU_VOCAB_LOOKUP.values()]}
    )

    word: str = Field(
        description=(
            "Từ cần tra. Tối đa **64 ký tự**, và sau khi chuẩn hoá phải là một từ "
            "hoặc cụm nhiều nhất ba từ, chỉ chữ cái a-z (cho phép dấu nối và dấu "
            "nháy đơn ở giữa).\n\n"
            'Service tự chuẩn hoá trước khi tra: `"Donuts "` thành `"donuts"`, và '
            'model được yêu cầu trả về dạng từ điển `"donut"`. Vì vậy `card.word` '
            "**có thể khác** chuỗi bạn gửi lên — đó là chủ ý, thẻ từ vựng cần dạng gốc.\n\n"
            "Chuỗi không thể là một từ (có chữ số, dấu chấm, quá bốn từ) trả "
            "**400 `INVALID_REQUEST`** mà **không tốn token nào**."
        ),
        examples=["donut"],
    )
    context: str = Field(
        default="",
        description=(
            "Câu chứa từ đó, khi người dùng bôi đen một từ trong lúc đọc. Tối đa "
            "**300 ký tự**, vượt thì bị cắt chứ không báo lỗi — ngữ cảnh là thứ "
            "phụ trợ, cắt bớt vẫn dùng được, khác `word` là thứ bắt buộc.\n\n"
            "Có ngữ cảnh thì model chọn nghĩa hợp với câu đó: `bank` trong câu về "
            "dòng sông ra `bờ sông`, trong câu về tiền ra `ngân hàng`. Bỏ trống thì "
            "model lấy nghĩa thông dụng nhất.\n\n"
            "**Ngữ cảnh nằm trong khoá cache**, nên cùng một từ với hai câu khác "
            "nhau là hai lượt gọi khác nhau."
        ),
        examples=["They sat on the river bank and watched the boats go by."],
    )
    allowed_deck_ids: list[int] | None = Field(
        default=None,
        description=(
            "Phạm vi bộ thẻ — **tuỳ chọn**. Gửi `null` hoặc bỏ hẳn field vẫn tra "
            "được bình thường qua cache/AI; chỉ mất hai việc phụ thuộc phạm vi: "
            "không bao giờ có `source: YOUR_DECK` và `already_in_deck` luôn là "
            "`false`.\n\n"
            "Gửi DANH SÁCH RỖNG `[]` thì khác: đó là khai báo phạm vi bằng không "
            "→ **400 `INVALID_SCOPE`**, giống mọi endpoint khác. Cách rẻ nhất để "
            "tắt kiểm trùng là bỏ field, đừng gửi mảng rỗng.\n\n"
            "Khi có mặt, nó dùng cho một việc duy nhất: kiểm xem người dùng **đã "
            "có** từ này chưa. Có rồi thì trả thẳng nội dung thẻ đó, "
            "`source: YOUR_DECK`, **0 token** — và giao diện nên báo ngay là họ "
            "sắp tạo thẻ trùng."
        ),
        examples=[[1, 2, 3, 4]],
    )


class VocabLookupStats(BaseModel):
    source: Literal["YOUR_DECK", "CACHE", "AI"] = Field(
        description=(
            "Câu trả lời này đến từ đâu.\n\n"
            "| | Token | Nghĩa |\n"
            "|---|---|---|\n"
            "| `YOUR_DECK` | **0** | Từ đã có trong `allowed_deck_ids`, trả nội dung thẻ đó |\n"
            "| `CACHE` | **0** | Đã có người tra từ này (cùng ngữ cảnh) gần đây |\n"
            "| `AI` | ~2.000 | Gọi LLM thật |\n\n"
            "Theo dõi tỉ lệ `AI` để biết nút tra có đang đốt ngân sách không."
        ),
        examples=["AI"],
    )
    word_chars: int = Field(examples=[5])
    context_chars: int = Field(examples=[0])
    llm_calls: int = Field(
        description="`0` với `YOUR_DECK` và `CACHE`, `1` với `AI`. Không bao giờ lớn hơn 1.",
        examples=[1],
    )
    cache_size: int = Field(
        description="Số mục đang nằm trong cache tra từ. Trần 2.000, đuổi theo LRU.",
        examples=[137],
    )
    prompt_tokens: int = Field(examples=[512])
    completion_tokens: int = Field(examples=[734])
    latency_ms: int = Field(examples=[1840])


class VocabLookupResponse(BaseModel):
    source: Literal["YOUR_DECK", "CACHE", "AI"] = Field(
        description="Lặp lại `stats.source` ở tầng ngoài cho giao diện tiện đọc.",
        examples=["AI"],
    )
    found: bool = Field(
        description=(
            "`false` nghĩa là **đây không phải một từ tiếng Anh có thật**, và khi đó "
            "`card` là `null`.\n\n"
            "Vẫn là **200**, không phải lỗi: câu hỏi đã được trả lời, câu trả lời là "
            '"không có từ này". Giao diện nên hiện đúng như vậy rồi để người dùng '
            "tự điền, thay vì đưa cho họ một thẻ trông hợp lệ cho một từ không tồn tại.\n\n"
            "Service **không kiểm chứng được** cờ này — repo không có từ điển tiếng "
            "Anh. Nó là lời của model. Nhưng hỏi thẳng vẫn tốt hơn nhiều so với để "
            "model tự bịa một nghĩa nghe rất thật."
        ),
        examples=[True],
    )
    suggestion: str | None = Field(
        default=None,
        description=(
            "Từ đúng, khi `found` là `false` mà model đoán được người dùng định gõ "
            "gì: `recieve` → `receive`. `null` khi không đoán được.\n\n"
            'Giao diện nên hiện dạng "Ý bạn là **receive**?" kèm nút tra lại.'
        ),
        examples=["receive"],
    )
    card: VocabCandidate | None = Field(
        default=None,
        description=(
            "Thẻ đã dựng sẵn đủ trường, `null` khi `found` là `false`.\n\n"
            "Với `source: YOUR_DECK` đây là **nội dung thẻ có thật của người dùng**, "
            "kèm `already_in_deck: true` và `existing_card_id` — đừng lưu lại, hãy "
            "mở thẻ cũ ra.\n\n"
            "Với `source: AI` hoặc `CACHE` thì **mọi trường đều do model sinh ra**. "
            "Service kiểm được hình dạng (độ dài, bộ ký tự, ngôn ngữ, câu ví dụ có "
            "dùng đúng từ không) nhưng không kiểm được nghĩa có đúng hay phiên âm có "
            "thật. Người dùng vẫn phải xem trước khi lưu."
        ),
    )
    stats: VocabLookupStats
