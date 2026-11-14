from abc import ABC, abstractmethod

class HostAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def connect(self, config: dict) -> bool:
        pass

    @abstractmethod
    def disconnect(self) -> None:
        pass

    @abstractmethod
    def send_message(self, message: str) -> dict:
        pass

    @abstractmethod
    def register_tool(self, tool: dict) -> bool:
        pass
