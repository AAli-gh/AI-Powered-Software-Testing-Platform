import pytest
from fastapi.testclient import TestClient

from todo.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
