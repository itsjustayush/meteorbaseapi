from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from uuid import UUID, uuid4

from dotenv import load_dotenv
load_dotenv()

import httpx
from fastapi import FastAPI, HTTPException, Security, Depends, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("meteorbase")

SERVICE_NAME = "meteorbase"
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "90"))

# Load Firebase configuration from environment or firebase-applet-config.json
FIREBASE_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "firebase-applet-config.json")
firebase_config: dict[str, Any] = {}
if os.path.exists(FIREBASE_CONFIG_PATH):
    try:
        with open(FIREBASE_CONFIG_PATH, "r", encoding="utf-8") as f:
            firebase_config = json.load(f)
    except Exception as e:
        logger.warning("Failed to load firebase-applet-config.json: %s", e)

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", firebase_config.get("projectId", "itsjustayush"))
FIREBASE_DB_ID = os.getenv("FIREBASE_DATABASE_ID", firebase_config.get("firestoreDatabaseId", "(default)"))
FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY", firebase_config.get("apiKey", ""))


def get_firestore_base_url() -> str:
    return f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/{FIREBASE_DB_ID}/documents"


# Dynamic Serialization / Deserialization for Flexible Firestore Storage
def python_to_firestore_value(val: Any) -> dict[str, Any]:
    """Convert any Python value to Firestore REST API value format dynamically."""
    if val is None:
        return {"nullValue": None}
    elif isinstance(val, bool):
        return {"booleanValue": val}
    elif isinstance(val, int):
        return {"integerValue": str(val)}
    elif isinstance(val, float):
        return {"doubleValue": val}
    elif isinstance(val, str):
        return {"stringValue": val}
    elif isinstance(val, (datetime,)):
        return {"stringValue": val.isoformat()}
    elif isinstance(val, list):
        return {"arrayValue": {"values": [python_to_firestore_value(x) for x in val]}}
    elif isinstance(val, dict):
        return {"mapValue": {"fields": {k: python_to_firestore_value(v) for k, v in val.items()}}}
    else:
        return {"stringValue": str(val)}


def firestore_value_to_python(val: dict[str, Any]) -> Any:
    """Convert Firestore REST API field value back to native Python types."""
    if not val or not isinstance(val, dict):
        return None
    if "nullValue" in val:
        return None
    if "booleanValue" in val:
        return val["booleanValue"]
    if "integerValue" in val:
        return int(val["integerValue"])
    if "doubleValue" in val:
        return float(val["doubleValue"])
    if "stringValue" in val:
        return val["stringValue"]
    if "arrayValue" in val:
        return [firestore_value_to_python(x) for x in val["arrayValue"].get("values", [])]
    if "mapValue" in val:
        return {k: firestore_value_to_python(v) for k, v in val["mapValue"].get("fields", {}).items()}
    return None


def python_to_firestore_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {k: python_to_firestore_value(v) for k, v in data.items()}


def firestore_fields_to_python(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: firestore_value_to_python(v) for k, v in fields.items()}


def save_to_firestore(collection: str, doc_id: str, data: dict[str, Any]) -> bool:
    """Dynamically save any document with arbitrary fields to any Firestore collection."""
    if not FIREBASE_API_KEY:
        logger.warning("FIREBASE_API_KEY not configured, skipping write to %s", collection)
        return False

    url = f"{get_firestore_base_url()}/{collection}?documentId={doc_id}&key={FIREBASE_API_KEY}"
    payload = {"fields": python_to_firestore_fields(data)}

    try:
        resp = httpx.post(url, json=payload, timeout=10.0)
        if resp.status_code in (200, 201):
            return True
        logger.error("Firestore save error [%d]: %s", resp.status_code, resp.text)
        return False
    except Exception as exc:
        logger.error("Error saving to Firestore %s/%s: %s", collection, doc_id, exc)
        return False


def query_firestore_collection(collection: str) -> list[dict[str, Any]]:
    """Dynamically query all documents in a collection without rigid schema requirements."""
    if not FIREBASE_API_KEY:
        return []

    url = f"{get_firestore_base_url()}:runQuery?key={FIREBASE_API_KEY}"
    query = {"structuredQuery": {"from": [{"collectionId": collection}]}}

    try:
        resp = httpx.post(url, json=query, timeout=10.0)
        if resp.status_code != 200:
            logger.error("Firestore query error [%d]: %s", resp.status_code, resp.text)
            return []

        results = []
        for item in resp.json():
            doc = item.get("document")
            if not doc:
                continue
            fields = doc.get("fields", {})
            name_parts = doc.get("name", "").split("/")
            doc_id = name_parts[-1] if name_parts else ""
            parsed = firestore_fields_to_python(fields)
            parsed["id"] = parsed.get("id") or doc_id
            results.append(parsed)
        return results
    except Exception as exc:
        logger.error("Error querying collection %s: %s", collection, exc)
        return []


def delete_from_firestore(collection: str, doc_id: str) -> bool:
    """Delete a document by ID with zero confirmation (100% automated)."""
    if not FIREBASE_API_KEY:
        return False
    url = f"{get_firestore_base_url()}/{collection}/{doc_id}?key={FIREBASE_API_KEY}"
    try:
        resp = httpx.delete(url, timeout=10.0)
        return resp.status_code in (200, 204)
    except Exception as exc:
        logger.error("Error deleting doc %s/%s: %s", collection, doc_id, exc)
        return False


def verify_and_cleanup_database(collections: list[str] | None = None) -> dict[str, Any]:
    """Automated retention cleaner: Checks timestamps and deletes records older than 90 days.
    Runs automatically on ping with zero confirmation.
    """
    if collections is None:
        collections = ["summaries"]

    now = datetime.now(timezone.utc)
    current_epoch = int(now.timestamp())
    expired_count = 0
    active_count = 0
    total_checked = 0

    for col in collections:
        docs = query_firestore_collection(col)
        total_checked += len(docs)
        for doc in docs:
            doc_id = str(doc.get("id", ""))
            exp_epoch = doc.get("expires_at_epoch")
            is_expired = False

            if exp_epoch and isinstance(exp_epoch, (int, float)) and exp_epoch <= current_epoch:
                is_expired = True
            elif doc.get("created_at"):
                try:
                    c_time = datetime.fromisoformat(str(doc["created_at"]).replace("Z", "+00:00"))
                    if (now - c_time).total_seconds() >= RETENTION_DAYS * 86400:
                        is_expired = True
                except Exception:
                    pass

            if is_expired and doc_id:
                # Automatic deletion with no confirmation
                if delete_from_firestore(col, doc_id):
                    expired_count += 1
            else:
                active_count += 1

    return {
        "checked_at": now.isoformat(),
        "total_checked": total_checked,
        "expired_deleted": expired_count,
        "active_records": active_count,
        "retention_days": RETENTION_DAYS,
    }


# Initialize Gemini Client
gemini_client = None
try:
    from google import genai
    gemini_client = genai.Client()
    logger.info("Google GenAI client initialized successfully")
except Exception as e:
    logger.warning("Could not initialize google.genai: %s", e)

# Security Scheme
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(api_key: Optional[str] = Security(api_key_header)) -> Optional[str]:
    expected_key = os.getenv("MY_API_SECRET")
    if expected_key:
        if not api_key or api_key != expected_key:
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")
    return api_key


# FastAPI Initialization
app = FastAPI(
    title="MeteorBase API",
    description="Flexible, Supabase & Firebase-backed API with Gemini Summarizer, automated 90-day retention, and real-time telemetry.",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Flexible Request Model (allows any arbitrary extra fields without collisions)
class SummarizeRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: str = Field(..., min_length=10, description="The raw text to be summarized")
    format_type: str = Field(default="bullets", description="Format: 'bullets' or 'paragraph'")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary custom metadata for future extensibility")


# Exact Root UI endpoint (Serves previous Core Infrastructure UI with new function latencies)
@app.get("/", response_class=FileResponse)
async def root():
    return "index.html"


# Main Summarizer Endpoint
@app.post("/summarize", tags=["summarizer"])
def summarize_text(request: SummarizeRequest, api_key: Optional[str] = Depends(verify_api_key)):
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    prompt_text = (
        f"Please provide a concise summary of the following text. "
        f"Format the output strictly as {request.format_type}. "
        f"Do not include any introductory filler or markdown code blocks.\n\n"
        f"TEXT TO SUMMARIZE:\n{request.text}"
    )

    summary_text = ""
    if gemini_client:
        models_to_try = ["gemini-3.6-flash", "gemini-3.8-flash", "gemini-flash-latest"]
        last_error = None
        for m in models_to_try:
            try:
                response = gemini_client.models.generate_content(
                    model=m,
                    contents=prompt_text
                )
                if response and response.text:
                    summary_text = response.text.replace("```html", "").replace("```", "").strip()
                    break
            except Exception as exc:
                last_error = exc
                logger.warning("Model %s failed: %s, trying fallback...", m, exc)

        if not summary_text and last_error:
            raise HTTPException(status_code=500, detail=f"Gemini API error: {str(last_error)}")
    else:
        raise HTTPException(status_code=503, detail="Gemini client is not initialized. Check GEMINI_API_KEY.")

    # 90-day retention calculation
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=RETENTION_DAYS)
    summary_id = str(uuid4())

    # Build dynamic document payload
    doc_payload = {
        "id": summary_id,
        "raw_text": request.text,
        "summary": summary_text,
        "format_type": request.format_type,
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "created_at_epoch": int(now.timestamp()),
        "expires_at_epoch": int(expires.timestamp()),
        "retention_days": RETENTION_DAYS,
        "metadata": request.metadata,
    }

    # Extract any extra fields provided in request and store them dynamically
    for k, v in request.model_extra.items() if request.model_extra else []:
        if k not in doc_payload:
            doc_payload[k] = v

    persisted = save_to_firestore("summaries", summary_id, doc_payload)

    return {
        "id": summary_id,
        "summary": summary_text,
        "format_type": request.format_type,
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "retention_days": RETENTION_DAYS,
        "persisted_to_firebase": persisted,
        "metadata": request.metadata,
    }


# Lightweight health probe for /summarize to check endpoint latency on the dashboard
@app.get("/summarize/health", tags=["summarizer"])
def summarize_health() -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "endpoint": "/summarize",
        "status": "ready" if gemini_client is not None else "degraded",
        "model": "gemini-3.6-flash",
        "retention_days": RETENTION_DAYS,
    }


# Continuous Pinging System (Verifies database & automatically cleans up 90d expired data)
@app.get("/ping", tags=["telemetry"])
def ping() -> dict[str, Any]:
    """Continuous pinging endpoint for external monitors.
    Verifies timestamps across the database and automatically purges data older than 90 days.
    """
    cleanup_result = verify_and_cleanup_database()
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "message": "Ping acknowledged. 90-day retention timestamps verified.",
        "telemetry": cleanup_result,
    }


@app.get("/healthz", tags=["telemetry"])
def healthz() -> dict[str, Any]:
    cleanup_result = verify_and_cleanup_database()
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "telemetry": cleanup_result,
    }


@app.get("/readyz", tags=["telemetry"])
def readyz() -> dict[str, Any]:
    fb_configured = bool(FIREBASE_API_KEY and FIREBASE_PROJECT_ID)
    return {
        "service": SERVICE_NAME,
        "status": "ready" if fb_configured else "degraded",
        "firebase": {
            "configured": fb_configured,
            "project_id": FIREBASE_PROJECT_ID,
            "database_id": FIREBASE_DB_ID,
            "retention_days": RETENTION_DAYS,
        },
        "gemini_api": {
            "ready": gemini_client is not None,
            "model": "gemini-3.6-flash",
        },
    }


@app.get("/api/test", tags=["test"])
def api_test() -> dict[str, str]:
    return {"service": SERVICE_NAME, "status": "ok", "message": "FastAPI is working."}


@app.post("/cleanup", tags=["telemetry"])
def manual_cleanup() -> dict[str, Any]:
    """Explicit trigger to verify and automatically purge expired 90-day data."""
    return verify_and_cleanup_database()


# Summary retrieval endpoints
@app.get("/summaries", tags=["summaries"])
def get_summaries(limit: int = Query(default=25, ge=1, le=100)) -> list[dict[str, Any]]:
    docs = query_firestore_collection("summaries")
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    active = []
    for d in docs:
        exp_epoch = d.get("expires_at_epoch")
        if not exp_epoch or exp_epoch > now_epoch:
            active.append(d)
    active.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
    return active[:limit]


@app.get("/summaries/{summary_id}", tags=["summaries"])
def get_summary(summary_id: str) -> dict[str, Any]:
    docs = query_firestore_collection("summaries")
    for d in docs:
        if str(d.get("id")) == summary_id:
            return d
    raise HTTPException(status_code=404, detail="Summary not found or expired.")


@app.delete("/summaries/{summary_id}", tags=["summaries"])
def delete_summary(summary_id: str) -> dict[str, str]:
    """Automatic immediate deletion from Firebase without confirmation."""
    if delete_from_firestore("summaries", summary_id):
        return {"status": "deleted", "id": summary_id}
    raise HTTPException(status_code=404, detail="Summary not found in Firestore.")


# Generic Dynamic API Endpoints for Future Functions
@app.post("/api/data/{collection}", tags=["dynamic"])
def store_dynamic_data(collection: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Flexible storage endpoint allowing any future features to store arbitrary documents."""
    doc_id = str(payload.get("id") or uuid4())
    now = datetime.now(timezone.utc)
    payload["id"] = doc_id
    if "created_at" not in payload:
        payload["created_at"] = now.isoformat()
    if "expires_at" not in payload:
        payload["expires_at"] = (now + timedelta(days=RETENTION_DAYS)).isoformat()
        payload["expires_at_epoch"] = int((now + timedelta(days=RETENTION_DAYS)).timestamp())

    success = save_to_firestore(collection, doc_id, payload)
    return {"status": "stored" if success else "failed", "id": doc_id, "data": payload}


@app.get("/api/data/{collection}", tags=["dynamic"])
def query_dynamic_data(collection: str, limit: int = Query(default=25, ge=1, le=100)) -> list[dict[str, Any]]:
    """Flexible query endpoint for any collection."""
    return query_firestore_collection(collection)[:limit]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "3000")),
        reload=os.getenv("FASTAPI_RELOAD") == "1",
    )
