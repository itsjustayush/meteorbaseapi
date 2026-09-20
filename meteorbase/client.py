from __future__ import annotations
import httpx
from typing import Dict, Any, Optional

# --- Custom SDK Exceptions ---
class MeteorBaseError(Exception):
    """Base exception for all MeteorBase SDK errors."""
    pass

class MeteorBaseAPIKeyError(MeteorBaseError):
    """Raised when the provided API key is invalid, missing, or unverified (401 / 403)."""
    pass

class MeteorBaseRateLimitError(MeteorBaseError):
    """Raised when rate limits are exceeded (429)."""
    pass

class MeteorBaseAPIError(MeteorBaseError):
    """Raised for other unexpected API server errors."""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


# --- Resource Namespaces ---
class DatabaseResource:
    def __init__(self, client: Client):
        self._client = client

    def query(self, table: str, select: str = "*", filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Query data from tables via the gateway."""
        response = self._client._request("POST", "/api/db/query", json={
            "table": table,
            "select": select,
            "filters": filters or {}
        })
        return response.json()


class TelemetryResource:
    def __init__(self, client: Client):
        self._client = client

    def get(self) -> Dict[str, Any]:
        """Fetch system telemetry and health metrics."""
        response = self._client._request("GET", "/api/telemetry")
        return response.json()


# --- Main SDK Client ---
class Client:
    # Base URL is permanently fixed as requested
    BASE_URL: str = "https://meteorbase-api.vercel.app"

    def __init__(self, apikey: str):
        self.apikey = apikey
        self._http = httpx.Client(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {self.apikey}",
                "Content-Type": "application/json"
            },
            timeout=30.0
        )
        
        # Attach namespaced resource helpers
        self.db = DatabaseResource(self)
        self.telemetry = TelemetryResource(self)

    def _request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
        """Internal helper to handle errors and map them to clean SDK exceptions."""
        try:
            response = self._http.request(method, endpoint, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            message = exc.response.text
            
            if status in (401, 403):
                raise MeteorBaseAPIKeyError(
                    f"API Key verification failed ({status}): Provided API key is invalid, expired, or unauthorized."
                ) from exc
            elif status == 429:
                raise MeteorBaseRateLimitError(
                    "Rate limit exceeded. Please slow down your requests."
                ) from exc
            else:
                raise MeteorBaseAPIError(
                    f"API error ({status}): {message}", status_code=status
                ) from exc
        except httpx.RequestError as exc:
            raise MeteorBaseError(f"Network error communicating with MeteorBase: {exc}") from exc

    def about(self) -> Dict[str, Any]:
        """
        Returns authenticated client session info: fetches the user's account details 
        (name, email, etc.) from the database linked to this API key via Firebase/Oauth.
        """
        response = self._request("GET", "/api/auth/me")
        return response.json()

    def close(self):
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def about() -> Dict[str, Any]:
    """
    Dedicated global function for the whole package: fetches public metadata, 
    version, author info, and general system details from the gateway root.
    """
    with httpx.Client() as client:
        response = client.get("https://meteorbase-api.vercel.app/")
        response.raise_for_status()
        return response.json()