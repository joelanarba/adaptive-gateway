"""Deterministic synthetic upstream for local experiments; no external requests."""

from fastapi import FastAPI

app = FastAPI()
PAYLOAD = [
    {
        "id": index,
        "title": f"item {index}",
        "body": "optional-text-" * 160,
        "meta": {"debug": "diagnostic-" * 30, "category": "synthetic"},
    }
    for index in range(100)
]


@app.get("/items")
async def items() -> list[dict]:
    return PAYLOAD
