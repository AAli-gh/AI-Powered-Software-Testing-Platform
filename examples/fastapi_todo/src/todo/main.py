from fastapi import FastAPI

from todo.routers import items

app = FastAPI(title="Todo API")
app.include_router(items.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
