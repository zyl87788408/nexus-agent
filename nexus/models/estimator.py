import re

QUANT_BYTES = {
    "q4": 0.65,
    "q5": 0.80,
    "q6": 0.95,
    "q8": 1.10,
    "fp16": 2.10,
}


def _base_quant(quant: str) -> str:
    """Map a concrete GGUF quant name (e.g. `q4_k_m`) to its byte-rate bucket.

    Ollama reports quantisation as `q4_K_M` / `q8_0` / `f16`. Only the leading
    family is meaningful for size estimation, so look for the longest known
    prefix and fall back to fp16 rather than raising KeyError.
    """
    q = quant.lower().replace("-", "_")
    for key in sorted(QUANT_BYTES, key=len, reverse=True):
        if q.startswith(key):
            return key
    if q in {"f16", "float16", "bf16"}:
        return "fp16"
    if q.startswith("q"):
        return "q4"
    return "fp16"


def estimate_model_memory(model, context_tokens=2048):
    if isinstance(model, dict):
        name = model.get("name", "")
        size = model.get("size_bytes")
        parameters = model.get("parameters")
        quantization = model.get("quantization")
    else:
        name = str(model)
        size = None
        parameters = None
        quantization = None

    text = f"{name} {parameters or ''} {quantization or ''}".lower()
    match = re.search(r'(\d+(?:\.\d+)?)\s*b', text)
    params = float(match.group(1)) if match else None
    quant = str(quantization or "").lower()
    if not quant:
        quant = next((q for q in QUANT_BYTES if q in text), "fp16")
    quant = _base_quant(quant)

    if size:
        weight_mb = size / (1024 * 1024)
        source = "model_file_size"
    elif params:
        weight_mb = params * QUANT_BYTES[quant] * 1024
        source = "parameter_estimate"
    else:
        return None

    context_mb = max(128, context_tokens * 0.125)
    total_mb = round(weight_mb * 1.2 + context_mb)

    return {
        "parameters_b": params,
        "quantization": quant,
        "estimated_memory_mb": total_mb,
        "context_tokens": context_tokens,
        "estimate_source": source,
        "approximate": True
    }
