"""
M0 — Kiểm tra Groq.

Chạy:
  Windows PowerShell:  $env:GROQ_API_KEY="gsk_..."; uv run python scripts/m0_groq.py
  Windows CMD:         set GROQ_API_KEY=gsk_... && uv run python scripts/m0_groq.py
  macOS / Linux:       GROQ_API_KEY=gsk_... uv run python scripts/m0_groq.py

Mục tiêu:
  1. Model nào còn sống? (Groq deprecate rất thường xuyên)
  2. Rate limit THẬT của account là bao nhiêu?
     -> Đọc thẳng từ header x-ratelimit-*, chính xác hơn mọi bài blog.
  3. Model nào viết tiếng Việt tốt nhất?
  4. JSON mode có hoạt động không? (M5 cần)

LƯU Ý: gói `openai` ở đây chỉ là HTTP client. Với base_url của Groq thì
toàn bộ request đi thẳng tới api.groq.com, không chạm OpenAI.

Chép toàn bộ output vào docs/M0_FINDINGS.md.
"""

import os
import sys
import time

# Console Windows mặc định dùng cp1252, không in được tiếng Việt có dấu.
# Thiếu đoạn này thì script chết khi in output tiếng Việt của model.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from openai import OpenAI

BASE_URL = "https://api.groq.com/openai/v1"

# Cập nhật danh sách này theo console.groq.com/docs/models
CANDIDATES = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-120b",
    "qwen/qwen3-32b",
]

PROMPT = (
    "Giải thích ngắn gọn bằng tiếng Việt cho người học TOEIC: "
    "từ 'resilient' nghĩa là gì, thuộc từ loại nào, và đặt một câu ví dụ "
    "trong ngữ cảnh công sở kèm bản dịch. Tối đa 100 từ."
)

api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    sys.exit(
        "Thiếu GROQ_API_KEY.\n"
        '  PowerShell: $env:GROQ_API_KEY="gsk_..."; uv run python scripts/m0_groq.py\n'
        "  CMD:        set GROQ_API_KEY=gsk_... && uv run python scripts/m0_groq.py\n"
        "  bash/zsh:   GROQ_API_KEY=gsk_... uv run python scripts/m0_groq.py"
    )

client = OpenAI(api_key=api_key, base_url=BASE_URL, max_retries=0, timeout=30.0)


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# ---------------------------------------------------------------- 1
section("1. Model nào Groq đang phục vụ?")

try:
    available = sorted(m.id for m in client.models.list().data)
    for m in available:
        print("   ", m)
except Exception as exc:
    print("Không liệt kê được:", exc)
    available = []

print()
for m in CANDIDATES:
    status = "CÒN SỐNG" if m in available else "KHÔNG THẤY - sửa lại CANDIDATES"
    print(f"  {m:<32} {status}")

# ---------------------------------------------------------------- 2
section("2. Rate limit thật và chất lượng tiếng Việt")

results = []

for model in CANDIDATES:
    if available and model not in available:
        continue

    print("\n" + "-" * 70)
    print(f"MODEL: {model}")
    print("-" * 70)

    try:
        t0 = time.perf_counter()
        raw = client.chat.completions.with_raw_response.create(
            model=model,
            messages=[{"role": "user", "content": PROMPT}],
            max_tokens=250,
            temperature=0.3,
        )
        elapsed = time.perf_counter() - t0
        resp = raw.parse()
        h = raw.headers

        # Đây là con số quan trọng nhất của cả M0.
        # Nó quyết định app phục vụ được bao nhiêu lượt chat mỗi phút.
        limit_tok = h.get("x-ratelimit-limit-tokens")
        remain_tok = h.get("x-ratelimit-remaining-tokens")
        limit_req = h.get("x-ratelimit-limit-requests")
        remain_req = h.get("x-ratelimit-remaining-requests")

        print(f"  độ trễ            : {elapsed:.2f}s")
        print(f"  TPM giới hạn      : {limit_tok}      <-- SỐ QUAN TRỌNG NHẤT")
        print(f"  TPM còn lại       : {remain_tok}")
        print(f"  RPM/RPD giới hạn  : {limit_req}")
        print(f"  RPM/RPD còn lại   : {remain_req}")
        print(f"  reset tokens      : {h.get('x-ratelimit-reset-tokens')}")
        print(
            f"  token dùng        : in={resp.usage.prompt_tokens} "
            f"out={resp.usage.completion_tokens}"
        )

        if limit_tok and str(limit_tok).isdigit():
            tpm = int(limit_tok)
            print(f"  -> với ~950 token/lượt: khoảng {tpm / 950:.1f} lượt chat/phút")

        print("\n  --- output ---")
        content = resp.choices[0].message.content or ""
        print("  " + content.replace("\n", "\n  "))

        results.append((model, elapsed, limit_tok, resp.usage.completion_tokens))

    except Exception as exc:
        print(f"  LỖI: {type(exc).__name__}: {exc}")
        response = getattr(exc, "response", None)
        if response is not None:
            print(f"  retry-after: {response.headers.get('retry-after')}")

# ---------------------------------------------------------------- 3
section("3. JSON mode có hoạt động không? (M5 cần)")

json_model = CANDIDATES[0]
try:
    r = client.chat.completions.create(
        model=json_model,
        messages=[
            {
                "role": "user",
                "content": 'Trả về JSON đúng dạng {"word":"resilient","meaning_vi":"..."} '
                "và không có gì khác.",
            }
        ],
        response_format={"type": "json_object"},
        max_tokens=120,
    )
    print(f"  {json_model}: OK")
    print("  " + (r.choices[0].message.content or ""))
except Exception as exc:
    print(f"  {json_model}: KHÔNG hỗ trợ json_object")
    print("  -> M5 phải tự parse và validate chặt hơn")
    print(f"  {type(exc).__name__}: {exc}")

# ---------------------------------------------------------------- tổng kết
section("TỔNG KẾT - chép vào docs/M0_FINDINGS.md")

print(f"{'model':<32} {'độ trễ':>8} {'TPM':>10} {'out tokens':>12}")
for model, elapsed, limit_tok, out_tok in results:
    print(f"{model:<32} {elapsed:>7.2f}s {str(limit_tok):>10} {out_tok:>12}")

print("\nCòn phải tự làm bằng tay:")
print("  - Chấm chất lượng tiếng Việt 1-5 cho từng output ở trên")
print("  - Điền vào .env: AI_MODEL_CHAT / AI_MODEL_REWRITE / AI_MODEL_QUIZ / AI_MODEL_FALLBACK")
print("  - Đặt AI_GLOBAL_TOKENS_PER_MINUTE THẤP HƠN TPM ở trên (biên an toàn ~20%)")