"""
Schema cho `POST /internal/v1/vocab/extract`. SPEC muc 8.5b.

Quy tắc viết mô tả ở đây giống mọi file schema khác: nói HẬU QUẢ CỦA VIỆC HIỂU
SAI, đừng nói lại tên trường. Viết "trường này là id của deck" thì không cứu
được ai.
"""

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
            "Dạng từ điển, chữ thường. CÓ THỂ khác dạng xuất hiện trong đoạn văn: "
            "văn bản có `running` thì thẻ ghi `run`, vì thẻ từ vựng cần dạng gốc. "
            "Đó là chủ ý, không phải lỗi."
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
            "**Đảm bảo có thật trong đoạn văn bạn gửi lên.** Service so khớp câu "
            "này với văn bản gốc sau khi bỏ qua khác biệt hình thức (nháy cong so "
            "với nháy thẳng, gạch dài so với gạch ngắn, khoảng trắng, hoa thường); "
            "không khớp thì **cả ứng viên bị loại**, không phải chỉ xoá trường này.\n\n"
            "Vì sao khắt khe: kết quả ở đây sẽ được lưu thành thẻ và chia sẻ được, "
            "nên đây là trường duy nhất LLM có thể dùng để đưa nội dung mới vào dữ "
            "liệu lưu trữ. Bắt buộc nó phải có sẵn trong văn bản là cách đóng đường "
            "đó lại. Số ứng viên bị loại nằm ở `stats.dropped_not_grounded`."
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
