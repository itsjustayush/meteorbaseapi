from __future__ import annotations

# Expose the SDK Client and global helper function
from .client import Client, about

# Expose the FastAPI app instance for ASGI servers / Vercel
from .app import app

__version__ = "0.2.0"
__all__ = ["Client", "about", "app"]