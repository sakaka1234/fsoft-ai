"""
Schema cho POST /internal/v1/search. SPEC muc 8.3.

Mọi `description` trong file này hiện thẳng lên /docs. Người đọc là lập trình
viên backend Java chưa từng đụng vào RAG hay embedding, nên chỗ nào dễ hiểu sai
thì nói luôn hậu quả của việc hiểu sai — viết "trường này là ID của deck" thì
không cứu được ai.

Ở đây CHỈ có metadata: không trường nào đổi tên, đổi kiểu, đổi mặc định hay đổi
thứ tự. Sửa mấy thứ đó là đổi hợp đồng với backend Java.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.chat import MatchType

# Nguồn duy nhất của các ví dụ request. Router truyền dict này vào
# `Body(openapi_examples=...)` để Swagger UI dựng được menu thả xuống trong ô
# "Request body"; `model_config` bên dưới rút lại phần `value` cho tab Schema.
# Sửa ví dụ thì sửa đúng một chỗ này.
#
# Mọi giá trị đều lấy từ lần chạy thật trên dữ liệu mẫu 24 thẻ
# (`AI_SOURCE_MODE=fixture`), bấm Execute là ra ngay, không phải sửa gì.
VI_DU_TIM_KIEM: dict = {
    "tra_dung_tu_tieng_anh": {
        "summary": "Tra đúng một từ tiếng Anh — ra 1 kết quả EXACT, score 1.0",
        "description": (
            "Bấm Execute: đúng **1** kết quả, thẻ `101 resilient` với "
            '`match_type: "EXACT"` và `score: 1.0` (điểm ghim cứng, không đi qua công '
            "thức RRF), `candidate_count: 1`. Tầng khớp chính xác đã trả lời dứt điểm "
            "nên hai tầng còn lại không có gì để thêm. Tốn **0 token LLM**."
        ),
        "value": {"query": "resilient", "allowed_deck_ids": [1, 2, 3, 4], "top_k": 5},
    },
    "cau_hoi_tieng_viet": {
        "summary": "Người dùng quên từ, chỉ tả được nghĩa — ra apprehensive + anxious (HYBRID)",
        "description": (
            "Bấm Execute: **4** kết quả, `candidate_count: 4`. Hai thẻ đầu "
            "`102 apprehensive` và `108 anxious` mang `HYBRID` (cả BM25 lẫn tầng vector "
            "đều tìm thấy), điểm RRF quanh `0.032`. Hai thẻ sau là nhiễu `LEXICAL`: "
            '`105 empathetic` lọt vào vì nghĩa của nó cũng chứa chữ "cảm", '
            '`106 impulsive` vì chứa chữ "trước". Đây chính là lý do nên quyết định '
            "hiển thị theo `match_type` chứ đừng lọc theo `score`."
        ),
        "value": {
            "query": "từ nào diễn tả cảm giác lo lắng trước kỳ thi",
            "allowed_deck_ids": [1],
            "top_k": 5,
        },
    },
    "ngoai_pham_vi_cho_phep": {
        "summary": "Từ có thật nhưng nằm ngoài quyền đọc — results rỗng",
        "description": (
            "Bấm Execute: `results: []`, `candidate_count: 0`, và **vẫn là HTTP 200**. "
            "Thẻ `303 deforestation` có tồn tại trong index nhưng nằm ở deck 3, mà "
            "request chỉ cho phép deck 1 và 2 — service không được phép nhìn thấy nó. "
            "Đổi `allowed_deck_ids` thành `[1, 2, 3]` rồi Execute lại là thấy nó ngay: "
            "cách kiểm tra ranh giới bảo mật trong 10 giây."
        ),
        "value": {"query": "deforestation", "allowed_deck_ids": [1, 2], "top_k": 5},
    },
    "thu_hep_bang_scope_deck_id": {
        "summary": "Người học đang mở một bộ thẻ — chỉ tìm trong deck 2",
        "description": (
            "Bấm Execute: service chỉ tìm trong deck 2 dù danh sách cho phép có 4 deck, "
            'trả về thẻ `201 deadline` với `match_type: "EXACT"`, `score: 1.0`. Đổi '
            "`scope_deck_id` thành `9` (không nằm trong `allowed_deck_ids`) rồi Execute "
            "lại sẽ nhận `400 INVALID_SCOPE` — trường này chỉ thu hẹp được, không bao "
            "giờ mở rộng được phạm vi."
        ),
        "value": {
            "query": "deadline",
            "allowed_deck_ids": [1, 2, 3, 4],
            "top_k": 5,
            "scope_deck_id": 2,
        },
    },
    "cau_ngoai_chu_de": {
        "summary": "Câu hỏi chẳng dính gì tới bộ thẻ — results rỗng vì không thẻ nào vượt ngưỡng",
        "description": (
            "Bấm Execute: `results: []`, `candidate_count: 0`, dù đã cho phép cả 4 deck "
            "(24 thẻ). Không thẻ nào vượt ngưỡng liên quan `AI_MIN_SCORE = 0.83` nên "
            "service chọn im lặng thay vì bịa ra 5 từ ngẫu nhiên trông rất thuyết phục. "
            "**Tăng `top_k` không làm nó hết rỗng.**"
        ),
        "value": {"query": "cách nấu phở bò", "allowed_deck_ids": [1, 2, 3, 4], "top_k": 5},
    },
}


class SearchRequest(BaseModel):
    # Dùng lại chính `VI_DU_TIM_KIEM` để tab "Schema" và menu thả xuống ở ô
    # "Request body" không bao giờ lệch nhau. Ví dụ ĐẦU vẫn phải là ví dụ chạy
    # được ngay với dữ liệu mẫu 24 thẻ: nó là cái Swagger điền sẵn trước khi
    # người thử kịp mở menu chọn.
    model_config = ConfigDict(
        json_schema_extra={"examples": [v["value"] for v in VI_DU_TIM_KIEM.values()]}
    )

    query: str = Field(
        description=(
            "Câu người dùng gõ, tiếng Việt hay tiếng Anh đều được, **không cần tiền xử "
            "lý gì cả**. Service tự tách từ tiếng Anh cho tầng khớp chính xác, tự bỏ hư "
            "từ (`từ`, `nào`, `nghĩa`, `là`, `gì`...) trước khi đưa vào BM25, và tự nối "
            "tiền tố `query: ` mà model E5 đòi hỏi.\n\n"
            "**Đừng tự nối `query: ` ở phía backend.** Nối hai lần thì vector lệch đi "
            "nhưng không có lỗi nào bắn ra — triệu chứng duy nhất là kết quả tự nhiên "
            "kém hẳn, rất khó lần ra.\n\n"
            "Hạn chế đã biết: câu thuần tiếng Việt viết **không dấu** hay trả về rỗng. "
            "Đo thử trên dữ liệu mẫu: `từ nào diễn tả cảm giác lo lắng trước kỳ thi` ra 4 "
            "thẻ, còn `tu nao chi cam giac lo lang` ra `[]` — bỏ dấu đi thì điểm cosine "
            "tụt xuống dưới ngưỡng liên quan, mà riêng tầng BM25 thì không tự tạo ra kết "
            "quả (xem `match_type`). Câu có chứa từ tiếng Anh thì vẫn chạy, nhờ tầng khớp "
            "chính xác."
        ),
        examples=["resilient", "từ nào diễn tả cảm giác lo lắng trước kỳ thi"],
    )
    allowed_deck_ids: list[int] = Field(
        description=(
            "Danh sách deck mà **người dùng hiện tại** được phép đọc, do backend Java tự "
            "tính từ quyền của họ. Service này không biết người dùng là ai: không có "
            "`profileId`, không có phiên đăng nhập, không có bảng phân quyền. Nó tin "
            "tuyệt đối vào danh sách bạn gửi, nên **đây là toàn bộ ranh giới bảo mật**.\n\n"
            '- Rỗng `[]` → **400 `INVALID_SCOPE`**, chứ KHÔNG phải "không lọc". Fail '
            'đóng là cố ý: một biến `List<Long>` quên gán mà bị hiểu thành "cho xem tất" '
            "là lộ sạch bộ thẻ riêng tư của mọi người dùng.\n"
            "- Thừa một deck của người khác thì thẻ của người đó hiện ra trong kết quả, và "
            "service không có cách nào phát hiện. Lỗi này nằm hoàn toàn ở phía người gọi.\n"
            "- Deck không tồn tại (ví dụ `[999]`) không phải lỗi: nó chỉ đơn giản không "
            "khớp thẻ nào, `results` rỗng.\n\n"
            "Dữ liệu mẫu có deck `1, 2, 3, 4`."
        ),
        examples=[[1, 2, 3, 4]],
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=50,
        description=(
            "Số thẻ **tối đa** trả về. Đây là chặn trên, không phải số lượng bảo đảm — "
            "cổng lọc liên quan hoàn toàn có thể cắt xuống còn 1 hoặc 0 thẻ.\n\n"
            "Vì vậy **tăng `top_k` không cứu được kết quả rỗng**: rỗng nghĩa là không thẻ "
            'nào vượt ngưỡng liên quan, không phải "xin ít quá". Xin 50 thẻ trong khi '
            "chỉ có 2 thẻ đủ liên quan thì vẫn nhận đúng 2.\n\n"
            "Ngoài khoảng 1–50 sẽ bị FastAPI chặn ở tầng kiểm dữ liệu và trả **422**, có "
            'dạng `{"detail": [...]}` — khác hẳn dạng `{"error": {...}}` của các lỗi '
            "nghiệp vụ. Đừng parse chung một chỗ.\n\n"
            "Gợi ý: giao diện gợi ý nhanh dùng 3–5, còn khi soi lỗi retrieval thì để 10–20 "
            "cho dễ thấy thứ hạng."
        ),
        examples=[5],
    )
    scope_deck_id: int | None = Field(
        default=None,
        description=(
            "**Thu hẹp thêm** bên trong phạm vi đã cho phép. Nó không bao giờ mở rộng "
            "được phạm vi — đó là khác biệt duy nhất nhưng quan trọng nhất so với "
            "`allowed_deck_ids`.\n\n"
            "| Giá trị | Service làm gì |\n"
            "|---|---|\n"
            "| `null` (mặc định) | Tìm trên toàn bộ `allowed_deck_ids` |\n"
            "| Có, và nằm trong `allowed_deck_ids` | Chỉ tìm trong đúng deck đó |\n"
            "| Có, nhưng KHÔNG nằm trong danh sách | **400 `INVALID_SCOPE`** |\n\n"
            "Trường hợp thứ ba là cố ý ồn ào: nếu service âm thầm bỏ qua giá trị sai, "
            "backend sẽ tưởng đang tìm trong một deck trong khi thực tế đang tìm khắp "
            "mọi deck của người dùng.\n\n"
            "Dùng khi người học đang mở một bộ thẻ cụ thể: vẫn gửi đủ `allowed_deck_ids` "
            "theo quyền, cộng thêm `scope_deck_id` là deck đang mở. **Đừng bao giờ dùng "
            "nó thay cho `allowed_deck_ids`** — chỉ gửi `scope_deck_id` mà để danh sách "
            "cho phép rỗng thì nhận 400."
        ),
        examples=[2],
    )


class SearchResult(BaseModel):
    card_id: int = Field(
        description=(
            "ID thẻ đúng bằng ID mà backend Java đã gửi sang lúc đồng bộ. Ghép ngược về "
            "dữ liệu gốc (tiến độ SRS, lịch sử ôn) bằng chính ID này."
        ),
        examples=[102],
    )
    word: str = Field(
        description=(
            "Từ vựng, **chép nguyên văn từ thẻ**. Endpoint này không gọi LLM nên không có "
            "chữ nào do máy sinh ra: sai chính tả ở đây nghĩa là dữ liệu nguồn sai."
        ),
        examples=["apprehensive"],
    )
    meaning: str = Field(
        description="Nghĩa tiếng Việt, cũng chép nguyên văn từ thẻ.",
        examples=["lo lắng, e ngại về điều sắp xảy ra"],
    )
    deck_id: int = Field(
        description=(
            "Deck chứa thẻ. Luôn nằm trong `allowed_deck_ids` của request — nếu thấy giá "
            "trị lạ thì lỗi nằm ở danh sách bạn gửi lên, không phải ở service."
        ),
        examples=[1],
    )
    deck_title: str | None = Field(
        description=(
            "Tên bộ thẻ để hiển thị. `null` khi backend không gửi kèm `deckTitle` lúc đồng "
            "bộ — giao diện phải chịu được `null`, đừng nối chuỗi thẳng."
        ),
        examples=["TOEIC - Cảm xúc & Tính cách"],
    )
    score: float = Field(
        description=(
            "**Không phải xác suất, không phải phần trăm giống nhau, và không so sánh được "
            "giữa hai câu hỏi khác nhau.** Chỉ dùng đúng một việc: xếp thứ tự trong CÙNG "
            "một phản hồi (mà phản hồi thì đã sắp sẵn giảm dần rồi).\n\n"
            "Cách tính, xem `app/retrieval/hybrid.py`:\n\n"
            "- Thẻ `EXACT` bị **ghim cứng 1.0** và đẩy lên đầu, không đi qua công thức nào.\n"
            "- Còn lại là điểm **RRF** (Reciprocal Rank Fusion): `score = Σ 1/(60 + thứ "
            "hạng)` cộng trên hai danh sách lexical và semantic. Nên trần thực tế là "
            "`1/61 + 1/61 ≈ 0.0328` (đứng nhất ở cả hai tầng), còn thẻ chỉ một tầng tìm "
            "thấy thì quanh `0.0164`.\n\n"
            "Vì sao chỉ dùng THỨ HẠNG chứ không cộng điểm thô: cosine của E5 dồn hết vào "
            "dải 0.80–0.95, còn điểm BM25 chạy từ 0 tới vài chục — không có cách chuẩn hoá "
            "nào ổn định giữa hai thang đó.\n\n"
            'Hậu quả nếu hiểu nhầm: hiện "độ khớp 3%" cho người dùng, hoặc lọc '
            "`score > 0.5` rồi vứt sạch mọi kết quả trừ `EXACT`. **Muốn lọc thì lọc theo "
            "`match_type`.** Chênh lệch `0.0164` với `0.0161` chỉ là lệch một bậc thứ "
            "hạng, không phải chênh lệch chất lượng."
        ),
        examples=[0.0328],
    )
    match_type: MatchType = Field(
        description=(
            "Tầng nào tìm ra thẻ này. Đây mới là thứ đáng dùng để quyết định hiển thị.\n\n"
            "| Giá trị | Nghĩa | Nên làm gì |\n"
            "|---|---|---|\n"
            "| `EXACT` | Người dùng gõ **đúng một từ có trong bộ thẻ** (`resilient`, "
            "`deadline`). Khớp tuyệt đối trên trường `word` | Chắc chắn nhất, luôn ở đầu "
            "danh sách. Hiện thẳng nghĩa, không cần hỏi LLM |\n"
            "| `HYBRID` | **Cả hai tầng** BM25 và vector đều tìm thấy | Bằng chứng mạnh "
            "nhất trong nhóm không khớp chính xác. Hiện được ngay |\n"
            '| `SEMANTIC` | Chỉ tầng vector thấy, và đã vượt ngưỡng liên quan | Ca "gần '
            'nghĩa" — điển hình của câu hỏi tiếng Việt mô tả. Hiện được, nên kèm nghĩa để '
            "người dùng tự đối chiếu |\n"
            "| `LEXICAL` | Chỉ BM25 thấy trùng chữ trên `word + meaning + example_sentence` "
            "| Yếu nhất, thường là nhiễu. Nên xếp cuối hoặc bỏ hẳn |\n\n"
            'Ví dụ nhiễu có thật: hỏi *"từ nào diễn tả cảm giác lo lắng trước kỳ thi"* '
            "thì `empathetic` lọt vào với `LEXICAL` chỉ vì nghĩa của nó cũng chứa chữ "
            '"cảm", và `impulsive` lọt vào vì chứa chữ "trước".\n\n'
            "Một điều dễ bỏ sót: **riêng tầng BM25 không bao giờ tự tạo ra phản hồi**. "
            "Không có `EXACT` và cũng không có `SEMANTIC` nào thì service trả rỗng luôn, "
            "kể cả khi BM25 đang có hàng chục thẻ trùng chữ. Nên các dòng `LEXICAL` bạn "
            "thấy đều là ăn theo một kết quả thật khác."
        ),
        examples=["HYBRID"],
    )


class SearchResponse(BaseModel):
    results: list[SearchResult] = Field(
        description=(
            "Đã sắp xếp sẵn: `EXACT` trước, rồi tới điểm RRF giảm dần. Cứ hiện theo đúng "
            "thứ tự này, đừng sắp lại theo `score`.\n\n"
            "**Rỗng `[]` là câu trả lời hợp lệ**, không phải lỗi và không phải service "
            "hỏng. Nó nghĩa là: *bộ thẻ được phép xem không chứa câu trả lời*. Hai cửa ải "
            "trong `app/retrieval/hybrid.py` tạo ra chuyện đó:\n\n"
            "1. Tầng vector bị chặn bởi ngưỡng `AI_MIN_SCORE = 0.83` trên điểm cosine. "
            "Ngưỡng này đo từ bộ 40 case ở `tests/eval/retrieval_golden.json` chứ không "
            "phỏng đoán: hai phân bố chồng lấn nhau (positive thấp nhất 0.8277, negative "
            "cao nhất 0.8297) nên 0.83 chấp nhận mất một positive yếu để chặn được năm "
            "negative.\n"
            "2. Không có thẻ khớp chính xác **và** không có thẻ nào qua được ngưỡng thì "
            "trả rỗng ngay, không đưa gì vào công thức trộn.\n\n"
            "Không có cổng lọc này thì mọi câu hỏi đều trả về đủ `top_k` thẻ — hỏi "
            '"deforestation" trong bộ TOEIC sẽ nhận 5 từ ngẫu nhiên trông rất thuyết '
            'phục. **Giao diện phải hiện "không tìm thấy trong bộ thẻ này", tuyệt đối '
            "đừng lấp chỗ trống bằng thẻ ngẫu nhiên.** Luồng chat cũng dựa đúng vào tín "
            'hiệu rỗng này để trả lời "chưa có trong bộ thẻ của bạn".'
        )
    )
    latency_ms: int = Field(
        description=(
            "Thời gian xử lý **bên trong service**: bấm giờ sau khi đã kiểm phạm vi deck "
            "và trạng thái sẵn sàng, dừng lúc dựng xong phản hồi. Gần như toàn bộ con số "
            "này là việc mã hoá câu hỏi thành vector — đo trên máy phát triển thì phần "
            "đó chiếm khoảng 96%, còn ba tầng tìm kiếm chạy trên RAM chỉ tốn dưới 1ms.\n\n"
            "KHÔNG gồm thời gian đi dây và xác thực token, nên số bạn đo ở phía Java sẽ "
            "luôn lớn hơn. Với dữ liệu mẫu 24 thẻ, giá trị thường thấy là 8–15ms và khá "
            "ổn định (câu càng dài thì càng về phía trên của dải) — không gọi mạng ra "
            "ngoài nên không có phương sai của nhà cung cấp LLM. Thấy con số vọt lên hàng "
            "trăm ms thì nghi máy chủ đang thiếu CPU."
        ),
        examples=[11],
    )
    candidate_count: int = Field(
        description=(
            "**Luôn bằng đúng `len(results)`** — cứ đọc `app/api/v1/search.py` là thấy nó "
            'được gán bằng số phần tử của chính danh sách trả về. Nó KHÔNG phải "số thẻ đã '
            'được xem xét" như cái tên gợi ý.\n\n'
            "Giữ lại vì SPEC muc 8.3 khai báo như vậy. Đừng dùng nó để đo độ phủ của "
            "retrieval, và đừng dựng cảnh báo khi thấy nó nhỏ — muốn biết retrieval xét "
            "bao nhiêu ứng viên thì phải xem log của service, không có trong phản hồi."
        ),
        examples=[2],
    )
