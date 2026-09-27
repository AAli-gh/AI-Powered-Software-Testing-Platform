import threading

import pytest
from werkzeug.serving import make_server

from counter import create_app


@pytest.fixture(scope="session")
def base_url():
    """The application on a free local port for the browser (pytest-playwright's base_url)."""
    server = make_server("127.0.0.1", 0, create_app())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
