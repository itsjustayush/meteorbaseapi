from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # Local smoke tests may run without optional dotenv support.
    def load_dotenv() -> bool:
        return False

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

SERVICE_NAME = "meteorbase"


class TestRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)


class TestResponse(BaseModel):
    service: str
    status: str
    message: str


@lru_cache(maxsize=1)
def get_supabase_client() -> Any:
    """Create one reusable Supabase client from the existing Render variables."""
    url = os.getenv("SUPABASE_URL")
    key = (
        os.getenv("SUPABASE_SERVICE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or os.getenv("SUPABASE_KEY")
    )

    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and one of SUPABASE_SERVICE_KEY, SUPABASE_ANON_KEY, "
            "or SUPABASE_KEY must be configured."
        )

    from supabase import create_client

    return create_client(url, key)


app = FastAPI(
    title="MeteorBase API",
    description="A minimal FastAPI service connected to the existing Supabase project.",
    version="1.0.0",
)


@app.get("/", tags=["system"])
def root() -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "framework": "FastAPI",
        "docs": "/docs",
        "test_endpoint": "/api/test",
    }


@app.get("/healthz", tags=["system"])
def healthz() -> dict[str, str]:
    """Lightweight Render health check that does not require a database round trip."""
    return {"service": SERVICE_NAME, "status": "ok"}


@app.get("/readyz", tags=["system"])
def readyz() -> dict[str, Any]:
    configured = bool(
        os.getenv("SUPABASE_URL")
        and (
            os.getenv("SUPABASE_SERVICE_KEY")
            or os.getenv("SUPABASE_ANON_KEY")
            or os.getenv("SUPABASE_KEY")
        )
    )
    return {
        "service": SERVICE_NAME,
        "status": "ready" if configured else "not_ready",
        "supabase_configured": configured,
    }


@app.get("/api/test", response_model=TestResponse, tags=["test"])
def test_get() -> TestResponse:
    """Simple endpoint for confirming that the FastAPI service is responding."""
    return TestResponse(
        service=SERVICE_NAME,
        status="ok",
        message="FastAPI endpoint is working.",
    )


@app.post("/api/test", response_model=TestResponse, tags=["test"])
def test_post(payload: TestRequest) -> TestResponse:
    """Echo a JSON message to verify request parsing and Pydantic validation."""
    return TestResponse(service=SERVICE_NAME, status="ok", message=payload.message)


@app.get("/api/services", tags=["supabase"])
def list_services() -> dict[str, Any]:
    """Read active services from the existing Supabase `services` table."""
    try:
        result = (
            get_supabase_client()
            .table("services")
            .select("id, name, display_name, description, is_active, created_at, updated_at")
            .eq("is_active", True)
            .order("name")
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"message": "Supabase is unavailable.", "error": str(exc)},
        ) from exc

    return {"service": SERVICE_NAME, "status": "ok", "data": result.data or []}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("FASTAPI_RELOAD") == "1",
    )
