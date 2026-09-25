def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_and_list(client):
    assert client.post("/items/", json={"id": 1, "title": "write tests"}).status_code == 201
    assert [i["title"] for i in client.get("/items/").json()] == ["write tests"]


def test_delete_missing_item_is_404(client):
    assert client.delete("/items/404").status_code == 404
