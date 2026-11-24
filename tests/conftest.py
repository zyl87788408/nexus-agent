"""Shared test setup.

The server now requires an API key by default, so tests pin a known key and
let FastAPI's TestClient attach it to every request. Auth-specific tests
override ``server.API_KEY`` themselves.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_API_KEY = "test-api-key"


@pytest.fixture(autouse=True)
def _pin_api_key(monkeypatch):
    import server

    monkeypatch.setattr(server, "API_KEY", TEST_API_KEY, raising=False)
    # Default auth must stay on for these tests, matching production.
    monkeypatch.setattr(server, "_test_auth_headers", {"Authorization": f"Bearer {TEST_API_KEY}"},
                        raising=False)
    yield


@pytest.fixture(autouse=True)
def _authenticated_client(_pin_api_key):
    """Make TestClient send the key unless a test deliberately overrides it.

    A test that passes its own ``Authorization`` header (or none at all via
    ``@anon``) keeps full control.
    """
    from fastapi.testclient import TestClient

    original = TestClient.__init__

    def patched(self, app, *args, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        if any(k.lower() == "authorization" for k in headers):
            kwargs["headers"] = headers
        elif kwargs.pop("_anonymous", False):
            kwargs["headers"] = headers
        else:
            kwargs["headers"] = {"Authorization": f"Bearer {TEST_API_KEY}", **headers}
        original(self, app, *args, **kwargs)

    TestClient.__init__ = patched
    yield
    TestClient.__init__ = original
