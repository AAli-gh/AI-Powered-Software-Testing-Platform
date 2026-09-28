from testmind.api.security import client_key


def test_client_key_hashes_the_address() -> None:
    first = client_key({"client": ("10.0.0.1", 5000)})
    assert first == client_key({"client": ("10.0.0.1", 6000)})  # the port does not matter
    assert first != client_key({"client": ("10.0.0.2", 5000)})
    assert "10.0.0.1" not in first
    assert client_key({"client": None}) == client_key({})
