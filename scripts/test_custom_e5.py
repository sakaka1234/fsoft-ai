from pathlib import Path

from fastembed import TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType

MODEL = "intfloat/multilingual-e5-small"

snapshot = (
    Path.home()
    / "AppData"
    / "Local"
    / "Temp"
    / "fastembed_cache"
    / "models--intfloat--multilingual-e5-small"
    / "snapshots"
    / "614241f622f53c4eeff9890bdc4f31cfecc418b3"
)

TextEmbedding.add_custom_model(
    model=MODEL,
    pooling=PoolingType.MEAN,
    normalization=True,
    sources=ModelSource(hf=MODEL),
    dim=384,
    model_file="onnx/model.onnx",
    description="Multilingual E5 Small",
    license="MIT",
    size_in_gb=0.5,
    additional_files=[
        "onnx/tokenizer.json",
        "onnx/tokenizer_config.json",
        "onnx/special_tokens_map.json",
        "onnx/sentencepiece.bpe.model",
        "onnx/config.json",
    ],
)

print("Registered:", MODEL)
print("Snapshot:", snapshot)

model = TextEmbedding(
    model_name=MODEL,
    specific_model_path=str(snapshot),
)

vec = list(model.embed(["xin chào"]))[0]

print("dimension:", vec.shape[0])
print("norm:", float((vec**2).sum() ** 0.5))