"""Compatibility import for the frontend API client."""

from .interfaces.web_client import GridApiClient, GridApiError

__all__ = ["GridApiClient", "GridApiError"]
