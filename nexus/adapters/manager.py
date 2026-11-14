from nexus.adapters.base import HostAdapter

class AdapterManager:
    def __init__(self):
        self.adapters = {}

    def register(self, adapter: HostAdapter):
        if not isinstance(adapter, HostAdapter):
            raise TypeError("Invalid adapter")
        if adapter.name in self.adapters:
            raise ValueError("Adapter already registered")
        self.adapters[adapter.name] = adapter

    def connect(self, name, config=None):
        adapter = self.adapters[name]
        return adapter.connect(config or {})

    def disconnect(self, name):
        self.adapters[name].disconnect()

    def send_message(self, name, message):
        return self.adapters[name].send_message(message)

    def list_adapters(self):
        return list(self.adapters.keys())
