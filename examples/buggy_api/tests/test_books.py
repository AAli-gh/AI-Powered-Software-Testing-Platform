import pytest

from library import create_app


@pytest.fixture
def client():
    return create_app().test_client()


def test_list_books(client):
    assert client.get("/books").get_json()[0]["title"] == "Dune"


def test_add_book(client):
    response = client.post("/books", json={"title": "Emma", "year": 1815})
    assert response.status_code == 201


def test_get_book(client):
    assert client.get("/books/1").get_json()["year"] == 1965
