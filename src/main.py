"""FastAPI application entrypoint."""

from fastapi import FastAPI

app = FastAPI(title="TaskMaster Triage Helper")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
