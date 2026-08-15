"""
fsoft-ai — service RAG cho hệ thống học từ vựng.

CẢNH BÁO: hai dòng os.environ bên dưới PHẢI chạy trước khi bất kỳ thứ gì
import fastembed / onnxruntime.

Container Railway thường chỉ 1-2 vCPU, nhưng ONNX Runtime mặc định spawn
luồng theo số core của MÁY CHỦ VẬT LÝ. Oversubscription luồng làm chậm chứ
không làm nhanh. Chi tiết ở SPEC muc 5.10, bẫy 2.

Đặt ở đây vì mọi `import app.<gì đó>` đều chạy file này trước — đây là chỗ
duy nhất đảm bảo được thứ tự đó. Không thể dựa vào .env: pydantic-settings
đọc .env quá muộn, sau khi onnxruntime đã khởi tạo threadpool.
"""

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("ORT_NUM_THREADS", "1")
