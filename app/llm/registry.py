"""
Nạp prompt từ file. SPEC muc 11.4 mục 4.

CẤM nối chuỗi prompt trong code Python. Lý do: prompt là thứ thay đổi nhiều
nhất và cần review kỹ nhất trong cả hệ thống. Nằm rải rác trong .py thì không
ai diff nổi, không ai biết bản nào đang chạy, và mỗi lần sửa là một lần rủi ro
chạm vào logic. Nằm trong file .txt có version thì đọc như đọc văn bản.

`tests/test_llm_client.py` có một test tĩnh quét toàn bộ app/ để bảo đảm luật
này không bị phá.
"""

import re
from functools import lru_cache
from pathlib import Path

from app.config import PROJECT_ROOT

PROMPTS_DIR = PROJECT_ROOT / "app" / "llm" / "prompts"

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


class PromptNotFound(Exception):
    pass


class PromptRegistry:
    def __init__(self, prompts_dir: Path | None = None) -> None:
        self._dir = prompts_dir or PROMPTS_DIR

    @lru_cache(maxsize=32)  # noqa: B019 - registry sống suốt vòng đời process
    def _load(self, name: str) -> str:
        path = self._dir / f"{name}.txt"

        if not path.is_file():
            raise PromptNotFound(f"Không tìm thấy prompt {name!r} tại {path}")

        return path.read_text(encoding="utf-8").strip()

    def render(self, name: str, **values: str) -> str:
        """
        Thay `{{ten_bien}}` bằng giá trị.

        Thiếu biến thì NÉM LỖI chứ không để nguyên placeholder. Một prompt gửi
        đi kèm chuỗi `{{context}}` nguyên xi sẽ không làm gì sập cả — nó chỉ
        khiến model trả lời sai một cách rất khó lần ra.
        """

        template = self._load(name)

        missing = {key for key in _PLACEHOLDER_RE.findall(template) if key not in values}

        if missing:
            raise KeyError(f"Prompt {name!r} thiếu biến: {sorted(missing)}")

        return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)

    def available(self) -> list[str]:
        return sorted(path.stem for path in self._dir.glob("*.txt"))
