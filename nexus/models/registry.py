import json
import urllib.request

def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return json.loads(response.read())
    except Exception:
        return None

def discover_models():
    models = []

    data = fetch("http://127.0.0.1:11434/api/tags")
    if data:
        for item in data.get("models", []):
            details = item.get("details") or {}
            models.append({
                "backend": "ollama",
                "name": item.get("name", ""),
                "size_bytes": item.get("size"),
                "parameters": details.get("parameter_size"),
                "quantization": details.get("quantization_level"),
                "family": details.get("family"),
                "digest": item.get("digest"),
            })

    data = fetch("http://127.0.0.1:8080/v1/models")
    if data:
        for item in data.get("data", []):
            models.append({
                "backend": "llama.cpp",
                "name": item.get("id", ""),
                "size_bytes": None,
                "parameters": None,
                "quantization": None,
                "family": None,
                "digest": None,
            })

    return models

if __name__ == "__main__":
    print(json.dumps(discover_models(), indent=2, ensure_ascii=False))
