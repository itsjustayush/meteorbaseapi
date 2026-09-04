from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from uuid import UUID

try:
    from dotenv import load_dotenv
except ImportError:  # Local smoke tests may run without optional dotenv support.
    def load_dotenv() -> bool:
        return False

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
load_dotenv()

SERVICE_NAME = "meteorbase"
TASKS_TABLE = "meteorbase_tasks"


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    completed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    completed: bool | None = None
    metadata: dict[str, Any] | None = None


class Task(BaseModel):
    id: UUID
    title: str
    description: str | None = None
    completed: bool
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@lru_cache(maxsize=2)
def get_supabase_client(require_write: bool = False) -> Any:
    """Create a reusable Supabase client from Render environment variables."""
    url = os.getenv("SUPABASE_URL")
    key_names = supabase_key_names(require_write=require_write)
    key = next((os.getenv(name) for name in key_names if os.getenv(name)), None)

    if not url or not key:
        required = " or ".join(key_names)
        raise RuntimeError(f"SUPABASE_URL and {required} must be configured.")

    from supabase import create_client

    return create_client(url, key)


def from_exception(exc: Exception) -> HTTPException:
    """Attach the original exception as the cause without exposing it to clients."""
    error = HTTPException(
        status_code=503,
        detail="Supabase is unavailable. Check the Render Supabase environment variables and server key.",
    )
    error.__cause__ = exc
    return error


def supabase_key_names(require_write: bool = False) -> list[str]:
    """Return accepted Supabase key variable names for the current access mode."""
    write_names = [
        "SUPABASE_SECRET_KEY",
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
    ]
    read_names = [
        *write_names,
        "SUPABASE_PUBLISHABLE_KEY",
        "SUPABASE_ANON_KEY",
        "SUPABASE_KEY",
    ]
    return write_names if require_write else read_names


def task_columns() -> str:
    return "id, title, description, completed, metadata, created_at, updated_at"


app = FastAPI(
    title="MeteorBase  API",
    description="A small Supabase-backed CRUD API for temporary testing.",
    version="2.0.0",
)


#@app.get("/", tags=["system"])
#def root() -> dict[str, Any]:
#    return {
#        "service": SERVICE_NAME,
#        "status": "ok",
#        "framework": "FastAPI",
#        "docs": "/docs",
#        "task_table": TASKS_TABLE,
#        "endpoints": {
#            "create": "POST /task",
#            "list": "GET /get",
#            "read": "GET /task/{task_id}",
#            "update": "PATCH /task/{task_id}",
#            "delete": "DELETE /task/{task_id}",
#        },
#    }


@app.get("/", response_class=FileResponse)
async def root():
    return "index.html" 


@app.get("/healthz", tags=["system"])
def healthz() -> dict[str, str]:
    return {"service": SERVICE_NAME, "status": "ok"}


@app.get("/readyz", tags=["system"])
def readyz() -> dict[str, Any]:
    write_key_configured = bool(
        os.getenv("SUPABASE_URL")
        and any(os.getenv(name) for name in supabase_key_names(require_write=True))
    )
    return {
        "service": SERVICE_NAME,
        "status": "ready" if write_key_configured else "not_ready",
        "supabase_write_key_configured": write_key_configured,
        "table": TASKS_TABLE,
    }


@app.get("/api/test", tags=["test"])
def api_test() -> dict[str, str]:
    return {"service": SERVICE_NAME, "status": "ok", "message": "FastAPI is working."}


@app.post("/task", response_model=Task, status_code=status.HTTP_201_CREATED, tags=["tasks"])
def create_task(payload: TaskCreate) -> Task:
    try:
        result = (
            get_supabase_client(require_write=True)
            .table(TASKS_TABLE)
            .insert(payload.model_dump())
            .execute()
        )
    except Exception as exc:
        raise from_exception(exc)

    if not result.data:
        raise HTTPException(status_code=503, detail="Supabase did not return the created task.")
    return Task.model_validate(result.data[0])


@app.get("/get", response_model=list[Task], tags=["tasks"])
def get_tasks(
    limit: int = Query(default=25, ge=1, le=100),
    completed: bool | None = Query(default=None),
) -> list[Task]:
    try:
        query = (
            get_supabase_client()
            .table(TASKS_TABLE)
            .select(task_columns())
            .order("created_at", desc=True)
            .limit(limit)
        )
        if completed is not None:
            query = query.eq("completed", completed)
        result = query.execute()
    except Exception as exc:
        raise from_exception(exc)

    return [Task.model_validate(row) for row in (result.data or [])]


@app.get("/task/{task_id}", response_model=Task, tags=["tasks"])
def get_task(task_id: UUID) -> Task:
    try:
        result = (
            get_supabase_client()
            .table(TASKS_TABLE)
            .select(task_columns())
            .eq("id", str(task_id))
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise from_exception(exc)

    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found.")
    return Task.model_validate(result.data[0])


@app.patch("/task/{task_id}", response_model=Task, tags=["tasks"])
def update_task(task_id: UUID, payload: TaskUpdate) -> Task:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Provide at least one field to update.")

    changes["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        result = (
            get_supabase_client(require_write=True)
            .table(TASKS_TABLE)
            .update(changes)
            .eq("id", str(task_id))
            .select(task_columns())
            .execute()
        )
    except Exception as exc:
        raise from_exception(exc)

    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found.")
    return Task.model_validate(result.data[0])


@app.delete("/task/{task_id}", tags=["tasks"])
def delete_task(task_id: UUID) -> dict[str, str]:
    try:
        result = (
            get_supabase_client(require_write=True)
            .table(TASKS_TABLE)
            .delete()
            .eq("id", str(task_id))
            .select("id")
            .execute()
        )
    except Exception as exc:
        raise from_exception(exc)

    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"status": "deleted", "id": str(task_id)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("FASTAPI_RELOAD") == "1",
    )
