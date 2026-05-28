"""Auto-trigger for 2_Funds_parser based on 13F filing calendar.

Pure logic lives in :mod:`funds_refresh.decision`; subprocess-based
runner in :mod:`funds_refresh.runner`. The CLI entry point is
``scripts/3_auto_refresh_funds.py``.
"""
