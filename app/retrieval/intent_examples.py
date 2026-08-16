"""
Câu mẫu cho từng intent, embed sẵn lúc khởi động để tính centroid.

`OUT_OF_SCOPE` ở đây là một LỚP THẬT có câu mẫu riêng, không phải "cái còn lại
khi điểm dưới ngưỡng". Lý do ở docs/M0_FINDINGS.md muc 2.5: hai câu hỏi hoàn
toàn không liên quan vẫn đạt cosine ~0.85 khi cùng mang prefix `query: `, nên
mọi ngưỡng tuyệt đối đều vô dụng. So sánh TƯƠNG ĐỐI giữa các lớp thì miễn
nhiễm với chuyện đó.

Viết chủ yếu bằng tiếng Việt vì người dùng là người Việt, xen vài câu tiếng
Anh cho các trường hợp gõ lẫn.
"""

from app.schemas.chat import Intent

INTENT_EXAMPLES: dict[Intent, list[str]] = {
    Intent.VOCAB_LOOKUP: [
        "resilient nghĩa là gì",
        "từ deadline có nghĩa gì",
        "cho tôi biết nghĩa của từ meticulous",
        "streamline là gì vậy",
        "nghĩa tiếng Việt của appraisal",
        "từ nào chỉ cảm giác lo lắng",
        "từ nào nói về người làm việc cẩn thận",
        "có từ nào nghĩa là chăm chỉ không",
        "phát âm của từ curriculum thế nào",
        "từ loại của từ delegate là gì",
        "giải thích từ stakeholder giúp tôi",
        "khí thải tiếng anh là gì",
        "what does redundant mean",
        "từ này thuộc loại từ gì",
        "định nghĩa của onboarding",
        "từ emission dùng trong trường hợp nào",
        "khi nào thì dùng từ appraisal",
        "nói rõ hơn về từ procurement cho tôi",
    ],
    Intent.EXAMPLE_REQUEST: [
        "đặt câu với từ resilient",
        "cho tôi ví dụ với từ này",
        "cho một câu ví dụ dùng deadline",
        "ví dụ trong ngữ cảnh công sở",
        "dùng từ này trong câu như thế nào",
        "cho vài câu ví dụ thực tế",
        "đặt câu ví dụ giúp tôi",
        "cho ví dụ khác đi",
        "viết một câu có từ diligent",
        "give me an example sentence",
        "cho tôi thêm ví dụ nữa",
        "áp dụng từ này vào câu thế nào",
        "ví dụ khi nói chuyện với sếp",
    ],
    Intent.TRANSLATE: [
        "dịch câu này sang tiếng Việt",
        "dịch giúp tôi đoạn văn sau",
        "dịch sang tiếng Anh giúp mình",
        "câu này dịch thế nào",
        "translate this sentence to Vietnamese",
        "dịch nguyên câu bên dưới",
        "giúp tôi dịch email này",
        "dịch đoạn hội thoại này",
        "chuyển câu sau sang tiếng Anh",
        "dịch giùm mình với",
    ],
    Intent.GRAMMAR_QA: [
        "thì hiện tại hoàn thành dùng khi nào",
        "phân biệt thì quá khứ đơn và quá khứ tiếp diễn",
        "câu điều kiện loại 2 dùng thế nào",
        "khi nào dùng mệnh đề quan hệ",
        "cách dùng giới từ in on at",
        "câu bị động được thành lập ra sao",
        "phân biệt a và an",
        "khi nào thêm s vào động từ",
        "ngữ pháp câu tường thuật",
        "cấu trúc so sánh hơn viết thế nào",
        "danh động từ và động từ nguyên mẫu khác nhau chỗ nào",
        "cách chia động từ bất quy tắc",
        "modal verb dùng ra sao",
    ],
    Intent.QUIZ_REQUEST: [
        "tạo cho tôi một bài kiểm tra",
        "cho tôi làm quiz 10 câu",
        "kiểm tra kiến thức của tôi đi",
        "tạo bài test từ vựng",
        "cho tôi ôn tập bộ thẻ này",
        "làm bài trắc nghiệm nào",
        "sinh câu hỏi kiểm tra giúp tôi",
        "tôi muốn luyện tập với bộ thẻ",
        "cho vài câu hỏi để tôi tự kiểm tra",
        "tạo quiz điền từ",
        "cho tôi thi thử",
    ],
    Intent.SMALLTALK: [
        "xin chào",
        "chào bạn",
        "hello bạn khoẻ không",
        "cảm ơn bạn nhiều",
        "bạn là ai vậy",
        "bạn giúp được gì cho tôi",
        "tạm biệt nhé",
        "cảm ơn, vậy là đủ rồi",
        "bạn tên gì",
        "ok cảm ơn",
        "chào buổi sáng",
    ],
    Intent.OUT_OF_SCOPE: [
        "hôm nay thời tiết thế nào",
        "kết quả trận bóng đá tối qua ra sao",
        "chỉ tôi cách nấu phở",
        "giá bitcoin hôm nay bao nhiêu",
        "viết giúp tôi một đoạn code Python",
        "tình hình chính trị thế giới hiện nay",
        "gợi ý phim hay để xem cuối tuần",
        "cách giảm cân nhanh nhất",
        "đường từ Hà Nội vào Sài Gòn bao xa",
        "kể cho tôi một câu chuyện cười",
        "nên mua điện thoại nào",
        "giải phương trình bậc hai giúp tôi",
        "tôi bị đau đầu nên uống thuốc gì",
    ],
}
