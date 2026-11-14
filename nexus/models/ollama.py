import json
import urllib.request

class OllamaBackend:
    def __init__(self, model, host="http://127.0.0.1:11434"):
        self.model = model
        self.host = host.rstrip("/")

    def generate(self, prompt):
        return self._post("/api/generate", {
            "model": self.model,
            "prompt": prompt,
            "stream": False
        })["response"]

    def chat(self, messages):
        data = self._post("/api/chat", {
            "model": self.model,
            "messages": messages,
            "stream": False
        })
        return data["message"]["content"]

    def _post(self, path, payload):
        request = urllib.request.Request(
            self.host + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}
        )

        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read())
