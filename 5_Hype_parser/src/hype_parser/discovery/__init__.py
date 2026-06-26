"""Theme discovery — jury-convergence model (decisions D22-D26).

Discovers nascent themes from the *convergence of independent expert juries* (early-stage award
shortlists, breakthrough designations, agency priority calls, specialist-fund new positions) rather
than from document volume. Tiny footprint, zero Claude in the hot path. See
``spec/discovery_spec_v0.2.md`` and ``spec/discovery_explained.md``.

Pipeline:  parse juries -> jury_signals (signals.py)
           -> embed + cluster + score + promote (convergence.py)
           -> classify constituents listed/private (resolve.py)
           -> specialist-fund smart-money confirmation (funds.py)
"""
