"""4_List_renderer — Google-search-style result list renderer.

The HTML template (Outputs/list_results.html) is stable; only the
list_results_data.js sidecar is regenerated per run from a result payload.
"""

from .render import build_payload, normalize_result, render_sidecar, SIDECAR_NAME

__all__ = [
    "build_payload",
    "normalize_result",
    "render_sidecar",
    "SIDECAR_NAME",
]
