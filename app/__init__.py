"""
fsoft-ai — service RAG cho hệ thống học từ vựng.

Hai dòng `os.environ` bên dưới PHẢI chạy trước khi bất kỳ thứ gì import
onnxruntime hay numpy, và `app/__init__.py` là chỗ duy nhất đảm bảo được thứ tự
đó: mọi `import app.<gì đó>` đều chạy file này trước. Không thể dựa vào `.env` —
pydantic-settings đọc nó quá muộn, sau khi thư viện đã dựng threadpool.

ĐÍNH CHÍNH MỘT NIỀM TIN SAI đã ghim ở 9 chỗ trong repo này:

    `ORT_NUM_THREADS` KHÔNG điều khiển ONNX Runtime.

ONNX Runtime không đọc biến môi trường nào để lấy số luồng; nó chỉ nhận qua
`SessionOptions.intra_op_num_threads`. Và fastembed chỉ đặt trường đó khi được
truyền `threads` (`fastembed/common/onnx_model.py`) — mà `Encoder` trước đây
không bao giờ truyền. Kết quả: đặt hai biến này rồi tưởng đã giới hạn 1 luồng,
trong khi ORT vẫn mở luồng theo số nhân MÁY CHỦ. Đo được: session vẫn sinh thêm
7 luồng OS.

Đường đúng là `AI_ORT_INTRA_OP_THREADS`, do `Encoder` truyền xuống
`SessionOptions`. Dockerfile đặt nó bằng 1 vì container thường bị bóp CPU mà ORT
lại đếm nhân của máy chủ.

Hai dòng dưới đây VẪN GIỮ, nhưng vì lý do khác với lý do đã ghi trước đây:
`OMP_NUM_THREADS` thật sự có tác dụng — nó giới hạn BLAS/OpenMP mà numpy dùng.
`ORT_NUM_THREADS` giữ lại cho vô hại và cho khớp tài liệu cũ; nó không làm gì.
"""

import os

# Có tác dụng thật: giới hạn threadpool OpenMP của BLAS trong numpy.
os.environ.setdefault("OMP_NUM_THREADS", "1")
# KHÔNG có tác dụng với ONNX Runtime. Xem AI_ORT_INTRA_OP_THREADS.
os.environ.setdefault("ORT_NUM_THREADS", "1")
