"""Data-source adapters for 4_List_renderer.

Each adapter maps some upstream dataset onto the generic result schema that
the stable Outputs/list_results.html template consumes — the core idea of
adaptive display: one template, many sources.
"""

from .biopharm import build_payload_from_biotech_db
from .news import build_payload_from_news

__all__ = ["build_payload_from_biotech_db", "build_payload_from_news"]
