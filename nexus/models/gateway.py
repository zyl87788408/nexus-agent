from abc import ABC, abstractmethod

class ModelBackend(ABC):
    @abstractmethod
    def generate(self, prompt: str) -> str:
        pass

class ModelGateway:
    def __init__(self, backend=None):
        self.backend = backend

    def set_backend(self, backend):
        self.backend = backend

    def generate(self, prompt: str) -> str:
        if self.backend is None:
            raise RuntimeError("No local model backend configured")
        return self.backend.generate(prompt)
