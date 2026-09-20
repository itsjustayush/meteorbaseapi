"""Compatibility entry point for deployments that still use ``app:app``."""

from meteorbase.app import app

__all__ = ["app"]
