"""
Kiểm tra câu hỏi do LLM sinh. SPEC muc 11.6.

BẮT BUỘC, không phải tuỳ chọn. LLM sẽ sinh dữ liệu sai — đây là điều chắc chắn
xảy ra chứ không phải rủi ro. JSON mode chỉ đảm bảo CÚ PHÁP hợp lệ, không đảm
bảo NỘI DUNG đúng.
"""

import re
from dataclasses import dataclass

from app.quiz.distractors import normalize_meaning
from app.schemas.quiz import QuestionType, QuizQuestion

BLANK = "______"
REQUIRED_OPTIONS = 4

# Chữ cái chỉ có trong bảng chữ tiếng Việt — dùng để khẳng định lời giải thích
# đúng là tiếng Việt chứ không phải model trả lời bằng tiếng Anh.
_VIETNAMESE_RE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    reason: str = ""


def validate_question(
    question: QuizQuestion, *, answer_word: str | None = None
) -> ValidationResult:
    if question.type == QuestionType.MATCHING:
        return _validate_matching(question)

    checks = [
        _check_option_count(question),
        _check_options_distinct(question),
        _check_correct_index(question),
        _check_explanation(question),
    ]

    if question.type == QuestionType.FILL_BLANK:
        checks.append(_check_blank(question))
        checks.append(_check_answer_not_leaked(question, answer_word))

    for result in checks:
        if not result.ok:
            return result

    return ValidationResult(ok=True)


def _check_option_count(question: QuizQuestion) -> ValidationResult:
    if len(question.options) != REQUIRED_OPTIONS:
        return ValidationResult(False, f"cần đúng {REQUIRED_OPTIONS} lựa chọn")

    return ValidationResult(True)


def _check_options_distinct(question: QuizQuestion) -> ValidationResult:
    """So sau khi chuẩn hoá khoảng trắng, hoa thường và dấu câu."""

    normalized = [normalize_meaning(option) for option in question.options]

    if any(not option for option in normalized):
        return ValidationResult(False, "có lựa chọn rỗng")

    if len(set(normalized)) != len(normalized):
        return ValidationResult(False, "có hai lựa chọn trùng nhau")

    return ValidationResult(True)


def _check_correct_index(question: QuizQuestion) -> ValidationResult:
    if question.correct_index is None or not 0 <= question.correct_index < REQUIRED_OPTIONS:
        return ValidationResult(False, "correct_index ngoài khoảng 0-3")

    return ValidationResult(True)


def _check_explanation(question: QuizQuestion) -> ValidationResult:
    if not question.explanation.strip():
        return ValidationResult(False, "thiếu giải thích")

    if not _VIETNAMESE_RE.search(question.explanation):
        return ValidationResult(False, "giải thích không phải tiếng Việt")

    return ValidationResult(True)


def _check_blank(question: QuizQuestion) -> ValidationResult:
    if BLANK not in question.prompt:
        return ValidationResult(False, f"câu điền từ phải chứa {BLANK}")

    return ValidationResult(True)


def _check_answer_not_leaked(question: QuizQuestion, answer_word: str | None) -> ValidationResult:
    """
    Từ đáp án không được xuất hiện nguyên dạng trong đề bài.

    Lỗi hay gặp nhất của LLM: sinh câu "She remained resilient despite ______"
    rồi đặt đáp án đúng là chính `resilient`.
    """

    if not answer_word:
        return ValidationResult(True)

    if re.search(rf"\b{re.escape(answer_word)}\b", question.prompt, re.IGNORECASE):
        return ValidationResult(False, f"đề bài lộ đáp án {answer_word!r}")

    return ValidationResult(True)


def _validate_matching(question: QuizQuestion) -> ValidationResult:
    if not question.matching or len(question.matching) < 2:
        return ValidationResult(False, "cần ít nhất 2 cặp nối")

    if len(question.options) != len(question.matching):
        return ValidationResult(False, "số nghĩa không khớp số từ")

    indexes = [pair.correct_option_index for pair in question.matching]

    if sorted(indexes) != list(range(len(question.options))):
        return ValidationResult(False, "ánh xạ nối không phải hoán vị hợp lệ")

    return ValidationResult(True)
