from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # Local smoke tests may run without optional dotenv support.
    def load_dotenv() -> bool:
        return False

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

load_dotenv()

SERVICE_NAME = "meteorbase"
RESULTS_TABLE = "meteorbase_api_results"
SERVICES_TABLE = "meteorbase_services"


class TestRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)


class TestResponse(BaseModel):
    service: str
    status: str
    message: str
    stored: bool


@lru_cache(maxsize=2)
def get_supabase_client(require_write: bool = False) -> Any:
    """Create a reusable Supabase client from the existing Render variables."""
    url = os.getenv("SUPABASE_URL")
    write_key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
    read_key = (
        write_key
        or os.getenv("SUPABASE_PUBLISHABLE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or os.getenv("SUPABASE_KEY")
    )
    key = write_key if require_write else read_key

    if not url or not key:
        if require_write:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SECRET_KEY or SUPABASE_SERVICE_KEY "
                "must be configured for database writes."
            )
        raise RuntimeError("SUPABASE_URL and a Supabase key must be configured.")

    from supabase import create_client

    return create_client(url, key)


def store_api_result(
    *,
    endpoint: str,
    method: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any] | None,
    status_code: int,
    error_message: str | None = None,
) -> None:
    get_supabase_client(require_write=True).table(RESULTS_TABLE).insert(
        {
            "endpoint": endpoint,
            "method": method,
            "request_payload": request_payload,
            "response_payload": response_payload,
            "status_code": status_code,
            "error_message": error_message,
        }
    ).execute()


app = FastAPI(
    title="MeteorBase API",
    description="A FastAPI service connected to the Ayush MeteorAPI Supabase project.",
    version="1.2.0",
)


@app.get("/", tags=["system"])
def root() -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "framework": "FastAPI",
        "docs": "/docs",
        "test_endpoint": "/api/test",
        "results_endpoint": "/api/results",
    }


@app.get("/healthz", tags=["system"])
def healthz() -> dict[str, str]:
    """Lightweight Render health check that does not require a database round trip."""
    return {"service": SERVICE_NAME, "status": "ok"}


@app.get("/readyz", tags=["system"])
def readyz() -> dict[str, Any]:
    write_key_configured = bool(
        os.getenv("SUPABASE_URL")
        and (os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_KEY"))
    )
    read_key_configured = bool(
        os.getenv("SUPABASE_URL")
        and (
            os.getenv("SUPABASE_SECRET_KEY")
            or os.getenv("SUPABASE_SERVICE_KEY")
            or os.getenv("SUPABASE_PUBLISHABLE_KEY")
            or os.getenv("SUPABASE_ANON_KEY")
            or os.getenv("SUPABASE_KEY")
        )
    )
    return {
        "service": SERVICE_NAME,
        "status": "ready" if write_key_configured else "not_ready",
        "supabase_configured": read_key_configured,
        "supabase_write_key_configured": write_key_configured,
        "tables": {"services": SERVICES_TABLE, "results": RESULTS_TABLE},
    }


@app.get("/api/test", response_model=TestResponse, tags=["test"])
def test_get() -> TestResponse:
    """Verify the service and persist the successful test result in Supabase."""
    response = {
        "service": SERVICE_NAME,
        "status": "ok",
        "message": "FastAPI endpoint is working.",
    }
    try:
        store_api_result(
            endpoint="/api/test",
            method="GET",
            request_payload={},
            response_payload=response,
            status_code=200,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Supabase result storage failed. Set the destination project's "
                "secret/service key in Render as SUPABASE_SECRET_KEY or "
                "SUPABASE_SERVICE_KEY."
            ),
        ) from exc

    return TestResponse(**response, stored=True)


@app.post("/api/test", response_model=TestResponse, tags=["test"])
def test_post(payload: TestRequest) -> TestResponse:
    """Echo a JSON message and persist the test result in Supabase."""
    response = {
        "service": SERVICE_NAME,
        "status": "ok",
        "message": payload.message,
    }
    try:
        store_api_result(
            endpoint="/api/test",
            method="POST",
            request_payload=payload.model_dump(),
            response_payload=response,
            status_code=200,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Supabase result storage failed. Set the destination project's "
                "secret/service key in Render as SUPABASE_SECRET_KEY or "
                "SUPABASE_SERVICE_KEY."
            ),
        ) from exc

    return TestResponse(**response, stored=True)


@app.get("/api/results", tags=["supabase"])
def list_results(
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    """Return recent API test results from the isolated Supabase table."""
    try:
        result = (
            get_supabase_client()
            .table(RESULTS_TABLE)
            .select(
                "id, endpoint, method, request_payload, response_payload, "
                "status_code, error_message, created_at"
            )
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Supabase is unavailable for result retrieval.",
        ) from exc

    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "table": RESULTS_TABLE,
        "data": result.data or [],
    }


@app.get("/api/services", tags=["supabase"])
def list_services() -> dict[str, Any]:
    """Read active services from the isolated Supabase services table."""
    try:
        result = (
            get_supabase_client()
            .table(SERVICES_TABLE)
            .select("id, name, display_name, description, is_active, created_at, updated_at")
            .eq("is_active", True)
            .order("name")
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Supabase is unavailable for service retrieval.",
        ) from exc

    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "table": SERVICES_TABLE,
        "data": result.data or [],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("FASTAPI_RELOAD") == "1",
    )
