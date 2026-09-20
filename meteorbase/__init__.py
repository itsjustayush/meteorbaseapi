from __future__ import annotations

# Expose the SDK client and helper functions
from .client import Client, about

# Expose the FastAPI app instance for ASGI servers / Vercel
from .app import app

__version__ = "0.1.0"
__all__ = ["Client", "about", "app"]