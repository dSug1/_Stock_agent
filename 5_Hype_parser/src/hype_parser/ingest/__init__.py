"""Ingestion clients for the diffusion engine (Wave 1: arXiv, GDELT, Wikipedia).

Each module exposes a fetch function that takes an injectable HTTP callable (so tests run
without network) and returns plain Python structures. No DB writes here — the orchestrator
(scripts/5_radar.py) persists results.
"""
