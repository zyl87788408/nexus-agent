import json
import urllib.request

class LlamaCppBackend:
    def __init__(self, model="local", host="http://127.0.0.1:8080"):
        self.model = model
        self.host = host.rstrip("/")

    def generate(self, prompt):
        return self.chat([{"role": "user", "content": prompt}])

    def chat(self, messages):
        data = json.dumps({
            "model": self.model,
            "messages": messages,
            "stream": False
        }).encode()

        request = urllib.request.Request(
            self.host + "/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"}
        )

        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.loads(response.read())

        return result["choices"][0]["message"]["content"]
