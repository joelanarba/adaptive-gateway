"""Deterministic synthetic upstream for local experiments; no external requests."""

from fastapi import FastAPI

if __package__:
    from .contract import PAYLOAD
else:  # Existing uvicorn app:app --app-dir benchmarks/fixture entry point.
    from contract import PAYLOAD

app = FastAPI()


@app.get("/items")
async def items() -> list[dict]:
    return PAYLOAD
