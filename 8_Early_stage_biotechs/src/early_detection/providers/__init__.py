"""Universe providers — each yields ``Listing`` records (pre-reconciliation), fail-soft & independent.

A provider failure logs + returns [] so one dead source never blocks the others (spec §6).
"""
