from flask import Flask, jsonify, request


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(config or {})
    notes: list[dict] = []

    @app.get("/notes")
    def list_notes():
        return jsonify(notes)

    @app.post("/notes")
    def add_note():
        payload = request.get_json(silent=True) or {}
        text = str(payload.get("text", "")).strip()
        if not text:
            return jsonify({"error": "text is required"}), 400
        notes.append({"id": len(notes) + 1, "text": text})
        return jsonify(notes[-1]), 201

    return app
