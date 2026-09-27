"""A one-page counter: the page calls the JSON API from the browser."""

from flask import Flask, jsonify

PAGE = """<!doctype html>
<html>
  <head><title>Counter</title></head>
  <body>
    <h1>Counter</h1>
    <p>Count: <span id="count">0</span></p>
    <button id="increment">+1</button>
    <script>
      document.getElementById("increment").addEventListener("click", async () => {
        const response = await fetch("/api/increment", {method: "POST"});
        const data = await response.json();
        document.getElementById("count").textContent = data.count;
      });
    </script>
  </body>
</html>
"""


def create_app():
    app = Flask(__name__)
    state = {"count": 0}

    @app.get("/")
    def index():
        return PAGE

    @app.post("/api/increment")
    def increment():
        state["count"] += 1
        return jsonify(state)

    return app
