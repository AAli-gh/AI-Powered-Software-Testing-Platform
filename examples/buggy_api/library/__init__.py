"""A small book catalogue API with deliberate defects (see EXPECTED_API.md)."""

from flask import Flask, abort, jsonify, request


def create_app():
    app = Flask(__name__)
    books = {1: {"id": 1, "title": "Dune", "year": 1965}}

    @app.get("/books")
    def list_books():
        return jsonify(list(books.values()))

    @app.post("/books")
    def add_book():
        payload = request.get_json()
        title = payload["title"]  # defect: a body without "title" is a 500, not a 400
        book = {"id": max(books, default=0) + 1, "title": title, "year": payload.get("year")}
        books[book["id"]] = book
        return jsonify(book), 201

    @app.get("/books/<int:book_id>")
    def get_book(book_id):
        if book_id not in books:
            abort(404)
        return jsonify(books[book_id])

    @app.put("/books/<int:book_id>")
    def update_book(book_id):
        if book_id not in books:
            abort(404)
        # defect: nothing is validated; an empty or malformed body is accepted
        books[book_id].update(request.get_json(silent=True) or {})
        return jsonify(books[book_id])

    @app.delete("/books/<int:book_id>")
    def delete_book(book_id):
        books.pop(book_id, None)
        return "", 204

    @app.get("/stats")
    def stats():
        years = [b["year"] for b in books.values() if b.get("year")]
        return jsonify({"books": len(books), "average_year": sum(years) / len(years)})

    return app
